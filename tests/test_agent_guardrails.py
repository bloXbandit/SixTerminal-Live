"""
test_agent_guardrails.py — the two engine-side fixes for agent behaviour.

Both come from a real session where the agent invented activity IDs
(MDC1.MIL.1130, MDC1.MIL.1070 — neither exists) and then had to be walked
through a bulk WBS change one folder at a time.

  1. A lookup that fails hands back the real nearby ids. A flat "not found"
     invites another guess, and guesses compound.
  2. add_wbs_for_each does "a sub-folder under every MV room" in ONE command,
     so the ids never have to be enumerated by an agent in the first place.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.edit_engine import apply_command
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _proj():
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="JOB", parent_uid=None)]
    p.activities = []
    return p


def _wbs(p, uid, name, parent="root", code=None):
    p.wbs_nodes.append(WBSNode(uid=uid, name=name, code=code or name[:20],
                               parent_uid=parent))
    p.build_lookups()


def _act(p, uid, aid, name, wbs="root"):
    p.activities.append(Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                                 calendar_uid="1", status="Not Started",
                                 planned_duration=40.0, remaining_duration=40.0,
                                 planned_start="2026-02-02", planned_finish="2026-02-06"))
    p.build_lookups()


def _rooms(p):
    """Three MV rooms and a decoy that must not match a digit-anchored regex."""
    for i, n in enumerate(("MV 105", "MV 106", "MV 210")):
        _wbs(p, f"mv{i}", n)
    _wbs(p, "other", "Generator Room 1")
    return p


# ── 1. a failed lookup names the real neighbours ──────────────────────────────

def test_a_missing_activity_id_suggests_the_real_ones():
    p = _proj()
    _act(p, "a1", "MDC1.MIL.1120", "Service Authority")
    _act(p, "a2", "MDC1.MIL.1140", "Complete Construction")
    ok, msg = apply_command(p, {"action": "rename_activity",
                                "activity_id": "MDC1.MIL.1130", "new_name": "X"})
    assert not ok
    assert "not found" in msg
    assert "MDC1.MIL.1120" in msg and "MDC1.MIL.1140" in msg


def test_a_missing_predecessor_says_which_end_was_wrong():
    p = _proj()
    _act(p, "a1", "MDC1.MIL.1120", "Service Authority")
    _act(p, "a2", "MDC1.MIL.1140", "Complete Construction")
    ok, msg = apply_command(p, {"action": "add_relation",
                                "predecessor_id": "MDC1.MIL.1130",
                                "successor_id": "MDC1.MIL.1140"})
    assert not ok
    assert msg.startswith("Predecessor")
    assert "MDC1.MIL.1120" in msg


def test_a_near_miss_on_the_name_is_suggested_too():
    p = _proj()
    _act(p, "a1", "A1000", "Precast Erection Area 1")
    ok, msg = apply_command(p, {"action": "delete_activity",
                                "activity_id": "Precast Erection Area 7"})
    assert not ok and "A1000" in msg


def test_a_missing_wbs_suggests_real_folders():
    p = _proj()
    _wbs(p, "w1", "Structure")
    ok, msg = apply_command(p, {"action": "add_activity", "wbs_name": "Structrue",
                                "name": "T", "duration_days": 1})
    assert not ok and "Structure" in msg


def test_no_suggestion_is_offered_when_nothing_is_close():
    """Better to say 'use one from the context' than to list a random folder."""
    p = _proj()
    _act(p, "a1", "A1000", "Precast Erection")
    ok, msg = apply_command(p, {"action": "rename_activity",
                                "activity_id": "ZZZZZZZZ", "new_name": "X"})
    assert not ok and "Did you mean" not in msg


# ── 2. one command, a folder under every match ────────────────────────────────

def test_a_child_is_added_under_every_matching_folder():
    p = _rooms(_proj())
    ok, msg = apply_command(p, {"action": "add_wbs_for_each",
                                "match_regex": r"^MV\s*(\d+)",
                                "name_template": "WBO MV {1}"})
    assert ok, msg
    kids = {w.name: w.parent_uid for w in p.wbs_nodes if w.name.startswith("WBO")}
    assert kids == {"WBO MV 105": "mv0", "WBO MV 106": "mv1", "WBO MV 210": "mv2"}


def test_the_decoy_folder_is_left_alone():
    p = _rooms(_proj())
    apply_command(p, {"action": "add_wbs_for_each", "match_regex": r"^MV\s*(\d+)",
                      "name_template": "WBO MV {1}"})
    assert not [w for w in p.wbs_nodes if w.parent_uid == "other"]


def test_the_num_placeholder_pulls_the_room_number():
    p = _rooms(_proj())
    apply_command(p, {"action": "add_wbs_for_each", "match_contains": "MV",
                      "name_template": "WBO MV {num}"})
    assert any(w.name == "WBO MV 105" for w in p.wbs_nodes)


def test_the_name_placeholder_uses_the_whole_parent_name():
    p = _rooms(_proj())
    apply_command(p, {"action": "add_wbs_for_each", "match_contains": "MV",
                      "name_template": "WBO {name}"})
    assert any(w.name == "WBO MV 105" for w in p.wbs_nodes)


def test_running_it_twice_adds_nothing_the_second_time():
    p = _rooms(_proj())
    cmd = {"action": "add_wbs_for_each", "match_regex": r"^MV\s*(\d+)",
           "name_template": "WBO MV {1}"}
    apply_command(p, cmd)
    before = len(p.wbs_nodes)
    ok, msg = apply_command(p, cmd)
    assert ok and len(p.wbs_nodes) == before
    assert "already" in msg.lower()


def test_a_second_run_cannot_nest_wbo_inside_wbo():
    """The children match 'MV' too — matching must not chase its own output."""
    p = _rooms(_proj())
    cmd = {"action": "add_wbs_for_each", "match_contains": "MV",
           "name_template": "WBO {name}"}
    apply_command(p, cmd)
    apply_command(p, cmd)
    assert not [w for w in p.wbs_nodes if w.name.startswith("WBO WBO")]


def test_scope_restricts_matching_to_one_branch():
    p = _proj()
    _wbs(p, "ph1", "Phase 1")
    _wbs(p, "ph2", "Phase 2")
    _wbs(p, "a", "MV 105", parent="ph1")
    _wbs(p, "b", "MV 106", parent="ph2")
    apply_command(p, {"action": "add_wbs_for_each", "match_contains": "MV",
                      "name_template": "WBO {name}",
                      "under_parent_name": "Phase 1"})
    made = [w for w in p.wbs_nodes if w.name.startswith("WBO")]
    assert len(made) == 1 and made[0].parent_uid == "a"


def test_matching_nothing_is_an_error_not_a_silent_success():
    p = _rooms(_proj())
    ok, msg = apply_command(p, {"action": "add_wbs_for_each",
                                "match_contains": "Chiller",
                                "name_template": "WBO {name}"})
    assert not ok and "no wbs folder matches" in msg.lower()


def test_a_missing_template_is_refused():
    p = _rooms(_proj())
    ok, msg = apply_command(p, {"action": "add_wbs_for_each", "match_contains": "MV"})
    assert not ok and "name_template" in msg


def test_a_bad_regex_is_reported_not_raised():
    p = _rooms(_proj())
    ok, msg = apply_command(p, {"action": "add_wbs_for_each", "match_regex": "MV(",
                                "name_template": "WBO {name}"})
    assert not ok and "pattern" in msg.lower()


def test_the_message_names_what_was_created():
    p = _rooms(_proj())
    ok, msg = apply_command(p, {"action": "add_wbs_for_each", "match_contains": "MV",
                                "name_template": "WBO {name}"})
    assert ok and "3" in msg and "WBO MV 105" in msg


def test_a_code_template_is_honoured():
    p = _rooms(_proj())
    apply_command(p, {"action": "add_wbs_for_each", "match_regex": r"^MV\s*(\d+)",
                      "name_template": "WBO MV {1}", "code_template": "WBO{1}"})
    made = next(w for w in p.wbs_nodes if w.name == "WBO MV 105")
    assert made.code == "WBO105"
