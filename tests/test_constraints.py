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


def test_start_on_or_before_pulls_an_earlier_date_but_not_a_later_one():
    """'Start On Or Before' is a soft cap: it pulls the start back if the
    natural CPM date is later than the constraint, but a constraint date that
    is already later than the natural date changes nothing."""
    p = _chain()
    natural = _act(p, "A1010").planned_start
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On Or Before",
                      "constraint_date": "2026-06-01"})   # later than natural
    compute_dates(p)
    assert _act(p, "A1010").planned_start == natural

    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On Or Before",
                      "constraint_date": "2026-01-05"})   # earlier than natural
    compute_dates(p)
    assert _act(p, "A1010").planned_start == "2026-01-05"


def test_finish_on_or_after_pushes_a_later_date_but_not_an_earlier_one():
    """'Finish On Or After' is a soft floor: it pushes the finish out if the
    natural CPM date is earlier than the constraint, but a constraint date
    that is already earlier than the natural date changes nothing."""
    p = _chain()
    natural = _act(p, "A1010").planned_finish
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Finish On Or After",
                      "constraint_date": "2026-01-05"})   # earlier than natural
    compute_dates(p)
    assert _act(p, "A1010").planned_finish == natural

    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Finish On Or After",
                      "constraint_date": "2026-05-01"})   # later than natural
    compute_dates(p)
    assert _act(p, "A1010").planned_finish == "2026-05-01"


def test_pinned_date_never_lands_on_a_non_working_day():
    """A constraint on a Saturday is pulled onto the next working day."""
    p = _chain()
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On",
                      "constraint_date": "2026-03-07"})   # a Saturday
    compute_dates(p)
    import datetime as dt
    assert dt.date.fromisoformat(_act(p, "A1010").planned_start).weekday() < 5


# ── Held dates, origin fallback, actual-date edits ───────────────────────────

def test_unlinked_activity_keeps_its_planned_date_on_implicit_recompute():
    """An imported/pasted activity has no logic links. The recompute that runs
    after every edit must not drag it back to the project origin — its
    existing planned date is its anchor. Only an explicit full reflow
    (hold_unlinked_dates=False, the F9 path) collapses it to the origin."""
    p = _chain()
    p.relations = []                        # both activities now unlinked
    a = _act(p, "A1010")
    a.planned_start = "2026-04-06"          # as if pasted with this date
    compute_dates(p)
    assert a.planned_start == "2026-04-06"  # held, not dragged to Jan 5
    compute_dates(p)                        # and stable across repeated runs
    assert a.planned_start == "2026-04-06"

    compute_dates(p, hold_unlinked_dates=False)   # explicit F9 reflow
    assert a.planned_start == "2026-01-05"


def test_compute_dates_derives_an_origin_when_the_project_has_none():
    """Paste/PDF imports and old saved sessions have neither a planned start
    nor a data date. compute_dates used to bail out entirely, leaving blank
    date columns — it must fall back to the earliest known date instead."""
    p = _chain()
    p.planned_start = None
    p.data_date = None
    for a in p.activities:
        a.planned_start = a.planned_finish = None
        a.early_start = a.early_finish = None
    compute_dates(p)
    assert all(a.planned_start for a in p.activities)
    assert p.planned_start                   # origin persisted on the project


def test_editing_a_date_on_a_started_activity_moves_the_actual():
    """Constraints can't move an In Progress row — CPM anchors it to its
    actual start. The grid's date edit must go through set_actual_date."""
    p = _chain()
    a = _act(p, "A1010")
    a.status = "In Progress"
    a.actual_start = "2026-02-02"
    a.percent_complete = 50.0
    compute_dates(p)
    assert a.planned_start == "2026-02-02"

    # a constraint alone cannot move it (this is the trap the UI avoids)
    apply_command(p, {"action": "set_constraint", "activity_id": "A1010",
                      "constraint_type": "Start On", "constraint_date": "2026-03-02"})
    compute_dates(p)
    assert a.planned_start == "2026-02-02"

    ok, _ = apply_command(p, {"action": "set_actual_date", "activity_id": "A1010",
                              "field": "start", "date": "2026-03-02"})
    assert ok
    compute_dates(p)
    assert a.actual_start == "2026-03-02"
    assert a.planned_start == "2026-03-02"


def test_setting_an_actual_finish_completes_the_activity():
    p = _chain()
    ok, _ = apply_command(p, {"action": "set_actual_date", "activity_id": "A1000",
                              "field": "finish", "date": "2026-02-13"})
    assert ok
    a = _act(p, "A1000")
    assert a.status == "Completed" and a.percent_complete == 100.0
    assert a.actual_finish == "2026-02-13" and a.actual_start
    compute_dates(p)
    assert a.planned_finish == "2026-02-13"


def test_clearing_an_actual_start_returns_the_row_to_not_started():
    p = _chain()
    apply_command(p, {"action": "set_actual_date", "activity_id": "A1000",
                      "field": "start", "date": "2026-02-02"})
    assert _act(p, "A1000").status == "In Progress"
    apply_command(p, {"action": "set_actual_date", "activity_id": "A1000",
                      "field": "start", "date": ""})
    a = _act(p, "A1000")
    assert a.status == "Not Started" and a.actual_start is None


# ── Duplicate / relationship / type / progress actions ───────────────────────

def _branch():
    p = Project(uid="1", name="t", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="10", name="Electrical", code="E"),
                   WBSNode(uid="11", name="ER 209", code="ER209", parent_uid="10"),
                   WBSNode(uid="12", name="Gear", code="G", parent_uid="11")]
    p.activities = [
        Activity(uid="100", activity_id="A1000", name="Rough-in", wbs_uid="11",
                 calendar_uid="1", planned_duration=40.0),
        Activity(uid="101", activity_id="A1010", name="Terminate", wbs_uid="11",
                 calendar_uid="1", planned_duration=24.0),
        Activity(uid="102", activity_id="A1020", name="Set Gear", wbs_uid="12",
                 calendar_uid="1", planned_duration=16.0),
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="100", successor_uid="101")]
    p.build_lookups()
    return p


def test_duplicate_wbs_copies_branch_activities_and_internal_logic():
    p = _branch()
    ok, _ = apply_command(p, {"action": "duplicate_wbs", "wbs_name": "ER 209",
                              "new_name": "ER 210"})
    assert ok
    names = [w.name for w in p.wbs_nodes]
    assert "ER 210" in names
    assert names.count("Gear") == 2                 # nested folder came along
    assert len(p.activities) == 6                   # 3 originals + 3 copies
    assert len({a.activity_id for a in p.activities}) == 6   # ids are unique
    assert len(p.relations) == 2                    # internal logic duplicated


def test_duplicate_wbs_can_make_several_copies():
    p = _branch()
    apply_command(p, {"action": "duplicate_wbs", "wbs_name": "ER 209",
                      "new_name": "ER", "count": 3})
    assert sum(1 for w in p.wbs_nodes if w.name.startswith("ER ")) >= 4


def test_update_relation_changes_type_and_lag():
    p = _branch()
    ok, _ = apply_command(p, {"action": "update_relation", "predecessor_id": "A1000",
                              "successor_id": "A1010", "type": "ss", "lag_days": 3})
    assert ok
    rel = p.relations[0]
    assert rel.type == "Start to Start" and rel.lag == 24.0


def test_update_relation_rejects_a_missing_link():
    p = _branch()
    ok, msg = apply_command(p, {"action": "update_relation", "predecessor_id": "A1010",
                                "successor_id": "A1000", "type": "ss"})
    assert not ok and "No relationship" in msg


def test_changing_type_to_milestone_zeroes_the_duration():
    p = _branch()
    ok, _ = apply_command(p, {"action": "update_activity_type", "activity_id": "A1000",
                              "activity_type": "Finish Milestone"})
    assert ok
    a = p.get_activity(activity_id="A1000")
    assert a.activity_type == "Finish Milestone" and a.planned_duration == 0.0


def test_progress_keeps_status_consistent():
    p = _branch()
    for pct, status in ((0, "Not Started"), (40, "In Progress"), (100, "Completed")):
        apply_command(p, {"action": "update_progress", "activity_id": "A1000",
                          "percent_complete": pct})
        assert p.get_activity(activity_id="A1000").status == status
