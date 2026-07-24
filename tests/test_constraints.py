"""
test_constraints.py — Pinned dates: a constraint must survive a schedule run,
and clearing it must hand the activity back to the logic.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation, compute_dates)
from engine.edit_engine import apply_command


def _chain():
    """A1000 -> A1010, both 5d, starting Mon 5 Jan 2026."""
    p = Project(uid="1", name="c", id="C", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="10", name="W", code="W")]
    p.activities = [
        Activity(uid="100", activity_id="A1000", name="First", wbs_uid="10",
                 calendar_uid="1", planned_duration=40.0, remaining_duration=40.0),
        Activity(uid="101", activity_id="A1010", name="Second", wbs_uid="10",
                 calendar_uid="1", planned_duration=40.0, remaining_duration=40.0),
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="100", successor_uid="101")]
    p.build_lookups()
    compute_dates(p)
    return p


def _act(p, aid):
    return p.get_activity(activity_id=aid)


def test_start_on_constraint_holds_through_a_schedule_run():
    p = _chain()
    natural = _act(p, "A1010").planned_start
    ok, _ = apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                              "constraint_type": "Start On",
                              "constraint_date": "2026-03-02"})
    assert ok
    compute_dates(p)                       # the Schedule button
    assert _act(p, "A1010").planned_start == "2026-03-02"
    assert _act(p, "A1010").planned_start != natural
    # re-running must not drift it
    compute_dates(p)
    assert _act(p, "A1010").planned_start == "2026-03-02"


def test_pinned_activity_pushes_its_successors():
    p = _chain()
    p.relations.append(Relation(uid="r2", predecessor_uid="101", successor_uid="101"))
    p.relations.pop()                      # keep the simple two-activity chain
    apply_command(p, {"action": "set_constraint", "activity_id": "A1000",
                      "constraint_type": "Start On", "constraint_date": "2026-06-01"})
    compute_dates(p)
    assert _act(p, "A1000").planned_start == "2026-06-01"
    # the successor follows the pinned predecessor rather than the old dates
    assert _act(p, "A1010").planned_start >= _act(p, "A1000").planned_finish


def test_clearing_a_constraint_returns_the_activity_to_the_logic():
    p = _chain()
    natural = _act(p, "A1010").planned_start
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On", "constraint_date": "2026-03-02"})
    compute_dates(p)
    assert _act(p, "A1010").planned_start == "2026-03-02"

    ok, _ = apply_command(p, {"action": "clear_constraint", "activity_id": "A1010"})
    assert ok
    compute_dates(p)
    assert _act(p, "A1010").constraint_type is None
    assert _act(p, "A1010").planned_start == natural


def test_pinned_date_never_lands_on_a_non_working_day():
    """A constraint on a Saturday is pulled onto the next working day."""
    p = _chain()
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On",
                      "constraint_date": "2026-03-07"})   # a Saturday
    compute_dates(p)
    import datetime as dt
    assert dt.date.fromisoformat(_act(p, "A1010").planned_start).weekday() < 5
