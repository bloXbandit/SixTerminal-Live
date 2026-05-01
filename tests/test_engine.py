"""
test_engine.py — Unit tests for the Six Terminal Live edit engine.

Run with: python -m pytest tests/ -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Relation, Calendar
from engine.edit_engine import apply_command, apply_commands


def _make_project() -> Project:
    """Create a minimal test project."""
    p = Project(uid="1", name="Test Project", id="TEST")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [
        WBSNode(uid="10", name="Structure", code="STR"),
        WBSNode(uid="11", name="Interiors", code="INT"),
        WBSNode(uid="12", name="Closeout", code="CLO"),
    ]
    p.activities = [
        Activity(uid="100", activity_id="A1000", name="Pour Slab Level 2", wbs_uid="10", calendar_uid="1",
                 planned_duration=40.0, remaining_duration=40.0, status="Not Started"),
        Activity(uid="101", activity_id="A1010", name="Frame Walls Level 2", wbs_uid="10", calendar_uid="1",
                 planned_duration=24.0, remaining_duration=24.0, status="Not Started"),
        Activity(uid="102", activity_id="A1020", name="Install Drywall Level 2", wbs_uid="11", calendar_uid="1",
                 planned_duration=32.0, remaining_duration=32.0, status="Not Started"),
        Activity(uid="103", activity_id="A1030", name="Install Drywall Level 3", wbs_uid="11", calendar_uid="1",
                 planned_duration=32.0, remaining_duration=32.0, status="Not Started"),
        Activity(uid="104", activity_id="A1040", name="Substantial Completion", wbs_uid="12", calendar_uid="1",
                 planned_duration=0.0, remaining_duration=0.0, status="Not Started",
                 activity_type="Finish Milestone"),
    ]
    p.relations = [
        Relation(uid="200", predecessor_uid="100", successor_uid="101", type="Finish to Start"),
        Relation(uid="201", predecessor_uid="101", successor_uid="102", type="Finish to Start"),
    ]
    p.build_lookups()
    return p


def test_rename_activity_by_id():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "rename_activity", "activity_id": "A1000", "new_name": "Pour Slab L2 (Revised)"})
    assert ok, msg
    assert p.get_activity(activity_id="A1000").name == "Pour Slab L2 (Revised)"


def test_rename_activity_by_name():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "rename_activity", "target_name": "Frame Walls Level 2", "new_name": "Erect Steel Frame L2"})
    assert ok, msg
    assert p.get_activity(activity_id="A1010").name == "Erect Steel Frame L2"


def test_update_duration():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "update_duration", "activity_id": "A1000", "new_duration_days": 3})
    assert ok, msg
    assert p.get_activity(activity_id="A1000").planned_duration == 24.0  # 3 days * 8h


def test_bulk_update_duration():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "bulk_update_duration", "pattern": "Install Drywall", "new_duration_days": 5, "apply_to_all": True})
    assert ok, msg
    assert p.get_activity(activity_id="A1020").planned_duration == 40.0  # 5 * 8
    assert p.get_activity(activity_id="A1030").planned_duration == 40.0


def test_add_activity():
    p = _make_project()
    ok, msg = apply_command(p, {
        "action": "add_activity",
        "activity_id": "A1099",
        "name": "Owner Punch Walk",
        "wbs_name": "Closeout",
        "duration_days": 3,
    })
    assert ok, msg
    a = p.get_activity(activity_id="A1099")
    assert a is not None
    assert a.name == "Owner Punch Walk"
    assert a.planned_duration == 24.0


def test_add_relation():
    p = _make_project()
    ok, msg = apply_command(p, {
        "action": "add_relation",
        "predecessor_id": "A1020",
        "successor_id": "A1040",
        "type": "fs",
    })
    assert ok, msg
    pred = p.get_activity(activity_id="A1020")
    succ = p.get_activity(activity_id="A1040")
    assert any(r.predecessor_uid == pred.uid and r.successor_uid == succ.uid for r in p.relations)


def test_delete_activity_removes_relations():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "delete_activity", "activity_id": "A1010"})
    assert ok, msg
    assert p.get_activity(activity_id="A1010") is None
    # Relations involving A1010 should be gone
    a1010_uid = None  # already deleted, check by original uid
    for r in p.relations:
        assert r.predecessor_uid != "101" and r.successor_uid != "101"


def test_rename_wbs():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "rename_wbs", "wbs_name": "Structure", "new_name": "Structural Steel & Concrete"})
    assert ok, msg
    wbs = next(w for w in p.wbs_nodes if w.uid == "10")
    assert wbs.name == "Structural Steel & Concrete"


def test_add_wbs():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "add_wbs", "name": "Finishes", "code": "FIN", "parent_name": "Interiors"})
    assert ok, msg
    new_wbs = next((w for w in p.wbs_nodes if w.code == "FIN"), None)
    assert new_wbs is not None
    assert new_wbs.parent_uid == "11"


def test_move_activity_wbs():
    p = _make_project()
    ok, msg = apply_command(p, {"action": "move_activity_wbs", "activity_id": "A1020", "wbs_name": "Closeout"})
    assert ok, msg
    assert p.get_activity(activity_id="A1020").wbs_uid == "12"


def test_set_and_clear_constraint():
    p = _make_project()
    ok, msg = apply_command(p, {
        "action": "set_constraint",
        "activity_id": "A1000",
        "constraint_type": "Start On Or After",
        "constraint_date": "2026-06-01",
    })
    assert ok, msg
    a = p.get_activity(activity_id="A1000")
    assert a.constraint_type == "Start On Or After"
    assert a.constraint_date == "2026-06-01"

    ok2, msg2 = apply_command(p, {"action": "clear_constraint", "activity_id": "A1000"})
    assert ok2, msg2
    assert a.constraint_type is None


if __name__ == "__main__":
    tests = [
        test_rename_activity_by_id, test_rename_activity_by_name,
        test_update_duration, test_bulk_update_duration,
        test_add_activity, test_add_relation,
        test_delete_activity_removes_relations,
        test_rename_wbs, test_add_wbs, test_move_activity_wbs,
        test_set_and_clear_constraint,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
