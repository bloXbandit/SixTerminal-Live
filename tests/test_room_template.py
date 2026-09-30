"""
test_room_template.py — one room's flow, taken from the job that has it right.

A contractor's printed schedule says what a generator room needs in fifteen
lines. That is the GC's view. It is not a schedule an electrical foreman can
work to, because the twenty-three separable pieces of electrical work in that
room — high steel, hangers, ladder tray, LBB to LBB, LBB to MSB, MEG/QC,
terminate — are not in it, and neither is which of them somebody else does.

So the flow is lifted off the schedule that has the detail. What these tests
are about is the two ways that goes wrong: moving dates that were supposed to
stay put, and inventing hours that are not real work.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import room_template as rt
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _ref():
    """A reference room: four steps, one of them somebody else's, with a gap."""
    p = Project(uid="1", name="Ref", id="REF-1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Ref", code="R"),
                   WBSNode(uid="own", name="Gen 315 - JER", code="G315J",
                           parent_uid="root"),
                   WBSNode(uid="wbo", name="Gen 315 - WBO", code="G315W",
                           parent_uid="root")]
    rows = [("A10", "Install High Steel (Gen 315)", "own", 24, "2026-03-02"),
            ("A20", "Install Hangers (Gen 315)", "own", 32, "2026-03-05"),
            ("A30", "Epoxy Floor **WBO (Gen 315)", "wbo", 40, "2026-06-01"),
            ("A40", "Set Generator (Gen 315)", "own", 8, "2026-06-08")]
    p.activities = []
    for i, (aid, name, folder, hrs, start) in enumerate(rows, start=1):
        p.activities.append(Activity(
            uid=f"r{i}", activity_id=aid, name=name, wbs_uid=folder,
            calendar_uid="1", planned_duration=hrs, remaining_duration=hrs,
            planned_start=start, planned_finish=start))
    p.relations = []
    p.build_lookups()
    return p


def _target(start="2026-09-01", finish="2026-11-30", status="Not Started",
            rooms=("315",)):
    """A parsed room: coarse activities, real dates, no logic."""
    p = Project(uid="2", name="Tgt", id="TGT-1", data_date="2026-02-03",
                planned_start="2026-02-03")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Tgt", code="T"),
                   WBSNode(uid="gens", name="Generator Rooms - Phase 1",
                           code="GR1", parent_uid="root")]
    p.activities = []
    for room in rooms:
        p.wbs_nodes.append(WBSNode(uid=f"rm{room}", name=f"Gen {room}",
                                   code=f"G{room}", parent_uid="gens"))
        for i, (s, f) in enumerate(((start, start), (finish, finish)), start=1):
            p.activities.append(Activity(
                uid=f"t{room}{i}", activity_id=f"MDC3.GEN.{room}.10{i}0",
                name=f"Overhead Electrical/FA - GEN {room}", wbs_uid=f"rm{room}",
                calendar_uid="1", status=status, planned_duration=40,
                remaining_duration=40, planned_start=s, planned_finish=f))
    p.relations = []
    p.build_lookups()
    return p


def _d(x):
    return dt.date.fromisoformat(str(x)[:10])


# ── reading the template off the reference ───────────────────────────────────

def test_the_room_number_is_stripped_so_the_flow_is_reusable():
    t = rt.extract(_ref(), "GEN")
    assert "Install High Steel" in [s["label"] for s in t["steps"]]
    assert not any("315" in s["label"] for s in t["steps"])


def test_whose_scope_each_step_is_comes_across():
    """A `**WBO` line that gets crew loaded against it is the reason this
    distinction is carried at all."""
    t = rt.extract(_ref(), "GEN")
    by = {s["label"]: s["wbo"] for s in t["steps"]}
    assert by["Epoxy Floor **WBO"] is True
    assert by["Install High Steel"] is False


def test_the_steps_come_back_in_order_with_their_offsets():
    t = rt.extract(_ref(), "GEN")
    offsets = [s["offset"] for s in t["steps"]]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0


def test_durations_are_carried_in_hours_unchanged():
    t = rt.extract(_ref(), "GEN")
    assert [s["hours"] for s in t["steps"]] == [24, 32, 40, 8]


# ── laying it into the target ────────────────────────────────────────────────

def test_the_room_starts_where_it_already_started():
    p = _target(start="2026-09-01")
    rt.apply_template(p, rt.extract(_ref(), "GEN"))
    acts = [a for a in p.activities if "GEN.315" in a.activity_id]
    assert min(_d(a.planned_start) for a in acts) == dt.date(2026, 9, 1)


def test_the_room_also_finishes_about_where_it_already_finished():
    """
    The measurement this default exists for. The reference room carries a
    three-month wait in the middle of it for somebody else's epoxy, so laid in
    unscaled its flow ran 44 days past the contractor's own finish on a median
    generator room, up to 72. Keeping close to the schedule being matched means
    the gaps give, not the dates.
    """
    p = _target(start="2026-09-01", finish="2026-11-30")
    rt.apply_template(p, rt.extract(_ref(), "GEN"), fit="window")
    acts = [a for a in p.activities if "GEN.315" in a.activity_id]
    moved = (max(_d(a.planned_finish) for a in acts) - dt.date(2026, 11, 30)).days
    assert abs(moved) <= 14, f"finish moved {moved} days"


def test_durations_are_never_stretched_to_fill_the_window():
    """Hours that are not real work content are worse than a date that moved,
    because the date is visible and the hours are not."""
    t = rt.extract(_ref(), "GEN")
    p = _target(start="2026-09-01", finish="2027-06-30")   # a very wide window
    rt.apply_template(p, t, fit="window")
    acts = [a for a in p.activities if "GEN.315" in a.activity_id]
    assert sorted(float(a.planned_duration) for a in acts) \
        == sorted(s["hours"] for s in t["steps"])


def test_the_scope_split_is_mirrored_into_folders():
    p = _target()
    rt.apply_template(p, rt.extract(_ref(), "GEN"))
    names = {n.name for n in p.wbs_nodes}
    assert "Gen 315 - JER" in names
    assert "Gen 315 - WBO" in names


def test_the_room_keeps_its_place_in_the_tree():
    """A transplant that also moves the room loses the one thing the rebuilt
    hierarchy got right."""
    p = _target()
    rt.apply_template(p, rt.extract(_ref(), "GEN"))
    by_uid = {n.uid: n for n in p.wbs_nodes}
    jer = next(n for n in p.wbs_nodes if n.name == "Gen 315 - JER")
    assert by_uid[jer.parent_uid].name == "Generator Rooms - Phase 1"


def test_the_coarse_activities_are_replaced_not_added_to():
    p = _target()
    t = rt.extract(_ref(), "GEN")
    rt.apply_template(p, t)
    acts = [a for a in p.activities if "GEN.315" in a.activity_id]
    assert len(acts) == len(t["steps"])
    assert not any("Overhead Electrical/FA" in (a.name or "") for a in acts)


def test_the_new_activities_carry_the_room_in_their_name():
    p = _target()
    rt.apply_template(p, rt.extract(_ref(), "GEN"))
    acts = [a for a in p.activities if "GEN.315" in a.activity_id]
    assert all("315" in (a.name or "") for a in acts)


# ── what it refuses to do ────────────────────────────────────────────────────

def test_a_room_with_progress_on_it_is_left_alone():
    """
    An activity somebody has started is a fact about the job, and the flow it
    belongs to cannot be swapped out from under it without throwing that away.
    """
    p = _target(status="In Progress")
    r = rt.apply_template(p, rt.extract(_ref(), "GEN"))
    assert r["counts"]["ready"] == 0
    assert "started" in r["blocked"]["315"]
    assert any("Overhead Electrical/FA" in (a.name or "") for a in p.activities)


def test_every_room_is_either_rewritten_or_explained():
    p = _target(rooms=("315", "316"))
    r = rt.apply_template(p, rt.extract(_ref(), "GEN"))
    c = r["counts"]
    assert c["ready"] + c["blocked"] == c["rooms"] == 2


def test_natural_fit_keeps_the_reference_rhythm_exactly():
    """Available on purpose: right when the two schedules are paced alike."""
    t = rt.extract(_ref(), "GEN")
    p = _target(start="2026-09-01", finish="2026-11-30")
    rt.apply_template(p, t, fit="natural")
    acts = sorted((a for a in p.activities if "GEN.315" in a.activity_id),
                  key=lambda a: _d(a.planned_start))
    span = (_d(acts[-1].planned_start) - _d(acts[0].planned_start)).days
    assert span > 60, "the reference gap was squeezed under 'natural'"


def test_an_unknown_room_kind_is_an_error_not_a_guess():
    with pytest.raises(KeyError):
        rt.extract(_ref(), "NOPE")


# ── the same three templates have to serve every sister job ──────────────────

def test_the_job_code_is_read_off_the_schedule_not_assumed():
    """
    A room full of MDC3 ids inside MDC-2 imports as activities nobody can
    find. The tool is used across sister jobs, so the prefix comes from the
    schedule being written rather than from a default.
    """
    p = _target()
    for a in p.activities:
        a.activity_id = a.activity_id.replace("MDC3", "MDC2")
    p.build_lookups()
    rt.apply_template(p, rt.extract(_ref(), "GEN"))
    made = [a for a in p.activities if a.activity_id.startswith("MDC2.GEN.315")]
    assert made, "found no rooms once the job code changed"
    assert not any("MDC3" in (a.activity_id or "") for a in p.activities)


def test_a_kind_with_no_rooms_in_this_job_is_simply_empty():
    """MDC-2 has no HV rooms at all. That is not an error."""
    p = _target()
    assert rt.rooms_in(p, "HV") == {}
