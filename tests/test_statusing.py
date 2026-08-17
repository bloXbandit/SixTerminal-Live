"""
test_statusing.py — marking work started, complete, or back to not-started.

P6 defines status by which ACTUAL dates exist, so setting a status without the
dates (or a date without the status) leaves a row contradicting itself. The
grid had no way to change status at all, which made the weekly update — the
single most common thing a scheduler does — impossible here.

  Not Started   no actuals, 0%, full duration remaining
  In Progress   an actual START and NO actual finish, so the finish is still a
                forecast. This is the state a running activity sits in all
                week, and it was the one with no route to it.
  Completed     both actuals, 100%, nothing remaining
"""

import os
import sys
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.edit_engine import apply_command
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _proj(data_date="2026-03-20"):
    p = Project(uid="p", name="P", id="P", data_date=data_date,
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W", parent_uid=None)]
    p.activities = [Activity(
        uid="a1", activity_id="A1000", name="Rough-in", wbs_uid="w", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=40.0, remaining_duration=40.0,
        planned_start="2026-03-02", planned_finish="2026-03-06")]
    p.build_lookups()
    return p


def _a(p):
    return p.activities[0]


def _set(p, status, **kw):
    return apply_command(p, dict({"action": "set_progress", "activity_id": "A1000",
                                  "status": status}, **kw))


# ── in progress: started, not finished ────────────────────────────────────────

def test_marking_started_sets_an_actual_start_and_no_actual_finish():
    p = _proj()
    ok, msg = _set(p, "in progress")
    assert ok
    a = _a(p)
    assert a.status == "In Progress"
    assert a.actual_start and not a.actual_finish
    assert "2026-03-02" in msg


def test_the_actual_start_defaults_to_the_planned_start():
    p = _proj()
    _set(p, "in progress")
    assert _a(p).actual_start == "2026-03-02"


def test_a_given_actual_start_is_used():
    p = _proj()
    _set(p, "in progress", actual_start="2026-03-04")
    assert _a(p).actual_start == "2026-03-04"


def test_work_not_yet_scheduled_starts_at_the_data_date_not_the_future():
    """You cannot have already started work whose planned start is next month."""
    p = _proj(data_date="2026-01-10")
    _set(p, "in progress")
    assert _a(p).actual_start == "2026-01-10"


def test_in_progress_leaves_some_duration_remaining():
    p = _proj()
    _set(p, "in progress")
    a = _a(p)
    assert 0 < a.percent_complete < 100
    assert 0 < a.remaining_duration < a.planned_duration


def test_a_percent_can_be_given():
    p = _proj()
    _set(p, "in progress", percent_complete=80)
    a = _a(p)
    assert a.percent_complete == 80
    assert abs(a.remaining_duration - a.planned_duration * 0.2) < 1e-6


def test_an_existing_percent_is_kept():
    p = _proj()
    _a(p).percent_complete = 25.0
    _set(p, "in progress")
    assert _a(p).percent_complete == 25.0


def test_a_percent_of_one_hundred_is_held_below_complete():
    """In Progress means not finished — 100% would contradict the status."""
    p = _proj()
    _set(p, "in progress", percent_complete=100)
    assert _a(p).percent_complete < 100
    assert _a(p).status == "In Progress"


# ── completed ─────────────────────────────────────────────────────────────────

def test_marking_complete_sets_both_actuals():
    p = _proj()
    _set(p, "completed")
    a = _a(p)
    assert a.status == "Completed"
    assert a.actual_start == "2026-03-02" and a.actual_finish == "2026-03-06"
    assert a.percent_complete == 100.0 and a.remaining_duration == 0.0


def test_completing_a_running_activity_keeps_the_start_it_already_had():
    p = _proj()
    _set(p, "in progress", actual_start="2026-03-03")
    _set(p, "completed")
    assert _a(p).actual_start == "2026-03-03"


def test_both_actual_dates_can_be_given():
    p = _proj()
    _set(p, "completed", actual_start="2026-03-03", actual_finish="2026-03-11")
    a = _a(p)
    assert (a.actual_start, a.actual_finish) == ("2026-03-03", "2026-03-11")


def test_a_finish_before_the_start_is_refused():
    p = _proj()
    ok, msg = _set(p, "completed", actual_start="2026-03-10",
                   actual_finish="2026-03-02")
    assert not ok and "before" in msg


# ── back to not started ───────────────────────────────────────────────────────

def test_reopening_clears_both_actuals():
    p = _proj()
    _set(p, "completed")
    _set(p, "not started")
    a = _a(p)
    assert a.status == "Not Started"
    assert a.actual_start is None and a.actual_finish is None
    assert a.percent_complete == 0.0
    assert a.remaining_duration == a.planned_duration


def test_reopening_leaves_the_forecast_dates_alone():
    """Clearing actuals hands the row back to the scheduler, it does not wipe it."""
    p = _proj()
    _set(p, "completed")
    _set(p, "not started")
    a = _a(p)
    assert (a.planned_start, a.planned_finish) == ("2026-03-02", "2026-03-06")


# ── input handling ────────────────────────────────────────────────────────────

def test_the_usual_words_are_understood():
    for word in ("started", "in progress", "wip", "active"):
        p = _proj()
        ok, _ = _set(p, word)
        assert ok and _a(p).status == "In Progress", word
    for word in ("complete", "completed", "done", "finished"):
        p = _proj()
        ok, _ = _set(p, word)
        assert ok and _a(p).status == "Completed", word


def test_an_unknown_status_is_refused():
    p = _proj()
    ok, msg = _set(p, "nearly there")
    assert not ok and "must be" in msg


def test_a_bad_date_is_reported():
    p = _proj()
    ok, msg = _set(p, "in progress", actual_start="not-a-date")
    assert not ok and "valid date" in msg


def test_an_unknown_activity_suggests_real_ones():
    p = _proj()
    ok, msg = apply_command(p, {"action": "set_progress", "activity_id": "A9999",
                                "status": "completed"})
    assert not ok and "A1000" in msg


# ── the scheduler respects what statusing wrote ───────────────────────────────

def test_a_completed_row_keeps_its_actual_dates_through_a_reschedule():
    from engine.schedule_model import compute_dates
    p = _proj()
    _set(p, "completed", actual_start="2026-03-03", actual_finish="2026-03-11")
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    a = _a(p)
    assert a.planned_start == "2026-03-03" and a.planned_finish == "2026-03-11"


def test_a_started_row_is_anchored_to_its_actual_start():
    from engine.schedule_model import compute_dates
    p = _proj()
    _set(p, "in progress", actual_start="2026-03-03")
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    assert _a(p).planned_start == "2026-03-03"
