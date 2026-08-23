"""
test_id_normalizer.py — put stray activity codes back on the job's pattern.

The real file this was built for is coded MDC1.MIL.#### for milestones and
MDC1.FDG.#### in foundations, but months of added rows carry generic A1000
codes instead, so the schedule now runs two coding systems at once.

What matters here is not that ids change — it is that the RIGHT ones change,
to the right prefix for the folder they sit in, without ever colliding, and
that a schedule with no single convention is refused rather than guessed at.
Renaming is only safe because relations bind by uid; a test holds that line
too, since the day it stops being true this feature quietly destroys logic.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import id_normalizer
from engine.edit_engine import apply_command
from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation)


def _project():
    """Foundations and milestones coded MDC1.*, with generic rows mixed in."""
    p = Project(uid="1", name="DC", id="25-1539-INT-1", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [
        WBSNode(uid="fdg", name="Foundations", code="FDG"),
        WBSNode(uid="mil", name="Milestones", code="MIL"),
    ]

    def a(uid, aid, name, wbs):
        return Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                        calendar_uid="1", planned_duration=40.0,
                        remaining_duration=40.0, planned_start="2026-01-05",
                        planned_finish="2026-01-09")

    p.activities = [
        a("u1", "MDC1.FDG.1290", "Excavate footings", "fdg"),
        a("u2", "MDC1.FDG.1300", "Rebar footings", "fdg"),
        a("u3", "MDC1.FDG.1310", "Pour footings", "fdg"),
        a("u4", "A1000", "Backfill footings", "fdg"),          # drifted
        a("u5", "MDC1.MIL.1130", "Foundations complete", "mil"),
        a("u6", "MDC1.MIL.1140", "Steel complete", "mil"),
        a("u7", "A1010", "Envelope complete", "mil"),          # drifted
    ]
    p.build_lookups()
    return p


# ── reading the convention out of the file ───────────────────────────────────

def test_the_pattern_is_read_from_the_ids_not_configured():
    rep = id_normalizer.plan(_project())
    assert rep["convention"] == "MDC1"


def test_only_the_drifted_rows_are_touched():
    rep = id_normalizer.plan(_project())
    assert {c["from"] for c in rep["changes"]} == {"A1000", "A1010"}


def test_each_row_takes_its_own_folders_prefix():
    """A1000 sits in foundations and A1010 in milestones — they must not both
    get whichever prefix happened to be most common overall."""
    rep = id_normalizer.plan(_project())
    to = {c["from"]: c["to"] for c in rep["changes"]}
    assert to["A1000"].startswith("MDC1.FDG.")
    assert to["A1010"].startswith("MDC1.MIL.")


def test_the_number_the_row_already_carried_is_kept_when_it_is_free():
    rep = id_normalizer.plan(_project())
    to = {c["from"]: c["to"] for c in rep["changes"]}
    assert to["A1000"] == "MDC1.FDG.1000"
    assert to["A1010"] == "MDC1.MIL.1010"


def test_a_taken_number_falls_through_to_the_next_free_slot():
    p = _project()
    p.get_activity(activity_id="A1000").activity_id = "A1290"   # collides
    p.build_lookups()
    rep = id_normalizer.plan(p)
    to = {c["from"]: c["to"] for c in rep["changes"]}
    assert to["A1290"] != "MDC1.FDG.1290"        # that one is occupied
    assert to["A1290"] == "MDC1.FDG.1320"        # max 1310 + the folder's stride


def test_the_folders_own_numbering_stride_is_followed():
    """This job steps by 10; a folder stepping by 100 must not get 10."""
    p = _project()
    for aid, num in [("MDC1.FDG.1290", 1000), ("MDC1.FDG.1300", 1100),
                     ("MDC1.FDG.1310", 1200)]:
        p.get_activity(activity_id=aid).activity_id = f"MDC1.FDG.{num}"
    p.get_activity(activity_id="A1000").activity_id = "A1200"    # collides
    p.build_lookups()
    to = {c["from"]: c["to"] for c in id_normalizer.plan(p)["changes"]}
    assert to["A1200"] == "MDC1.FDG.1300"        # 1200 + stride of 100


def test_the_digit_width_of_the_folder_is_kept():
    p = _project()
    for i, aid in enumerate(["MDC1.FDG.1290", "MDC1.FDG.1300", "MDC1.FDG.1310"]):
        p.get_activity(activity_id=aid).activity_id = f"MDC1.FDG.{100 + i * 10:06d}"
    p.build_lookups()
    to = {c["from"]: c["to"] for c in id_normalizer.plan(p)["changes"]}
    assert to["A1000"] == "MDC1.FDG.001000"


# ── folders with nothing to follow ───────────────────────────────────────────

def test_a_new_subfolder_inherits_the_prefix_from_its_parent_branch():
    """Where generic ids actually collect: a folder added later, holding
    nothing but drifted rows, so it has no convention of its own."""
    p = _project()
    p.wbs_nodes.append(WBSNode(uid="fdg2", name="Piers", code="P", parent_uid="fdg"))
    p.activities.append(Activity(
        uid="u8", activity_id="A1020", name="Drill piers", wbs_uid="fdg2",
        calendar_uid="1", planned_duration=40.0, remaining_duration=40.0,
        planned_start="2026-01-05", planned_finish="2026-01-09"))
    p.build_lookups()
    to = {c["from"]: c["to"] for c in id_normalizer.plan(p)["changes"]}
    assert to["A1020"].startswith("MDC1.FDG.")


def test_a_folder_with_no_coded_row_anywhere_above_it_is_reported_not_guessed():
    p = Project(uid="1", name="X", id="X", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Loose", code="L")]
    p.activities = [Activity(uid="u1", activity_id="ODD-CODE", name="n", wbs_uid="w",
                             calendar_uid="1", planned_duration=8.0,
                             remaining_duration=8.0)]
    p.build_lookups()
    rep = id_normalizer.plan(p)
    assert rep["changes"] == []
    assert rep["skipped"]


# ── refusing to guess ────────────────────────────────────────────────────────

def test_a_schedule_with_no_single_convention_is_refused():
    """Half MDC1, half XYZ is a decision for the user — normalizing would be
    picking a winner, not following one."""
    p = _project()
    for uid, aid in [("u1", "XYZ.100"), ("u2", "XYZ.110"), ("u3", "XYZ.120")]:
        p.get_activity(uid=uid).activity_id = aid
    p.build_lookups()
    rep = id_normalizer.plan(p)
    assert rep["changes"] == []
    assert "single convention" in " ".join(rep["skipped"])


def test_an_id_with_no_trailing_number_is_left_alone_not_mangled():
    p = _project()
    p.get_activity(uid="u4").activity_id = "BACKFILL"
    p.build_lookups()
    rep = id_normalizer.plan(p)
    assert "BACKFILL" not in {c["from"] for c in rep["changes"]}
    assert any(x["activity_id"] == "BACKFILL" for x in rep["left_alone"])


def test_an_already_clean_schedule_proposes_nothing():
    p = _project()
    p.get_activity(activity_id="A1000").activity_id = "MDC1.FDG.1320"
    p.get_activity(activity_id="A1010").activity_id = "MDC1.MIL.1150"
    p.build_lookups()
    assert id_normalizer.plan(p)["changes"] == []


def test_running_it_twice_changes_nothing_the_second_time():
    p = _project()
    apply_command(p, {"action": "normalize_activity_ids"})
    assert id_normalizer.plan(p)["changes"] == []


# ── never collide ────────────────────────────────────────────────────────────

def test_two_drifted_rows_never_land_on_the_same_code():
    p = _project()
    p.get_activity(uid="u7").wbs_uid = "fdg"        # both drifted rows, one folder
    p.get_activity(uid="u7").activity_id = "A1000x"
    p.get_activity(uid="u7").activity_id = "B1000"  # same number, different prefix
    p.build_lookups()
    rep = id_normalizer.plan(p)
    news = [c["to"] for c in rep["changes"]]
    assert len(news) == len(set(news)), "two rows were given the same id"


def test_every_id_in_the_file_is_still_unique_after_applying():
    p = _project()
    apply_command(p, {"action": "normalize_activity_ids"})
    ids = [a.activity_id for a in p.activities]
    assert len(ids) == len(set(ids))


def test_validate_catches_a_rename_onto_an_untouched_row():
    p = _project()
    problems = id_normalizer.validate(p, [{"uid": "u4", "to": "MDC1.FDG.1290"}])
    assert problems and "already used" in problems[0]


def test_validate_catches_two_renames_wanting_one_code():
    p = _project()
    problems = id_normalizer.validate(p, [{"uid": "u4", "to": "MDC1.FDG.9000"},
                                          {"uid": "u7", "to": "MDC1.FDG.9000"}])
    assert problems and "both become" in problems[0]


def test_a_stale_uid_is_refused_rather_than_silently_skipped():
    p = _project()
    assert id_normalizer.validate(p, [{"uid": "gone", "to": "MDC1.FDG.9000"}])


# ── the line this whole feature rests on ─────────────────────────────────────

def test_renaming_ids_does_not_disturb_the_logic():
    """Relations bind by uid, which is the only reason renaming codes in bulk
    is safe at all. If that ever changes, this feature destroys the network."""
    p = _project()
    p.relations = [Relation(uid="r1", predecessor_uid="u3", successor_uid="u4",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    apply_command(p, {"action": "normalize_activity_ids"})
    assert len(p.relations) == 1
    rel = p.relations[0]
    assert rel.predecessor_uid == "u3" and rel.successor_uid == "u4"
    assert p.get_activity(uid="u4").activity_id.startswith("MDC1.FDG.")


# ── scoping and preview ──────────────────────────────────────────────────────

def test_a_scope_limits_which_rows_are_proposed():
    rep = id_normalizer.plan(_project(), root_uid="mil")
    assert {c["from"] for c in rep["changes"]} == {"A1010"}


def test_preview_writes_nothing():
    p = _project()
    before = [(a.uid, a.activity_id) for a in p.activities]
    ok, msg = apply_command(p, {"action": "normalize_activity_ids", "preview": True})
    assert ok and "would be renamed" in msg
    assert [(a.uid, a.activity_id) for a in p.activities] == before


def test_preview_and_apply_move_the_same_rows():
    preview_rep = id_normalizer.plan(_project())
    p = _project()
    apply_command(p, {"action": "normalize_activity_ids"})
    for c in preview_rep["changes"]:
        assert p.get_activity(uid=c["uid"]).activity_id == c["to"]


def test_applying_an_exact_previewed_list_lands_exactly_that():
    """What the user approved is what gets written — not a re-computation."""
    p = _project()
    ok, _ = apply_command(p, {"action": "normalize_activity_ids",
                              "changes": [{"uid": "u4", "to": "MDC1.FDG.7777"}]})
    assert ok
    assert p.get_activity(uid="u4").activity_id == "MDC1.FDG.7777"
    assert p.get_activity(uid="u7").activity_id == "A1010", "untouched row moved"


def test_a_bad_explicit_list_is_refused_before_anything_is_written():
    p = _project()
    ok, msg = apply_command(p, {"action": "normalize_activity_ids",
                                "changes": [{"uid": "u4", "to": "MDC1.FDG.1290"}]})
    assert not ok and "already used" in msg
    assert p.get_activity(uid="u4").activity_id == "A1000"


# ── through the route the button actually calls ──────────────────────────────

def _client():
    import server
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def test_the_preview_route_returns_a_reviewable_list():
    c = _client()
    body = c.post("/api/ids/normalize", json={"preview": True}).get_json()
    assert body["convention"] == "MDC1"
    assert {x["from"] for x in body["changes"]} == {"A1000", "A1010"}
    assert all(x.get("uid") and x.get("to") for x in body["changes"])


def test_the_preview_route_writes_nothing():
    import server
    c = _client()
    c.post("/api/ids/normalize", json={"preview": True})
    ids = {a.activity_id for a in server._projects["t"]["project"].activities}
    assert "A1000" in ids and "A1010" in ids


def test_applying_writes_exactly_what_the_preview_offered():
    import server
    c = _client()
    changes = c.post("/api/ids/normalize", json={"preview": True}).get_json()["changes"]
    body = c.post("/api/ids/normalize", json={"changes": changes}).get_json()
    assert body.get("success")
    proj = server._projects["t"]["project"]
    for ch in changes:
        assert proj.get_activity(uid=ch["uid"]).activity_id == ch["to"]


def test_applying_is_undoable_like_any_other_edit():
    import server
    c = _client()
    changes = c.post("/api/ids/normalize", json={"preview": True}).get_json()["changes"]
    c.post("/api/ids/normalize", json={"changes": changes})
    c.post("/api/undo")
    proj = server._projects["t"]["project"]
    assert proj.get_activity(activity_id="A1000") is not None


def test_applying_nothing_is_refused_rather_than_reported_as_success():
    c = _client()
    resp = c.post("/api/ids/normalize", json={"changes": []})
    assert resp.status_code == 400


def test_an_unknown_folder_scope_is_refused():
    c = _client()
    resp = c.post("/api/ids/normalize", json={"preview": True, "wbs_uid": "nope"})
    assert resp.status_code == 400
