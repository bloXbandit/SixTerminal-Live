"""
test_bulk_append_name.py — Mass "add text to names" without touching what's
already there, e.g. suffixing "(ER 209)" onto every activity in a folder.

Shares the same scope contract as bulk_clear_constraints (activity_ids |
wbs_name/wbs_code recursive | all), verified here for the recursive case
specifically since that reaches into nested sub-folders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar
from engine.edit_engine import apply_command


def _proj():
    p = Project(uid="1", name="T", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="root", name="Root", code="R"),
        WBSNode(uid="er", name="ER 209", code="ER209", parent_uid="root"),
        WBSNode(uid="sub", name="Rough-in phase", code="RI", parent_uid="er"),
        WBSNode(uid="other", name="Other room", code="OTH", parent_uid="root"),
    ]
    p.activities = [
        Activity(uid="1", activity_id="A1000", name="Terminate wire", wbs_uid="er", calendar_uid="1"),
        Activity(uid="2", activity_id="A1010", name="Pull cable", wbs_uid="sub", calendar_uid="1"),
        Activity(uid="3", activity_id="A1020", name="Unrelated work", wbs_uid="other", calendar_uid="1"),
    ]
    p.build_lookups()
    return p


def test_suffix_reaches_nested_sub_folders():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "wbs_name": "ER 209", "text": "(ER 209)"})
    assert ok, msg
    assert p.get_activity(activity_id="A1000").name == "Terminate wire (ER 209)"
    assert p.get_activity(activity_id="A1010").name == "Pull cable (ER 209)"       # nested
    assert p.get_activity(activity_id="A1020").name == "Unrelated work"           # sibling untouched


def test_rerun_is_idempotent():
    p = _proj()
    apply_command(p, {"action": "bulk_append_name", "wbs_name": "ER 209", "text": "(ER 209)"})
    ok, msg = apply_command(p, {"action": "bulk_append_name", "wbs_name": "ER 209", "text": "(ER 209)"})
    assert ok
    assert p.get_activity(activity_id="A1000").name == "Terminate wire (ER 209)"   # not doubled
    assert "already had it" in msg


def test_prefix_with_activity_ids_scope():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "activity_ids": ["A1020"],
                                "text": "SW -", "position": "prefix"})
    assert ok
    assert p.get_activity(activity_id="A1020").name == "SW - Unrelated work"


def test_all_scope():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "all": True, "text": "[Q1]"})
    assert ok
    assert all(a.name.endswith("[Q1]") for a in p.activities)


def test_custom_separator():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "activity_ids": ["A1000"],
                                "text": "-REV2", "separator": ""})
    assert ok
    assert p.get_activity(activity_id="A1000").name == "Terminate wire-REV2"


def test_missing_text_is_rejected():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "activity_ids": ["A1000"]})
    assert ok is False


def test_bad_position_is_rejected():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "activity_ids": ["A1000"],
                                "text": "x", "position": "middle"})
    assert ok is False


def test_no_scope_is_rejected():
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_append_name", "text": "x"})
    assert ok is False


def test_bulk_rename_activities_wbs_scope_now_recurses():
    """Regression: the wbs_name scope in bulk_rename_activities only matched
    activities living DIRECTLY in that folder, silently skipping anything one
    level deeper — inconsistent with every other WBS-scoped bulk action."""
    p = _proj()
    ok, msg = apply_command(p, {"action": "bulk_rename_activities",
                                "renames": [{"wbs_name": "ER 209",
                                            "to_name": "{original} — reviewed"}]})
    assert ok, msg
    assert p.get_activity(activity_id="A1000").name == "Terminate wire — reviewed"
    assert p.get_activity(activity_id="A1010").name == "Pull cable — reviewed"    # nested
    assert p.get_activity(activity_id="A1020").name == "Unrelated work"          # sibling untouched
