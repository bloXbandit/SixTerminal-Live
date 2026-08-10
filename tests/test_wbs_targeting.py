"""
test_wbs_targeting.py — An edit aimed at a folder must land in THAT folder.

Folder names repeat throughout a real WBS: the reference schedule has 38
repeated names, one of them seven times. Name lookup is a substring match that
returns the first hit, so every folder-targeting action — paste, move,
add-activity, new-sub-folder — silently landed in whichever same-named folder
came first, regardless of which one the user picked. These tests pin the uid
path that makes the target unambiguous.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation
from engine.edit_engine import apply_command, apply_commands


def _repeated_names():
    """'MV Rooms' under three different phases — the real-world shape."""
    p = Project(uid="1", name="DC", id="DC", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="p1", name="Phase 1", code="P1"),
        WBSNode(uid="p2", name="Phase 2", code="P2"),
        WBSNode(uid="p3", name="Phase 3", code="P3"),
        WBSNode(uid="r1", name="MV Rooms", code="MV1", parent_uid="p1"),
        WBSNode(uid="r2", name="MV Rooms", code="MV2", parent_uid="p2"),
        WBSNode(uid="r3", name="MV Rooms", code="MV3", parent_uid="p3"),
        WBSNode(uid="src", name="Source", code="SRC", parent_uid="p1"),
    ]
    p.activities = [
        Activity(uid="a1", activity_id="A1000", name="Rough-in", wbs_uid="src",
                 calendar_uid="1", planned_duration=40.0,
                 planned_start="2026-01-05", planned_finish="2026-01-09"),
        Activity(uid="a2", activity_id="A1010", name="Terminate", wbs_uid="src",
                 calendar_uid="1", planned_duration=40.0,
                 planned_start="2026-01-12", planned_finish="2026-01-16"),
    ]
    p.relations = [Relation(uid="rel1", predecessor_uid="a1", successor_uid="a2")]
    p.build_lookups()
    return p


def _count_in(p, uid):
    return sum(1 for a in p.activities if a.wbs_uid == uid)


# ── Pasting activities ───────────────────────────────────────────────────────

def test_paste_lands_in_the_folder_chosen_by_uid_not_the_first_of_that_name():
    for target in ("r1", "r2", "r3"):
        p = _repeated_names()
        ok, _ = apply_command(p, {"action": "copy_activities",
                                  "activity_ids": ["A1000", "A1010"],
                                  "wbs_uid": target})
        assert ok
        assert _count_in(p, target) == 2, f"paste missed {target}"
        for other in {"r1", "r2", "r3"} - {target}:
            assert _count_in(p, other) == 0, f"rows leaked into {other}"


def test_paste_by_name_alone_is_still_accepted_for_older_callers():
    p = _repeated_names()
    ok, _ = apply_command(p, {"action": "copy_activities",
                              "activity_ids": ["A1000"], "wbs_name": "MV Rooms"})
    assert ok
    assert sum(_count_in(p, u) for u in ("r1", "r2", "r3")) == 1


def test_pasted_rows_keep_the_logic_between_them():
    p = _repeated_names()
    before = len(p.relations)
    apply_command(p, {"action": "copy_activities",
                      "activity_ids": ["A1000", "A1010"], "wbs_uid": "r2"})
    assert len(p.relations) == before + 1, "internal logic did not travel with the copy"


# ── Creating a folder and pasting into it, atomically ────────────────────────

def test_new_folder_and_paste_land_together_even_when_the_name_already_exists():
    """The trap: creating "MV Rooms" then pasting into it BY NAME puts the rows
    in the pre-existing folder. Minting the uid up front keeps the batch exact."""
    p = _repeated_names()
    results = apply_commands(p, [
        {"action": "add_wbs", "name": "MV Rooms", "parent_uid": "r1",
         "new_wbs_uid": "brand-new"},
        {"action": "copy_activities", "activity_ids": ["A1000", "A1010"],
         "wbs_uid": "brand-new"},
    ])
    assert all(ok for ok, _ in results)
    assert _count_in(p, "brand-new") == 2
    for other in ("r1", "r2", "r3"):
        assert _count_in(p, other) == 0
    node = next(w for w in p.wbs_nodes if w.uid == "brand-new")
    assert node.parent_uid == "r1"


def test_a_supplied_uid_must_be_unique():
    p = _repeated_names()
    ok, msg = apply_command(p, {"action": "add_wbs", "name": "X",
                                "new_wbs_uid": "r1"})
    assert not ok and "already exists" in msg


def test_add_wbs_without_a_uid_still_generates_one():
    p = _repeated_names()
    before = {w.uid for w in p.wbs_nodes}
    ok, _ = apply_command(p, {"action": "add_wbs", "name": "Fresh"})
    assert ok
    new = {w.uid for w in p.wbs_nodes} - before
    assert len(new) == 1 and new.pop()


# ── Moving and adding into a folder ──────────────────────────────────────────

def test_move_activity_targets_the_chosen_folder():
    for target in ("r1", "r2", "r3"):
        p = _repeated_names()
        ok, _ = apply_command(p, {"action": "move_activity_wbs",
                                  "activity_id": "A1000", "wbs_uid": target})
        assert ok
        assert p.get_activity(activity_id="A1000").wbs_uid == target


def test_add_activity_targets_the_chosen_folder():
    for target in ("r1", "r2", "r3"):
        p = _repeated_names()
        ok, _ = apply_command(p, {"action": "add_activity", "wbs_uid": target,
                                  "name": "New work", "duration_days": 3})
        assert ok
        assert _count_in(p, target) == 1


def test_new_sub_folder_nests_under_the_chosen_parent():
    for target in ("r1", "r2", "r3"):
        p = _repeated_names()
        ok, _ = apply_command(p, {"action": "add_wbs", "name": "Sub",
                                  "parent_uid": target})
        assert ok
        sub = next(w for w in p.wbs_nodes if w.name == "Sub")
        assert sub.parent_uid == target


def test_an_unknown_uid_is_reported_rather_than_falling_back_to_a_name():
    """Silently landing somewhere else is worse than refusing."""
    p = _repeated_names()
    ok, msg = apply_command(p, {"action": "copy_activities",
                                "activity_ids": ["A1000"], "wbs_uid": "nope"})
    assert not ok and "not found" in msg.lower()


# ── Renaming ─────────────────────────────────────────────────────────────────

def test_rename_hits_only_the_chosen_folder_among_identical_names():
    p = _repeated_names()
    ok, _ = apply_command(p, {"action": "rename_wbs", "wbs_uid": "r2",
                              "new_name": "MV Rooms (Phase 2)"})
    assert ok
    by_uid = {w.uid: w.name for w in p.wbs_nodes}
    assert by_uid["r2"] == "MV Rooms (Phase 2)"
    assert by_uid["r1"] == "MV Rooms"
    assert by_uid["r3"] == "MV Rooms"
