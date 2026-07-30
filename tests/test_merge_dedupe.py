"""
test_merge_dedupe.py — Duplicate detection when merging pasted/imported
activities into an existing schedule.

The scoping rule that matters: a name collision only counts within the SAME
destination WBS folder. "Terminate wire" existing in ER 209 must never block
or interfere with a "Terminate wire" landing in ER 210 — the same name in a
different folder is not a conflict, only a genuine same-folder collision is.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation
from engine.importer import build_project_from_contract
from engine.paste_parser import contract_from_paste
import server


def _base():
    p = Project(uid="1", name="Base", id="BASE")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="er209", name="ER 209", code="ER209"),
        WBSNode(uid="er210", name="ER 210", code="ER210"),
    ]
    p.activities = [
        Activity(uid="e1", activity_id="A1000", name="Terminate wire", wbs_uid="er209",
                 calendar_uid="1", planned_duration=40.0, status="Not Started"),
        Activity(uid="e2", activity_id="A1010", name="Pull cable", wbs_uid="er209",
                 calendar_uid="1", planned_duration=24.0),
        Activity(uid="e3", activity_id="A2000", name="Terminate wire", wbs_uid="er210",
                 calendar_uid="1", planned_duration=40.0),
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="e2", successor_uid="e1")]
    p.build_lookups()
    return p


def _incoming(dur=80.0, status="Completed", second_activity=True):
    p = Project(uid="2", name="Paste", id="PASTE")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = []
    acts = [Activity(uid="i1", activity_id="B1000", name="Terminate wire", wbs_uid="w",
                     calendar_uid="1", planned_duration=dur, status=status,
                     percent_complete=100.0 if status == "Completed" else 0.0)]
    if second_activity:
        acts.append(Activity(uid="i2", activity_id="B1010", name="Brand new activity",
                             wbs_uid="w", calendar_uid="1", planned_duration=16.0))
        p.relations = [Relation(uid="ri", predecessor_uid="i1", successor_uid="i2")]
    p.activities = acts
    p.build_lookups()
    return p


# ── Core scoping behaviour ───────────────────────────────────────────────────

def test_same_name_different_wbs_is_not_a_conflict():
    base = _base()
    inc = _incoming()
    for a in inc.activities:
        a.wbs_uid = "er210"   # target the OTHER folder that already has "Terminate wire"
    report = server._merge_projects(base, inc, target_wbs_uid="er210", dedupe="skip")
    # er210 already had a "Terminate wire" (e3) — so THIS is a same-folder conflict
    assert report["skipped_duplicate"] == 1
    # but the one in er209 must be completely unaffected
    assert base.get_activity(activity_id="A1000").planned_duration == 40.0


def test_skip_keeps_existing_and_adds_the_rest():
    base = _base()
    inc = _incoming()
    for a in inc.activities:
        a.wbs_uid = "er209"
    report = server._merge_projects(base, inc, target_wbs_uid="er209", dedupe="skip")
    assert report["skipped_duplicate"] == 1 and report["added"] == 1
    assert base.get_activity(activity_id="A1000").planned_duration == 40.0
    assert any(a.name == "Brand new activity" for a in base.activities)


def test_replace_overwrites_data_but_keeps_original_identity():
    base = _base()
    inc = _incoming(dur=999.0)
    for a in inc.activities:
        a.wbs_uid = "er209"
    report = server._merge_projects(base, inc, target_wbs_uid="er209", dedupe="replace")
    assert report["replaced"] == 1
    kept = base.get_activity(activity_id="A1000")
    assert kept.uid == "e1"                    # identity preserved — relations still resolve
    assert kept.planned_duration == 999.0
    assert kept.status == "Completed"


def test_relations_follow_the_kept_activity_not_the_discarded_one():
    """An incoming relation touching the deduped activity must land on the
    KEPT uid — otherwise pasted logic silently dangles."""
    base = _base()
    inc = _incoming(dur=999.0)
    for a in inc.activities:
        a.wbs_uid = "er209"
    server._merge_projects(base, inc, target_wbs_uid="er209", dedupe="replace")
    kept = base.get_activity(activity_id="A1000")
    new_act = next(a for a in base.activities if a.name == "Brand new activity")
    assert any(r.predecessor_uid == kept.uid and r.successor_uid == new_act.uid
              for r in base.relations)


def test_off_mode_preserves_old_always_add_behaviour():
    base = _base()
    inc = _incoming()
    for a in inc.activities:
        a.wbs_uid = "er209"
    report = server._merge_projects(base, inc, target_wbs_uid="er209", dedupe=None)
    assert report["added"] == 2 and report["skipped_duplicate"] == 0
    dupes = [a for a in base.activities if a.name == "Terminate wire" and a.wbs_uid == "er209"]
    assert len(dupes) == 2


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_repeated_identical_paste_does_not_pile_up():
    base = _base()
    first = _incoming()
    for a in first.activities:
        a.wbs_uid = "er209"
    server._merge_projects(base, first, target_wbs_uid="er209", dedupe="skip")
    act_count, rel_count = len(base.activities), len(base.relations)

    second = _incoming()
    for a in second.activities:
        a.wbs_uid = "er209"
    server._merge_projects(base, second, target_wbs_uid="er209", dedupe="skip")
    assert len(base.activities) == act_count
    assert len(base.relations) == rel_count


# ── Within-batch duplicates (the paste itself repeats a name) ────────────────

def test_duplicate_within_the_same_paste_is_caught_too():
    base = _base()
    inc = Project(uid="2", name="P", id="P")
    inc.calendars = [Calendar(uid="1", name="S")]
    inc.wbs_nodes = []
    inc.activities = [
        Activity(uid="i1", activity_id="B1000", name="Terminate wire", wbs_uid="w",
                 calendar_uid="1", planned_duration=111.0),
        Activity(uid="i2", activity_id="B1010", name="Terminate wire", wbs_uid="w",
                 calendar_uid="1", planned_duration=222.0),
    ]
    inc.build_lookups()
    report = server._merge_projects(base, inc, target_wbs_uid="er209",
                                    flatten=True, dedupe="skip")
    assert report["skipped_duplicate"] == 2
    assert len([a for a in base.activities if a.name == "Terminate wire"
               and a.wbs_uid == "er209"]) == 1


# ── Placement fix: a plain no-header paste must land directly in target ─────

def test_plain_paste_with_no_headers_lands_directly_in_the_chosen_folder():
    """Without this, a no-header paste got wrapped in a synthetic sub-folder
    nested under the target, so dedupe could never see what was already
    there — the most common paste shape would appear to silently not work."""
    base = _base()
    contract = contract_from_paste(
        "A5000  Terminate wire  40  01-Jun-26  05-Jun-26\n"
        "A5010  Genuinely new task  8  01-Jun-26  02-Jun-26"
    )
    inc = build_project_from_contract(contract, project_id="X")
    report = server._merge_projects(base, inc, target_wbs_uid="er209",
                                    flatten=False, dedupe="skip")
    assert len(base.wbs_nodes) == 2, "no spurious folder should be created"
    new_task = next(a for a in base.activities if a.name == "Genuinely new task")
    assert new_task.wbs_uid == "er209"
    assert report["skipped_duplicate"] == 1


def test_one_genuine_named_section_still_gets_its_own_folder():
    """The placement fix must not flatten away a REAL single-section paste —
    only the synthetic no-header placeholder."""
    base = _base()
    contract = contract_from_paste(
        "Funding\n  A6000  Some funding task  10  01-Jun-26  10-Jun-26"
    )
    inc = build_project_from_contract(contract, project_id="Y")
    server._merge_projects(base, inc, target_wbs_uid="er209",
                           flatten=False, dedupe="skip")
    funding = [w for w in base.wbs_nodes if w.name == "Funding"]
    assert len(funding) == 1
    assert funding[0].parent_uid == "er209"
    new_act = next(a for a in base.activities if a.name == "Some funding task")
    assert new_act.wbs_uid == funding[0].uid


# ── HTTP endpoint ─────────────────────────────────────────────────────────────

def test_commit_endpoint_reports_dedupe_and_rejects_bad_value():
    p = _base()
    server._projects.clear()
    server._projects["base"] = {**server._make_session("base", "b.xml"), "project": p}
    server._active_id[0] = "base"
    c = server.app.test_client()

    contract = contract_from_paste("A9000  Terminate wire  40  01-Jun-26  05-Jun-26")
    r = c.post("/api/import/commit", json={
        "contract": contract, "mode": "merge",
        "target_wbs_code": "ER209", "dedupe": "skip",
    })
    j = r.get_json()
    assert r.status_code == 200
    assert j["merge_report"]["skipped_duplicate"] == 1

    r2 = c.post("/api/import/commit", json={
        "contract": contract, "mode": "merge",
        "target_wbs_code": "ER209", "dedupe": "not-a-real-value",
    })
    assert r2.status_code == 400
