"""
test_revision_merge.py — moving a job forward onto a newer file.

Three things were missing from "the contractor sent rev 3":

  The LOGIC was invisible. The diff compared eleven activity fields and no
  ties at all, so a date that moved because a predecessor was added looked
  exactly like one somebody typed. You could pull the new date across and
  still be working to the old network.

  Applying a date THREW IT AWAY. The recompute after an apply ran with P6's
  default of apply_dates=ON, which overwrites planned_start / planned_finish
  from the target's own logic — so pulling a revised date onto any activity
  with a predecessor put the old one straight back, said "Applied 2 fields",
  and left the row still reading as different.

  There was no way to say "this job, updated". Every upload opened a second
  project, which is right for comparing two revs side by side and wrong when
  the intent was to move forward: the brain stayed attached to a schedule
  nobody was looking at, and the switcher filled up with revisions.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.compare import (apply_activity_changes, apply_relation_changes,
                            compare_projects)
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _job(rels=(), n=3, start="2026-02-02"):
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="w", name="Data Hall", code="DH", parent_uid="root")]
    p.activities = [
        Activity(uid=f"u{i}", activity_id=f"A{i}0", name=f"Step {i}", wbs_uid="w",
                 calendar_uid="1", planned_duration=40, remaining_duration=40,
                 planned_start=start, planned_finish="2026-02-06")
        for i in range(1, n + 1)]
    p.relations = [
        Relation(uid=f"r{k}", predecessor_uid=f"u{a}", successor_uid=f"u{b}",
                 type=t, lag=lag)
        for k, (a, b, t, lag) in enumerate(rels)]
    p.build_lookups()
    return p


FS = "Finish to Start"
SS = "Start to Start"


# ── the logic is in the diff ─────────────────────────────────────────────────

def test_a_tie_the_new_revision_added_is_reported():
    d = compare_projects(_job([(1, 2, FS, 0)]), _job([(1, 2, FS, 0), (2, 3, FS, 0)]))
    assert d["summary"]["logic_added"] == 1
    rows = [r for s in d["sections"] for r in s["logic_added"]]
    assert (rows[0]["pred"], rows[0]["succ"]) == ("A20", "A30")


def test_a_tie_the_new_revision_cut_is_reported():
    d = compare_projects(_job([(1, 2, FS, 0), (2, 3, FS, 0)]), _job([(1, 2, FS, 0)]))
    assert d["summary"]["logic_removed"] == 1


def test_a_retyped_or_relagged_tie_is_reported_with_what_it_was():
    """"Changed" is useless without the old value — the whole question is
    what moved."""
    d = compare_projects(_job([(1, 2, FS, 0)]), _job([(1, 2, SS, 16)]))
    assert d["summary"]["logic_changed"] == 1
    row = [r for s in d["sections"] for r in s["logic_changed"]][0]
    assert row["from_type"] == FS and row["type"] == SS
    assert row["lag"] == "+2d"


def test_identical_logic_reports_nothing():
    d = compare_projects(_job([(1, 2, FS, 0)]), _job([(1, 2, FS, 0)]))
    s = d["summary"]
    assert (s["logic_added"], s["logic_removed"], s["logic_changed"]) == (0, 0, 0)


def test_ties_are_matched_on_activity_ids_not_uids():
    """Uids are minted per file, so the same tie in two exports of one job has
    two of them. Matching on uid would call every tie in every revision new."""
    a = _job([(1, 2, FS, 0)])
    b = _job([(1, 2, FS, 0)])
    for i, r in enumerate(b.relations):
        r.uid = f"totally-different-{i}"
    for i, act in enumerate(b.activities):
        act.uid = f"different-{i}"
    for r in b.relations:
        r.predecessor_uid, r.successor_uid = "different-0", "different-1"
    b.build_lookups()
    assert compare_projects(a, b)["summary"]["logic_added"] == 0


def test_a_logic_change_lands_in_the_successors_folder():
    """A tie belongs to the activity it constrains — which is how P6's own
    Predecessors tab reads it: you open the work that is waiting."""
    b = _job([(1, 2, FS, 0)])
    b.wbs_nodes.append(WBSNode(uid="w2", name="Gen Yard", code="GY", parent_uid="root"))
    b.activities[1].wbs_uid = "w2"
    b.build_lookups()
    d = compare_projects(_job(), b)
    sec = [s for s in d["sections"] if s["logic_added"]][0]
    assert sec["wbs_name"] == "Gen Yard"


def test_a_section_with_only_logic_changes_is_still_shown():
    """It used to be filtered out for having no activity rows, so a revision
    that only moved logic looked like no revision at all."""
    d = compare_projects(_job(), _job([(1, 2, FS, 0)]))
    assert any(s["logic_added"] for s in d["sections"])


# ── pulling the logic across ─────────────────────────────────────────────────

def test_an_added_tie_can_be_pulled_into_the_schedule_you_keep():
    a, b = _job(), _job([(1, 2, FS, 0)])
    apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "add"}])
    assert len(a.relations) == 1
    assert compare_projects(a, b)["summary"]["logic_added"] == 0


def test_a_cut_tie_can_be_pulled_across_too():
    a, b = _job([(1, 2, FS, 0)]), _job()
    apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "remove"}])
    assert a.relations == []


def test_a_retyped_tie_takes_the_new_type_and_lag():
    a, b = _job([(1, 2, FS, 0)]), _job([(1, 2, SS, 16)])
    apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "update"}])
    assert (a.relations[0].type, a.relations[0].lag) == (SS, 16)


def test_nothing_is_invented_when_the_source_has_no_such_tie():
    a, b = _job(), _job()
    _, _, det = apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "add"}])
    assert a.relations == []
    assert det["skipped"][0]["why"].startswith("the other schedule has no")


def test_a_tie_naming_an_activity_this_schedule_lacks_is_refused():
    """It would load and then fail to schedule — a relation pointing at
    nothing."""
    a, b = _job(n=2), _job(n=3, rels=[(1, 3, FS, 0)])
    _, _, det = apply_relation_changes(b, a, [{"pred": "A10", "succ": "A30", "op": "add"}])
    assert a.relations == []
    assert "A30 is not in this schedule" in det["skipped"][0]["why"]


def test_an_activity_cannot_be_made_to_precede_itself():
    a = _job()
    b = _job()
    b.relations = [Relation(uid="x", predecessor_uid="u1", successor_uid="u1")]
    b.build_lookups()
    _, _, det = apply_relation_changes(b, a, [{"pred": "A10", "succ": "A10", "op": "add"}])
    assert a.relations == []
    assert "precede itself" in det["skipped"][0]["why"]


def test_removing_a_tie_that_is_not_there_says_so_rather_than_failing():
    a, b = _job(), _job()
    ok, _, det = apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "remove"}])
    assert ok and det["removed"] == 0 and det["skipped"]


def test_applying_the_whole_logic_diff_makes_the_networks_agree():
    """The point of the feature, end to end."""
    a = _job([(1, 2, FS, 0), (2, 3, FS, 0)])
    b = _job([(1, 2, SS, 8), (1, 3, FS, 0)])
    d = compare_projects(a, b)
    changes = []
    for s in d["sections"]:
        changes += [{"pred": r["pred"], "succ": r["succ"], "op": "add"} for r in s["logic_added"]]
        changes += [{"pred": r["pred"], "succ": r["succ"], "op": "update"} for r in s["logic_changed"]]
        changes += [{"pred": r["pred"], "succ": r["succ"], "op": "remove"} for r in s["logic_removed"]]
    apply_relation_changes(b, a, changes)
    after = compare_projects(a, b)["summary"]
    assert (after["logic_added"], after["logic_removed"], after["logic_changed"]) == (0, 0, 0)


def test_pulling_logic_across_does_not_reschedule():
    """Changing the network changes the float. Only Schedule (F9) moves the
    dates — everything else leaves them where they are, so the drift is
    visible instead of silently absorbed."""
    a, b = _job(), _job([(1, 2, FS, 0)])
    before = [(x.planned_start, x.planned_finish) for x in a.activities]
    apply_relation_changes(b, a, [{"pred": "A10", "succ": "A20", "op": "add"}])
    assert [(x.planned_start, x.planned_finish) for x in a.activities] == before
    assert a.activities[0].total_float is not None, "float was not recomputed"


# ── applying a field keeps the value it was given ────────────────────────────

def test_a_pulled_date_survives_the_recompute():
    """The recompute used to run with apply_dates ON and overwrite the date it
    had just been asked to pull in, for any activity with a predecessor."""
    a = _job([(1, 2, FS, 0)])
    b = _job([(1, 2, FS, 0)])
    b.activities[1].planned_start = "2026-03-09"
    b.activities[1].planned_finish = "2026-03-13"
    apply_activity_changes(b, a, [{"activity_id": "A20",
                                   "attrs": ["planned_start", "planned_finish"]}])
    assert a.activities[1].planned_start == "2026-03-09"
    assert a.activities[1].planned_finish == "2026-03-13"


def test_applying_a_field_still_refreshes_float():
    a = _job([(1, 2, FS, 0)])
    b = _job([(1, 2, FS, 0)])
    b.activities[1].planned_duration = 80
    apply_activity_changes(b, a, [{"activity_id": "A20", "attrs": ["planned_duration"]}])
    assert a.activities[1].total_float is not None


# ── revising in place ────────────────────────────────────────────────────────

def _xml_of(project, tmp_path, name="rev.xml"):
    from engine.xml_writer import write_p6_xml
    path = str(tmp_path / name)
    write_p6_xml(project, path)
    return path


def _load_one(pid="old", acts=2):
    import server
    server._projects.clear()
    server._projects[pid] = server._make_session(pid, "rev1.xml")
    server._projects[pid]["project"] = _job(n=acts)
    server._active_id[0] = pid
    return server


def test_revising_keeps_the_same_project_rather_than_opening_another(tmp_path):
    server = _load_one()
    path = _xml_of(_job(n=5), tmp_path)
    with open(path, "rb") as f:
        d = server.app.test_client().post(
            "/api/revise", data={"project_id": "old", "file": (f, "rev2.xml")},
            content_type="multipart/form-data").get_json()
    assert d["success"] and d["project_id"] == "old"
    assert list(server._projects) == ["old"], "a second project was opened"
    assert d["activity_count"] == 5


def test_revising_says_what_moved(tmp_path):
    server = _load_one(acts=2)
    with open(_xml_of(_job(n=4), tmp_path), "rb") as f:
        d = server.app.test_client().post(
            "/api/revise", data={"file": (f, "rev2.xml")},
            content_type="multipart/form-data").get_json()
    assert d["diff"]["added"] == 2
    assert d["diff"]["removed"] == 0


def test_revising_is_one_undo_away(tmp_path):
    """The new file wins outright, so the way back has to be cheap."""
    server = _load_one()
    with open(_xml_of(_job(n=5), tmp_path), "rb") as f:
        d = server.app.test_client().post(
            "/api/revise", data={"file": (f, "rev2.xml")},
            content_type="multipart/form-data").get_json()
    assert d["undo_count"] >= 1
    assert len(server._projects["old"]["undo_stack"]) >= 1


def test_revising_updates_the_name_the_exports_are_built_from(tmp_path):
    server = _load_one()
    with open(_xml_of(_job(n=3), tmp_path), "rb") as f:
        server.app.test_client().post(
            "/api/revise", data={"file": (f, "MDC1_rev7.xml")},
            content_type="multipart/form-data")
    assert server._projects["old"]["source_name"] == "MDC1_rev7.xml"


def test_revising_with_nothing_loaded_says_so():
    import server
    server._projects.clear()
    server._active_id[0] = None
    r = server.app.test_client().post(
        "/api/revise", data={"file": (open(__file__, "rb"), "x.xml")},
        content_type="multipart/form-data")
    assert r.status_code == 400


def test_revising_refuses_a_file_that_is_not_a_schedule(tmp_path):
    server = _load_one()
    r = server.app.test_client().post(
        "/api/revise", data={"file": (open(__file__, "rb"), "notes.txt")},
        content_type="multipart/form-data")
    assert r.status_code == 400
    assert "Unsupported file type" in r.get_json()["error"]


def test_a_failed_revise_leaves_the_old_schedule_alone(tmp_path):
    server = _load_one()
    before = server._projects["old"]["project"]
    server.app.test_client().post(
        "/api/revise", data={"file": (open(__file__, "rb"), "notes.txt")},
        content_type="multipart/form-data")
    assert server._projects["old"]["project"] is before


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_apply_relations_endpoint_pulls_a_tie_across():
    import server
    server._projects.clear()
    for pid, proj in (("a", _job()), ("b", _job([(1, 2, FS, 0)]))):
        server._projects[pid] = server._make_session(pid, f"{pid}.xml")
        server._projects[pid]["project"] = proj
    server._active_id[0] = "a"
    d = server.app.test_client().post("/api/apply-relations", json={
        "source_project_id": "b", "target_project_id": "a",
        "changes": [{"pred": "A10", "succ": "A20", "op": "add"}]}).get_json()
    assert d["success"] and d["relation_count"] == 1
    assert d["undo_count"] >= 1


def test_the_apply_relations_endpoint_reports_what_it_would_not_do():
    import server
    server._projects.clear()
    for pid in ("a", "b"):
        server._projects[pid] = server._make_session(pid, f"{pid}.xml")
        server._projects[pid]["project"] = _job()
    server._active_id[0] = "a"
    d = server.app.test_client().post("/api/apply-relations", json={
        "source_project_id": "b",
        "changes": [{"pred": "A10", "succ": "A20", "op": "add"}]}).get_json()
    assert d["detail"]["skipped"], "a skip with no explanation is a silent failure"
