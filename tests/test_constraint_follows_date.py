"""
test_constraint_follows_date.py — a date the user sets must be the date that
survives the next recalculation, even when the row is pinned.

This is the long tail of "I type a date and it jumps back to the original".
Half of the real schedule this was found on is pinned — 1,368 of 2,729
activities carry a Start On constraint — and setting planned_start while
leaving the pin behind left the two contradicting each other. The constraint
wins in the CPM, so the typed date was thrown away on the very next edit.

Setting a date now re-dates a constraint the activity ALREADY has. It never
invents one and never changes its type; removing a constraint stays an
explicit act (clear_constraint).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.edit_engine import apply_command
from engine.schedule_model import (Project, Activity, WBSNode, Relation, Calendar,
                                   compute_dates)


def _proj(constraint=None, cdate=None, dur=5.0, atype="Task Dependent",
          start="2026-01-05", finish="2026-01-09"):
    p = Project(uid="p", name="P", id="P")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=8.0)]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None)]
    p.activities = [Activity(
        uid="a1", activity_id="A1000", name="Task", wbs_uid="w", calendar_uid="1",
        activity_type=atype, status="Not Started",
        planned_duration=dur * 8, remaining_duration=dur * 8,
        planned_start=start, planned_finish=finish,
        constraint_type=constraint, constraint_date=cdate)]
    p.build_lookups()
    return p


def _a(p):
    return p.activities[0]


def _set(p, field, date, **extra):
    cmd = {"action": "update_planned_date", "activity_id": "A1000",
           "field": field, "date": date}
    cmd.update(extra)
    return apply_command(p, cmd)


# ── the reported behaviour ────────────────────────────────────────────────────

def test_start_on_pin_follows_a_new_start():
    p = _proj("Start On", "2026-01-05")
    ok, msg = _set(p, "start", "2026-03-16")
    assert ok
    assert _a(p).planned_start == "2026-03-16"
    assert _a(p).constraint_date == "2026-03-16"      # the pin came along
    assert _a(p).constraint_type == "Start On"        # type untouched
    assert "moved to 2026-03-16" in msg


def test_the_new_date_survives_a_recalculation():
    """The whole point: recompute must not drag the date back to the old pin."""
    p = _proj("Start On", "2026-01-05")
    _set(p, "start", "2026-03-16")
    compute_dates(p)
    assert _a(p).planned_start == "2026-03-16"


def test_unpinned_row_gains_no_constraint():
    """Setting a date must not invent a pin — that was its own complaint."""
    p = _proj(None, None)
    _set(p, "start", "2026-03-16")
    assert not _a(p).constraint_type
    assert not _a(p).constraint_date


def test_finish_on_pin_follows_a_new_finish():
    p = _proj("Finish On", "2026-01-09")
    ok, _ = _set(p, "finish", "2026-01-16")
    assert ok
    assert _a(p).planned_finish == "2026-01-16"
    assert _a(p).constraint_date == "2026-01-16"


def test_finish_pin_shifts_with_the_start_it_hangs_off():
    """
    A finish pin is not about the start, so it is not jumped onto the start
    date — it travels by the same offset, keeping the gap the user had.
    """
    p = _proj("Finish On", "2026-01-09")            # start 01-05, finish 01-09
    _set(p, "start", "2026-01-12")                  # +7 calendar days
    assert _a(p).constraint_date == "2026-01-16"    # 01-09 + 7
    assert _a(p).constraint_type == "Finish On"


def test_start_pin_is_left_alone_when_only_the_finish_moves():
    """Typing a finish changes the duration; the start has not moved."""
    p = _proj("Start On", "2026-01-05")
    _set(p, "finish", "2026-01-16")
    assert _a(p).constraint_date == "2026-01-05"    # untouched
    assert _a(p).planned_start == "2026-01-05"


def test_milestone_date_moves_either_kind_of_pin():
    for ct in ("Start On", "Finish On"):
        p = _proj(ct, "2026-01-05", dur=0.0, atype="Start Milestone",
                  start="2026-01-05", finish="2026-01-05")
        _set(p, "finish", "2026-04-01")
        assert _a(p).constraint_date == "2026-04-01", ct
        assert _a(p).constraint_type == ct


# ── the softer constraint kinds keep their meaning ────────────────────────────

def test_start_on_or_after_follows_without_becoming_a_hard_pin():
    p = _proj("Start On Or After", "2026-01-05")
    _set(p, "start", "2026-03-16")
    assert _a(p).constraint_date == "2026-03-16"
    assert _a(p).constraint_type == "Start On Or After"


def test_deadline_follows_and_stays_a_deadline():
    p = _proj("Finish On Or Before", "2026-01-09")
    _set(p, "finish", "2026-02-06")
    assert _a(p).constraint_date == "2026-02-06"
    assert _a(p).constraint_type == "Finish On Or Before"


# ── opting out, and removing a constraint ─────────────────────────────────────

def test_move_constraint_false_leaves_the_pin_where_it_was():
    p = _proj("Start On", "2026-01-05")
    _set(p, "start", "2026-03-16", move_constraint=False)
    assert _a(p).planned_start == "2026-03-16"
    assert _a(p).constraint_date == "2026-01-05"


def test_clearing_the_constraint_still_removes_it():
    """Carrying a pin must not make it un-removable."""
    p = _proj("Start On", "2026-01-05")
    _set(p, "start", "2026-03-16")
    apply_command(p, {"action": "clear_constraint", "activity_id": "A1000"})
    assert not _a(p).constraint_type
    compute_dates(p)
    assert _a(p).planned_start == "2026-03-16"       # date kept, pin gone


def test_a_constraint_with_no_date_is_left_alone():
    p = _proj("Start On", None)
    ok, _ = _set(p, "start", "2026-03-16")
    assert ok
    assert _a(p).constraint_date is None


# ── a pinned row that is also linked ──────────────────────────────────────────

def test_pinned_successor_holds_its_new_date_against_its_predecessor():
    p = _proj("Start On", "2026-01-05")
    p.activities.append(Activity(
        uid="a0", activity_id="A0900", name="Pred", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=40.0, remaining_duration=40.0,
        planned_start="2026-01-05", planned_finish="2026-01-09"))
    p.relations = [Relation(uid="r1", predecessor_uid="a0", successor_uid="a1",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()

    _set(p, "start", "2026-06-01")
    compute_dates(p)
    assert _a(p).planned_start == "2026-06-01"
