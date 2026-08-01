"""
test_wbs_reorder.py — Moving WBS folders up/down among their siblings, and
renaming a folder by uid.

The reorder has one non-obvious requirement: imported files give every folder
the same sequence_num, so swapping two equal numbers is a no-op. These tests
pin the renumber-then-swap behaviour, and that a reorder never leaks across
parents.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, WBSNode, Calendar
from engine.edit_engine import apply_command


def _tree():
    """Two root folders; three children under the first. All sequence_num 0,
    exactly as a freshly imported schedule arrives."""
    p = Project(uid="1", name="p", id="P", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="1",  name="Area A", code="AA", sequence_num=0),
        WBSNode(uid="2",  name="Area B", code="AB", sequence_num=0),
        WBSNode(uid="10", name="Sub1", code="S1", parent_uid="1", sequence_num=0),
        WBSNode(uid="11", name="Sub2", code="S2", parent_uid="1", sequence_num=0),
        WBSNode(uid="12", name="Sub3", code="S3", parent_uid="1", sequence_num=0),
    ]
    p.build_lookups()
    return p


def _order(p, parent):
    sibs = [w for w in p.wbs_nodes if (w.parent_uid or None) == parent]
    sibs.sort(key=lambda w: (w.sequence_num, w.name))
    return [w.name for w in sibs]


def test_reorder_works_when_every_sequence_num_is_identical():
    """The imported-file case: a bare swap of two equal numbers moves nothing,
    so siblings must be renumbered into display order first."""
    p = _tree()
    assert {w.sequence_num for w in p.wbs_nodes} == {0}
    ok, _ = apply_command(p, {"action": "reorder_wbs", "wbs_uid": "12", "direction": "up"})
    assert ok
    assert _order(p, "1") == ["Sub1", "Sub3", "Sub2"]


def test_moving_up_and_down_round_trips():
    p = _tree()
    apply_command(p, {"action": "reorder_wbs", "wbs_uid": "10", "direction": "down"})
    assert _order(p, "1") == ["Sub2", "Sub1", "Sub3"]
    apply_command(p, {"action": "reorder_wbs", "wbs_uid": "10", "direction": "up"})
    assert _order(p, "1") == ["Sub1", "Sub2", "Sub3"]


def test_reordering_at_the_edge_is_a_no_op_not_an_error():
    """The grid's arrows are always clickable, so hitting the end must report
    success with an explanation rather than flashing a red error."""
    p = _tree()
    ok, msg = apply_command(p, {"action": "reorder_wbs", "wbs_uid": "10", "direction": "up"})
    assert ok and "already first" in msg
    assert _order(p, "1") == ["Sub1", "Sub2", "Sub3"]

    ok, msg = apply_command(p, {"action": "reorder_wbs", "wbs_uid": "12", "direction": "down"})
    assert ok and "already last" in msg


def test_reorder_never_leaks_across_parents():
    p = _tree()
    apply_command(p, {"action": "reorder_wbs", "wbs_uid": "12", "direction": "up"})
    assert _order(p, None) == ["Area A", "Area B"]        # roots untouched

    apply_command(p, {"action": "reorder_wbs", "wbs_uid": "1", "direction": "down"})
    assert _order(p, None) == ["Area B", "Area A"]
    assert _order(p, "1") == ["Sub1", "Sub3", "Sub2"]     # children kept their order


def test_reorder_rejects_a_bad_direction():
    p = _tree()
    ok, msg = apply_command(p, {"action": "reorder_wbs", "wbs_uid": "1", "direction": "sideways"})
    assert not ok and "up" in msg and "down" in msg


def test_reorder_reports_an_unknown_folder():
    p = _tree()
    ok, msg = apply_command(p, {"action": "reorder_wbs", "wbs_uid": "nope", "direction": "up"})
    assert not ok and "not found" in msg.lower()


# ── Renaming by uid ──────────────────────────────────────────────────────────

def test_rename_by_uid_targets_exactly_that_folder():
    """Folder names repeat and name lookup is a substring match, so renaming
    'Sub1' by name could hit 'Sub10'. The grid passes uid to avoid that."""
    p = _tree()
    p.wbs_nodes.append(WBSNode(uid="13", name="Sub1 Extra", code="S1E",
                               parent_uid="1", sequence_num=0))
    ok, _ = apply_command(p, {"action": "rename_wbs", "wbs_uid": "13",
                              "new_name": "Renamed"})
    assert ok
    by_uid = {w.uid: w.name for w in p.wbs_nodes}
    assert by_uid["13"] == "Renamed"
    assert by_uid["10"] == "Sub1"          # the substring twin is untouched


def test_rename_by_uid_beats_a_conflicting_name_argument():
    p = _tree()
    apply_command(p, {"action": "rename_wbs", "wbs_uid": "11",
                      "wbs_name": "Sub1", "new_name": "Winner"})
    by_uid = {w.uid: w.name for w in p.wbs_nodes}
    assert by_uid["11"] == "Winner"
    assert by_uid["10"] == "Sub1"


# ── Orphaned activities must never vanish from the grid ──────────────────────

def test_activities_with_no_folder_are_still_shown():
    """An activity whose wbs_uid matches no WBS node used to be dropped from
    the schedule view while still counting toward activity_count — a row that
    exists and exports but cannot be seen, which reads as 'my edit did
    nothing'. It must surface in a synthetic Unassigned folder instead."""
    import server
    from engine.schedule_model import Activity

    c = server.app.test_client()
    r = c.post("/api/import/paste",
               json={"text": "A1000\tMobilize\t10\t05-Jan-26\t16-Jan-26",
                     "project_name": "B"})
    c.post("/api/import/commit", json={"contract": r.get_json()["contract"],
                                       "mode": "replace", "project_name": "B"})
    p = server._get_session()["project"]
    p.activities.append(Activity(uid="999", activity_id="ORPHAN1",
                                 name="Orphaned row", wbs_uid="does-not-exist",
                                 calendar_uid="1", planned_duration=40.0))
    p.build_lookups()

    s = c.get("/api/schedule").get_json()
    shown = [a["activity_id"] for w in s["wbs_sections"] for a in w["activities"]]
    assert "ORPHAN1" in shown
    # the headline count and what the grid can actually draw must agree
    assert s["activity_count"] == len(shown)


# ── Deleting a folder ────────────────────────────────────────────────────────

def _peopled():
    """Area A (holding one activity) with a sub-folder, plus Area B."""
    from engine.schedule_model import Activity, Relation
    p = _tree()
    p.wbs_nodes = [w for w in p.wbs_nodes if w.uid in ("1", "2", "10")]
    p.activities = [
        Activity(uid="a1", activity_id="A1", name="In Area A", wbs_uid="1",
                 calendar_uid="1", planned_duration=8.0),
        Activity(uid="a2", activity_id="A2", name="In Sub1", wbs_uid="10",
                 calendar_uid="1", planned_duration=8.0),
        Activity(uid="a3", activity_id="A3", name="In Area B", wbs_uid="2",
                 calendar_uid="1", planned_duration=8.0),
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="a2", successor_uid="a3")]
    p.build_lookups()
    return p


def test_delete_folder_keeps_the_activities_by_default():
    """The default must never destroy work — the branch goes, the activities
    move up to the deleted folder's parent."""
    p = _peopled()
    ok, msg = apply_command(p, {"action": "delete_wbs", "wbs_uid": "1"})
    assert ok
    assert [w.name for w in p.wbs_nodes] == ["Area B"]
    assert len(p.activities) == 3                      # nothing lost
    assert {a.wbs_uid for a in p.activities} == {"2"}   # rehomed to a real folder
    assert len(p.relations) == 1


def test_delete_folder_with_contents_removes_activities_and_relations():
    p = _peopled()
    ok, msg = apply_command(p, {"action": "delete_wbs", "wbs_uid": "1",
                                "delete_contents": True})
    assert ok
    assert [a.activity_id for a in p.activities] == ["A3"]
    assert p.relations == []                            # dangling link cleaned up


def test_deleting_a_nested_folder_moves_work_to_its_parent():
    p = _peopled()
    apply_command(p, {"action": "delete_wbs", "wbs_uid": "10"})
    assert sorted(w.name for w in p.wbs_nodes) == ["Area A", "Area B"]
    assert p.get_activity(activity_id="A2").wbs_uid == "1"


def test_deleting_the_last_folder_holding_work_is_refused():
    """Rather than orphan the activities into a folder that doesn't exist."""
    p = _peopled()
    apply_command(p, {"action": "delete_wbs", "wbs_uid": "1", "delete_contents": True})
    ok, msg = apply_command(p, {"action": "delete_wbs", "wbs_uid": "2"})
    assert not ok and "no other folder" in msg


def test_delete_wbs_reports_an_unknown_folder():
    p = _peopled()
    ok, msg = apply_command(p, {"action": "delete_wbs", "wbs_uid": "nope"})
    assert not ok and "not found" in msg.lower()
