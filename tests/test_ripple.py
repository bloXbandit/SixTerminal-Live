"""
test_ripple.py — reschedule one activity's path, leave the rest of the job alone.

There were only two speeds. Type a date and nothing moves, so the logic
downstream goes on saying something the schedule no longer supports. Or press
Schedule and everything moves — on the reference file a single actualisation
would drag 1,971 unrelated activities, because a global reflow also pulls
every unlinked row to the data date.

Neither is what statusing an activity means. This is the middle: the change
flows down the work that DEPENDS on it and touches nothing else.

The design subtlety worth protecting: the CPM must run GLOBALLY, because an
activity's dates depend on the whole network above it and a fragment would
compute different numbers from the real schedule. It is the WRITE that is
scoped. Everything here is about that separation holding.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import ripple
from engine.edit_engine import apply_command
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p():
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, start, finish, dur=5, status="Not Started"):
    a = Activity(uid=uid, activity_id=uid, name=f"Work {uid}", wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status=status, planned_duration=dur * 8,
                 remaining_duration=dur * 8, planned_start=start,
                 planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start",
                                lag=0.0))
    p.build_lookups()


def _chain_and_bystander():
    """A -> B -> C is one chain. X is unrelated work with no logic at all —
    exactly the kind of row a full Schedule run would drag to the data date."""
    p = _p()
    _act(p, "A", "2026-02-02", "2026-02-06")
    _act(p, "B", "2026-02-09", "2026-02-13")
    _act(p, "C", "2026-02-16", "2026-02-20")
    _rel(p, "A", "B")
    _rel(p, "B", "C")
    _act(p, "X", "2026-09-07", "2026-09-11")
    return p


# ── what the path is ─────────────────────────────────────────────────────────

def test_the_path_is_the_activity_and_everything_downstream():
    p = _chain_and_bystander()
    a = p.get_activity(activity_id="A")
    assert ripple.downstream(p, a.uid) == {"A", "B", "C"}


def test_predecessors_are_not_on_the_path_by_default():
    """Moving an activity's start does not move what came before it —
    including them would let an actualisation rewrite history."""
    p = _chain_and_bystander()
    b = p.get_activity(activity_id="B")
    assert ripple.downstream(p, b.uid) == {"B", "C"}


def test_a_cycle_does_not_hang_the_walk():
    p = _chain_and_bystander()
    _rel(p, "C", "A")
    assert ripple.downstream(p, "A") == {"A", "B", "C"}


# ── the guarantee: off-path work is not touched ──────────────────────────────

def test_unrelated_work_is_left_exactly_as_it_was():
    """The whole reason this exists rather than pressing Schedule."""
    p = _chain_and_bystander()
    before = {a.activity_id: (a.planned_start, a.planned_finish)
              for a in p.activities}
    ripple.apply_ripple(p, "A", {"actual_start": "2026-02-16"})
    x = p.get_activity(activity_id="X")
    assert (x.planned_start, x.planned_finish) == before["X"]


def test_only_path_rows_are_written():
    p = _chain_and_bystander()
    before = {a.activity_id: (a.planned_start, a.planned_finish)
              for a in p.activities}
    ripple.apply_ripple(p, "B", {"actual_start": "2026-03-02"})
    changed = {aid for aid, v in before.items()
               if (p.get_activity(activity_id=aid).planned_start,
                   p.get_activity(activity_id=aid).planned_finish) != v}
    assert changed <= {"B", "C"}, f"wrote outside the path: {changed}"


def test_an_unlinked_bystander_is_not_even_threatened():
    """The ripple holds unlinked dates on purpose. A full Schedule run drives
    work with no predecessor to the data date — on the reference file that is
    592 activities — and a targeted reflow must not do that to a row the user
    never touched."""
    p = _chain_and_bystander()
    before = p.get_activity(activity_id="X").planned_start
    ripple.apply_ripple(p, "A", {"actual_start": "2026-02-16"})
    assert p.get_activity(activity_id="X").planned_start == before


def test_it_reports_what_a_full_schedule_would_have_dragged():
    """Naming the number is the point — it is the difference between this and
    pressing Schedule, and the user should see it. Here a SECOND chain is
    dated so the reflow moves it, but it is not downstream of A."""
    p = _chain_and_bystander()
    _act(p, "M", "2026-04-06", "2026-04-10")
    _act(p, "N", "2026-01-05", "2026-01-09")     # dated before its driver
    _rel(p, "M", "N")                            # so the pass has to move N
    r = ripple.simulate(p, "A", {"actual_start": "2026-02-16"})
    assert r["would_move_off_path"] >= 1
    assert "N" not in {m["activity_id"] for m in r["movers"]}, (
        "N is not downstream of A and must not be reported as moving")


def test_simulating_changes_nothing():
    p = _chain_and_bystander()
    before = [(a.activity_id, a.planned_start, a.planned_finish,
               a.actual_start, a.status) for a in p.activities]
    ripple.simulate(p, "A", {"actual_start": "2026-02-16"})
    ripple.report(p, "A", {"actual_start": "2026-02-16"})
    assert [(a.activity_id, a.planned_start, a.planned_finish,
             a.actual_start, a.status) for a in p.activities] == before


# ── the change actually lands ────────────────────────────────────────────────

def test_an_actual_start_anchors_the_activity_to_that_date():
    p = _chain_and_bystander()
    ripple.apply_ripple(p, "A", {"actual_start": "2026-02-16"})
    a = p.get_activity(activity_id="A")
    assert a.actual_start == "2026-02-16"
    assert a.planned_start == "2026-02-16"


def test_the_downstream_chain_moves_with_it():
    p = _chain_and_bystander()
    before_c = p.get_activity(activity_id="C").planned_start
    ripple.apply_ripple(p, "A", {"actual_start": "2026-02-16"})
    assert p.get_activity(activity_id="C").planned_start > before_c


def test_a_typed_planned_start_survives_on_a_linked_row():
    """Without a pin the predecessors drive it straight back, so the ripple
    would report a date the user never asked for and ignore the one they did."""
    p = _chain_and_bystander()
    ripple.apply_ripple(p, "B", {"planned_start": "2026-06-01"})
    assert p.get_activity(activity_id="B").planned_start == "2026-06-01"


def test_an_activity_with_no_successors_moves_only_itself():
    p = _chain_and_bystander()
    before = {a.activity_id: a.planned_start for a in p.activities}
    ripple.apply_ripple(p, "C", {"actual_start": "2026-03-02"})
    moved = {aid for aid, v in before.items()
             if p.get_activity(activity_id=aid).planned_start != v}
    assert moved == {"C"}


def test_predecessors_can_be_included_when_asked():
    p = _chain_and_bystander()
    r = ripple.simulate(p, "C", {"actual_start": "2026-03-02"},
                        include_predecessors=True)
    assert r["path_size"] == 3


# ── errors and wiring ────────────────────────────────────────────────────────

def test_an_unknown_activity_is_refused():
    assert "No activity" in ripple.simulate(_chain_and_bystander(),
                                            "NOPE", {})["error"]


def test_a_change_the_engine_rejects_is_reported_not_half_applied():
    p = _chain_and_bystander()
    before = [(a.activity_id, a.planned_start) for a in p.activities]
    r = ripple.simulate(p, "A", {"actual_finish": "not-a-date"})
    assert r.get("error")
    assert [(a.activity_id, a.planned_start) for a in p.activities] == before


def test_the_preview_action_is_advisory_and_writes_nothing():
    from engine.edit_engine import is_advisory
    assert is_advisory("ripple_preview")
    p = _chain_and_bystander()
    before = [(a.activity_id, a.planned_start) for a in p.activities]
    ok, msg = apply_command(p, {"action": "ripple_preview", "activity_id": "A",
                                "actual_start": "2026-02-16"})
    assert ok and "RIPPLE from A" in msg
    assert [(a.activity_id, a.planned_start) for a in p.activities] == before


def test_the_ripple_action_applies_only_with_apply():
    p = _chain_and_bystander()
    before = p.get_activity(activity_id="A").planned_start
    ok, _ = apply_command(p, {"action": "ripple", "activity_id": "A",
                              "actual_start": "2026-02-16"})
    assert ok and p.get_activity(activity_id="A").planned_start == before

    ok, msg = apply_command(p, {"action": "ripple", "activity_id": "A",
                                "actual_start": "2026-02-16", "apply": True})
    assert ok
    assert p.get_activity(activity_id="A").planned_start == "2026-02-16"
    assert "Undo reverts" in msg


def test_float_is_refreshed_after_a_ripple():
    """The derived columns have to agree with the dates that just changed."""
    p = _chain_and_bystander()
    ripple.apply_ripple(p, "A", {"actual_start": "2026-02-16"})
    assert p.get_activity(activity_id="C").early_start is not None
