"""
test_date_duration_math.py — start, finish and duration must always agree.

P6 counts a duration inclusively: a 5-day activity starting Monday finishes
Friday. This app used finish = start + duration, putting every finish a day
late; FS successors then started on the predecessor's finish day and the two
errors cancelled along a chain, which is why it went unnoticed. Measured
against the reference export, 793 of its 826 whole-day activities follow the
inclusive rule, and correcting it took our own rescheduled finish dates from
29 matching P6 to 1,364.

The editing half of the same rule:
  set the start  → the finish moves with it, keeping the duration
  set the finish → the duration is recalculated, the start stays put
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.edit_engine import apply_command, apply_commands
from engine.schedule_model import (Project, Activity, WBSNode, Relation, Calendar,
                                   compute_dates)


def _proj(dur=5.0, start="2026-02-02", finish="2026-02-06", hpd=8.0):
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=hpd)]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W", parent_uid=None)]
    p.activities = [Activity(
        uid="a1", activity_id="A1000", name="Task", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=dur * hpd, remaining_duration=dur * hpd,
        planned_start=start, planned_finish=finish)]
    p.build_lookups()
    return p


def _a(p):
    return p.activities[0]


def _dur(p, hpd=8.0):
    return _a(p).planned_duration / hpd


def _set(p, field, date):
    return apply_command(p, {"action": "update_planned_date", "activity_id": "A1000",
                             "field": field, "date": date})


# ── setting a start carries the finish ────────────────────────────────────────

def test_setting_a_start_moves_the_finish_by_the_duration():
    p = _proj(dur=5.0)
    _set(p, "start", "2026-03-02")                 # a Monday
    assert _a(p).planned_start == "2026-03-02"
    assert _a(p).planned_finish == "2026-03-06"    # Friday — five working days
    assert _dur(p) == 5.0                          # duration untouched


def test_a_four_day_activity_finishes_on_the_fourth_day():
    p = _proj(dur=4.0)
    _set(p, "start", "2026-03-02")
    assert _a(p).planned_finish == "2026-03-05"    # Mon-Thu


def test_a_one_day_activity_starts_and_finishes_the_same_day():
    p = _proj(dur=1.0)
    _set(p, "start", "2026-03-02")
    assert _a(p).planned_finish == "2026-03-02"


def test_the_finish_skips_the_weekend():
    p = _proj(dur=5.0)
    _set(p, "start", "2026-03-05")                 # Thursday
    assert _a(p).planned_finish == "2026-03-11"    # Thu,Fri,Mon,Tue,Wed


def test_the_finish_skips_a_holiday_on_the_activity_calendar():
    p = _proj(dur=5.0)
    p.calendars[0].holidays = frozenset({"2026-03-04"})
    _set(p, "start", "2026-03-02")
    assert _a(p).planned_finish == "2026-03-09"    # one working day later


def test_a_milestone_start_and_finish_stay_the_same_day():
    p = _proj(dur=0.0)
    _a(p).activity_type = "Start Milestone"
    _set(p, "start", "2026-03-02")
    assert _a(p).planned_start == _a(p).planned_finish == "2026-03-02"


def test_a_ten_hour_calendar_still_measures_in_days():
    """Duration is stored in hours; a 10h/day calendar must not skew the span."""
    p = _proj(dur=5.0, hpd=10.0)
    _set(p, "start", "2026-03-02")
    assert _a(p).planned_finish == "2026-03-06"


# ── setting a finish recalculates the duration ────────────────────────────────

def test_setting_a_finish_recalculates_the_duration_inclusively():
    p = _proj(dur=5.0, start="2026-03-02", finish="2026-03-06")
    _set(p, "finish", "2026-03-13")                # Mon 03-02 .. Fri 03-13
    assert _dur(p) == 10.0
    assert _a(p).planned_start == "2026-03-02"     # start did not move


def test_finishing_on_the_start_day_is_one_day_not_zero():
    p = _proj(dur=5.0, start="2026-03-02", finish="2026-03-06")
    _set(p, "finish", "2026-03-02")
    assert _dur(p) == 1.0


def test_a_finish_before_the_start_is_refused():
    p = _proj(dur=5.0, start="2026-03-02", finish="2026-03-06")
    ok, msg = _set(p, "finish", "2026-02-20")
    assert not ok and "before the start" in msg


# ── the two halves agree with each other, and with the scheduler ──────────────

def test_start_then_finish_round_trips_the_duration():
    p = _proj(dur=7.0)
    _set(p, "start", "2026-03-02")
    end = _a(p).planned_finish
    _set(p, "finish", end)
    assert _dur(p) == 7.0


def test_an_edited_row_survives_a_full_reschedule_unchanged():
    """What the grid shows after an edit is what the scheduler would produce.

    hold_unlinked_dates stays on: this activity has no predecessors, and the
    full F9 reflow deliberately drives unlinked work from the data date (the
    confirm dialog warns about exactly that). The point here is that the
    date arithmetic agrees, not that F9 leaves loose rows alone.
    """
    p = _proj(dur=6.0)
    _set(p, "start", "2026-03-02")
    s, f = _a(p).planned_start, _a(p).planned_finish
    compute_dates(p, apply_dates=True)
    assert (_a(p).planned_start, _a(p).planned_finish) == (s, f)


def test_a_successor_starts_the_working_day_after_its_predecessor_finishes():
    """The finish day is worked, so FS means 'the next day', not 'the same day'."""
    p = _proj(dur=5.0)
    p.activities.append(Activity(
        uid="a2", activity_id="A1010", name="Next", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=5 * 8, remaining_duration=5 * 8,
        planned_start="2026-03-09", planned_finish="2026-03-13"))
    p.relations = [Relation(uid="r", predecessor_uid="a1", successor_uid="a2",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    _set(p, "start", "2026-03-02")
    compute_dates(p, apply_dates=True)
    pred, succ = p.activities[0], p.activities[1]
    assert pred.planned_finish == "2026-03-06"     # Friday
    assert succ.planned_start == "2026-03-09"      # the following Monday


def test_a_lag_of_two_days_adds_two_working_days_to_the_gap():
    p = _proj(dur=5.0)
    p.activities.append(Activity(
        uid="a2", activity_id="A1010", name="Next", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=5 * 8, remaining_duration=5 * 8,
        planned_start="2026-03-09", planned_finish="2026-03-13"))
    p.relations = [Relation(uid="r", predecessor_uid="a1", successor_uid="a2",
                            type="Finish to Start", lag=2 * 8.0)]
    p.build_lookups()
    _set(p, "start", "2026-03-02")
    compute_dates(p, apply_dates=True)
    assert p.activities[1].planned_start == "2026-03-11"   # Mon + 2 working days


def test_back_to_back_work_reads_as_zero_implied_lag():
    """What the advisor calls 'confirms' — the dates already behave as the tie."""
    from engine.logic_advisor import implied_lag
    p = _proj(dur=5.0, start="2026-03-02", finish="2026-03-06")
    p.activities.append(Activity(
        uid="a2", activity_id="A1010", name="Next", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=5 * 8, remaining_duration=5 * 8,
        planned_start="2026-03-09", planned_finish="2026-03-13"))
    p.build_lookups()
    assert implied_lag(p, p.activities[0], p.activities[1]) == 0
