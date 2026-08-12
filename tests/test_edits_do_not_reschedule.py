"""
test_edits_do_not_reschedule.py — an edit changes what was edited, nothing else.

P6 does not reschedule because you typed in a cell; you press F9. This app
recalculated after every command and wrote the results straight back over
Start / Finish, so renaming one activity moved 991 of the reference schedule's
2,729 dates. That is the "my dates keep flipping" report in its final form.

Implicit recomputes now refresh only the derived columns — early / late dates,
total float, the critical path — and leave Start / Finish alone. The Schedule
(F9) action is the one path that reflows them.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.edit_engine import apply_commands
from engine.schedule_model import (Project, Activity, WBSNode, Relation, Calendar,
                                   compute_dates)


def _proj():
    """A → B → C, where B and C are stale: logic puts them much later."""
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=8.0)]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None)]
    mk = lambda uid, aid, start, dur: Activity(
        uid=uid, activity_id=aid, name=aid, wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=dur * 8, remaining_duration=dur * 8,
        planned_start=start, planned_finish=start)
    p.activities = [mk("a", "A", "2026-01-05", 20),
                    mk("b", "B", "2026-01-06", 5),
                    mk("c", "C", "2026-01-07", 5)]
    p.relations = [Relation(uid="r1", predecessor_uid="a", successor_uid="b",
                            type="Finish to Start", lag=0.0),
                   Relation(uid="r2", predecessor_uid="b", successor_uid="c",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    return p


def _dates(p):
    return {a.activity_id: (a.planned_start, a.planned_finish) for a in p.activities}


def _by(p, aid):
    return next(a for a in p.activities if a.activity_id == aid)


# ── an edit moves only what it edited ─────────────────────────────────────────

def test_renaming_one_activity_moves_no_dates():
    p = _proj()
    before = _dates(p)
    apply_commands(p, [{"action": "rename_activity", "activity_id": "A",
                        "new_name": "A renamed"}])
    assert _dates(p) == before


def test_changing_a_duration_does_not_push_successors():
    p = _proj()
    before = _dates(p)
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    after = _dates(p)
    assert after["B"] == before["B"]
    assert after["C"] == before["C"]


def test_adding_a_relation_does_not_move_the_successor():
    p = _proj()
    before = _dates(p)
    apply_commands(p, [{"action": "add_relation",
                        "predecessor_id": "A", "successor_id": "C"}])
    assert _dates(p)["C"] == before["C"]


def test_a_date_edit_moves_exactly_the_row_it_names():
    p = _proj()
    before = _dates(p)
    apply_commands(p, [{"action": "update_planned_date", "activity_id": "B",
                        "field": "start", "date": "2026-08-03"}])
    after = _dates(p)
    assert after["B"][0] == "2026-08-03"
    assert after["A"] == before["A"]
    assert after["C"] == before["C"]


# ── the derived columns stay live ─────────────────────────────────────────────

def test_float_and_critical_path_still_refresh_on_an_edit():
    p = _proj()
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    assert all(a.early_start for a in p.activities)
    assert all(a.total_float is not None for a in p.activities)
    assert any(a.is_critical for a in p.activities)


def test_early_dates_reflect_the_new_logic_even_though_start_did_not_move():
    """This divergence is the point — it is what the drift badge counts."""
    p = _proj()
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    b = _by(p, "B")
    assert b.early_start > b.planned_start


# ── new rows are never blank ──────────────────────────────────────────────────

def test_a_new_activity_without_dates_is_seeded():
    p = _proj()
    apply_commands(p, [{"action": "add_activity", "wbs_uid": "w",
                        "name": "Fresh", "duration_days": 5}])
    new = _by(p, next(a.activity_id for a in p.activities if a.name == "Fresh"))
    assert new.planned_start and new.planned_finish


def test_a_new_activity_with_dates_keeps_exactly_those_dates():
    p = _proj()
    apply_commands(p, [{"action": "add_activity", "wbs_uid": "w",
                        "name": "Fresh", "duration_days": 5,
                        "planned_start": "2027-03-01",
                        "planned_finish": "2027-03-05"}])
    new = next(a for a in p.activities if a.name == "Fresh")
    assert (new.planned_start, new.planned_finish) == ("2027-03-01", "2027-03-05")


# ── Schedule (F9) is still a real reflow ──────────────────────────────────────

def test_schedule_reflows_everything_on_demand():
    p = _proj()
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    before = _dates(p)
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    after = _dates(p)
    assert after["B"] != before["B"]                 # now it moves
    assert _by(p, "B").planned_start == _by(p, "B").early_start


# ── the drift count that drives the badge ─────────────────────────────────────

def test_drift_count_is_zero_on_a_freshly_scheduled_project():
    p = _proj()
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    assert server._out_of_date_count(p) == 0


def test_drift_count_rises_when_an_edit_outdates_the_dates():
    p = _proj()
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    assert server._out_of_date_count(p) > 0


def test_drift_count_returns_to_zero_after_scheduling():
    p = _proj()
    apply_commands(p, [{"action": "update_duration", "activity_id": "A",
                        "new_duration_days": 40}])
    assert server._out_of_date_count(p) > 0
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    assert server._out_of_date_count(p) == 0


def test_started_work_never_counts_as_drift():
    """Actual dates are facts; no reschedule moves them, so they are not drift."""
    p = _proj()
    # C is the only row whose dates disagree with its logic...
    for aid in ("A", "B"):
        a = _by(p, aid)
        a.planned_start = a.planned_finish = "2026-01-05"
    compute_dates(p, apply_dates=False)
    baseline = server._out_of_date_count(p)
    assert baseline > 0

    # ...now mark it started. Its dates are actuals, so it drops out entirely.
    c = _by(p, "C")
    c.status, c.actual_start = "In Progress", "2026-01-07"
    compute_dates(p, apply_dates=False)
    assert server._out_of_date_count(p) == baseline - 1
