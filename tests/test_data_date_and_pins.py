"""
test_data_date_and_pins.py — two rules P6 applies that the CPM was missing.

Both were found by auditing a real 2,729-activity export whose stored dates
disagreed with its own constraints on 150 not-started activities. Neither was
bad data: 97 were pinned to a date before the data date, and 51 of the
remaining 53 were driven past their pin by their own predecessors. P6 had
scheduled all of them correctly and left the stale pins in place.

  1. Remaining work cannot be scheduled before the data date. A pin dated in
     the past does not drag not-started work back there.
  2. "Start On" / "Finish On" raise a date, they do not pull it in front of
     the logic driving it. Only the Mandatory ("Must") forms override logic.
     A slip past a pin is reported as negative float by the backward pass,
     not scheduled away.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import (Project, Activity, WBSNode, Relation, Calendar,
                                   compute_dates)


def _proj(data_date="2026-03-02"):
    p = Project(uid="p", name="P", id="P", data_date=data_date,
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=8.0)]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None)]
    p.activities = []
    return p


def _act(p, aid, uid, start, dur=5.0, ct=None, cd=None, status="Not Started",
         astart=None, afinish=None):
    a = Activity(uid=uid, activity_id=aid, name=aid, wbs_uid="w", calendar_uid="1",
                 activity_type="Task Dependent", status=status,
                 planned_duration=dur * 8, remaining_duration=dur * 8,
                 planned_start=start, planned_finish=start,
                 constraint_type=ct, constraint_date=cd,
                 actual_start=astart, actual_finish=afinish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _by(p, aid):
    return next(a for a in p.activities if a.activity_id == aid)


# ── rule 1: the data-date floor ───────────────────────────────────────────────

def test_a_past_dated_pin_cannot_schedule_work_before_the_data_date():
    p = _proj(data_date="2026-03-02")
    _act(p, "A1", "a1", "2026-03-02", ct="Start On", cd="2026-01-05")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-03-02"      # floored, not 01-05


def test_an_unpinned_row_with_a_stale_past_date_is_floored_too():
    p = _proj(data_date="2026-03-02")
    _act(p, "A1", "a1", "2025-11-10")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-03-02"


def test_the_floor_lands_on_the_next_working_day():
    p = _proj(data_date="2026-03-07")                       # a Saturday
    _act(p, "A1", "a1", "2026-01-05", ct="Start On", cd="2026-01-05")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-03-09"       # Monday


def test_started_work_keeps_its_actual_start_in_the_past():
    """The floor is for remaining work. An actual date is a fact."""
    p = _proj(data_date="2026-03-02")
    _act(p, "A1", "a1", "2026-01-05", status="In Progress", astart="2026-01-05")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-01-05"


def test_completed_work_keeps_its_actual_dates_in_the_past():
    p = _proj(data_date="2026-03-02")
    _act(p, "A1", "a1", "2026-01-05", status="Completed",
         astart="2026-01-05", afinish="2026-01-09")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-01-05"


def test_a_future_date_is_untouched_by_the_floor():
    p = _proj(data_date="2026-03-02")
    _act(p, "A1", "a1", "2026-06-01")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-06-01"


def test_no_data_date_means_no_floor():
    p = Project(uid="p", name="P", id="P", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=8.0)]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W", parent_uid=None)]
    p.activities = []
    _act(p, "A1", "a1", "2026-01-05")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-01-05"


# ── rule 2: a pin raises a date, it does not outrank logic ───────────────────

def _linked(ct, cd, pred_finish="2026-06-01"):
    """Pred runs to `pred_finish`; the successor carries the pin under test."""
    p = _proj(data_date="2026-01-05")
    _act(p, "PRED", "a0", "2026-05-25", dur=5.0)
    _act(p, "SUCC", "a1", "2026-06-02", dur=5.0, ct=ct, cd=cd)
    p.relations = [Relation(uid="r", predecessor_uid="a0", successor_uid="a1",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    compute_dates(p)
    return p


def test_start_on_does_not_pull_a_row_in_front_of_its_predecessor():
    p = _linked("Start On", "2026-02-02")
    succ, pred = _by(p, "SUCC"), _by(p, "PRED")
    assert succ.planned_start >= pred.planned_finish    # logic still holds
    assert succ.planned_start != "2026-02-02"           # the pin did not win


def test_missing_a_start_on_pin_shows_as_negative_float():
    """The slip is reported, not scheduled away — that is the point."""
    p = _linked("Start On", "2026-02-02")
    assert _by(p, "SUCC").total_float < 0


def test_start_on_still_pushes_a_row_out_when_logic_allows_earlier():
    """A pin is a floor: it moves work later, it just cannot move it earlier."""
    p = _linked("Start On", "2026-09-01")
    assert _by(p, "SUCC").planned_start == "2026-09-01"


def test_mandatory_start_does_override_logic():
    p = _linked("Mandatory Start", "2026-02-02")
    assert _by(p, "SUCC").planned_start == "2026-02-02"


def test_must_start_on_does_override_logic():
    p = _linked("Must Start On", "2026-02-02")
    assert _by(p, "SUCC").planned_start == "2026-02-02"


def test_finish_on_does_not_pull_a_row_in_front_of_its_predecessor():
    p = _linked("Finish On", "2026-02-06")
    succ, pred = _by(p, "SUCC"), _by(p, "PRED")
    assert succ.planned_start >= pred.planned_finish
    assert succ.planned_finish != "2026-02-06"


def test_mandatory_finish_still_back_computes_the_start():
    p = _linked("Mandatory Finish", "2026-02-06")
    assert _by(p, "SUCC").planned_finish == "2026-02-06"


def test_an_unlinked_pinned_row_still_sits_on_its_pin():
    """Nothing drives it, so the pin is the date."""
    p = _proj(data_date="2026-01-05")
    _act(p, "A1", "a1", "2026-01-05", ct="Start On", cd="2026-07-01")
    compute_dates(p)
    assert _by(p, "A1").planned_start == "2026-07-01"
