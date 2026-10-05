"""
test_logic_transplant.py — giving a dateless schedule its relationships back.

A schedule read out of a printed Gantt has every activity, every date and no
logic, because a bar chart shows bars and not what drives them. It looks fine
and it is inert: nothing moves when something slips, and pressing F9 collapses
it to the data date.

The logic comes from two places — the dates the print already asserts, and the
sister job's own sequencing patterns. What these tests are mostly about is the
one invariant that matters: adding a relationship must not move a date. If the
tie is consistent with the printed schedule, rescheduling is a no-op. Anything
that moves is a tie that disagrees with the print, and that has to be visible
rather than shipped.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import logic_transplant as lt
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode, compute_dates)


def _job(rows, folders=("Room",), data_date="2026-01-05", relations=()):
    """rows: (activity_id, name, folder, start, finish)."""
    p = Project(uid="1", name="J", id="J-1", data_date=data_date,
                planned_start=data_date)
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="J", code="J")]
    for i, f in enumerate(folders, start=1):
        p.wbs_nodes.append(WBSNode(uid=f"f{i}", name=f, code=f"F{i}",
                                   parent_uid="root"))
    folder_uid = {f: f"f{i}" for i, f in enumerate(folders, start=1)}
    p.activities = []
    for i, (aid, name, folder, s, f) in enumerate(rows, start=1):
        days = (dt.date.fromisoformat(f) - dt.date.fromisoformat(s)).days + 1
        p.activities.append(Activity(
            uid=f"u{i}", activity_id=aid, name=name,
            wbs_uid=folder_uid[folder], calendar_uid="1", status="Not Started",
            planned_duration=max(8, days * 8), remaining_duration=max(8, days * 8),
            planned_start=s, planned_finish=f))
    p.relations = list(relations)
    p.build_lookups()
    return p


def _source():
    """A sister job with real logic to learn from."""
    rows = [("S.1000", "Install High Steel", "Room A", "2026-02-02", "2026-02-04"),
            ("S.1010", "Install Hangers", "Room A", "2026-02-05", "2026-02-10"),
            ("S.2000", "Install High Steel", "Room B", "2026-03-02", "2026-03-04"),
            ("S.2010", "Install Hangers", "Room B", "2026-03-05", "2026-03-10")]
    p = _job(rows, folders=("Room A", "Room B"))
    p.relations = [
        Relation(uid="r1", predecessor_uid="u1", successor_uid="u2",
                 type=lt.FS, lag=0.0),
        Relation(uid="r2", predecessor_uid="u3", successor_uid="u4",
                 type=lt.FS, lag=0.0)]
    p.build_lookups()
    return p


# ── the lag unit, which is where this went wrong ─────────────────────────────

def test_lag_is_written_in_hours_because_that_is_what_the_model_stores():
    """
    Relation.lag is hours, like every other duration here, and the scheduler
    divides it back out by the calendar's hours per day. Writing working days
    into it made every lag eight times too short — a 254-day gap became 32 —
    and a thousand ties each losing most of their gap moved 1,652 dates by up
    to eight months.
    """
    p = _job([("A.10", "First", "Room", "2026-02-02", "2026-02-06"),
              ("A.20", "Second", "Room", "2026-04-01", "2026-04-03")])
    lag = lt._implied_lag(p.activities[0], p.activities[1], lt.FS)
    assert lag is not None
    assert lag % lt.HOURS_PER_DAY == 0, "not a whole number of days in hours"
    assert lag / lt.HOURS_PER_DAY > 20, "a two-month gap came out as days"


def test_an_adjacent_pair_gets_a_zero_lag():
    """The scheduler starts a successor the working day AFTER its predecessor
    finishes, so a one-day gap is lag zero, not lag one."""
    p = _job([("A.10", "First", "Room", "2026-02-02", "2026-02-03"),
              ("A.20", "Second", "Room", "2026-02-04", "2026-02-05")])
    assert lt._implied_lag(p.activities[0], p.activities[1], lt.FS) == 0.0


def test_dates_that_contradict_the_relationship_give_no_lag():
    """A successor starting before its predecessor finishes is not
    finish-to-start here, whatever it is on the other building."""
    p = _job([("A.10", "First", "Room", "2026-03-02", "2026-03-20"),
              ("A.20", "Second", "Room", "2026-03-05", "2026-03-09")])
    assert lt._implied_lag(p.activities[0], p.activities[1], lt.FS) is None


# ── learning the sister job's sequence ───────────────────────────────────────

def test_the_room_is_stripped_so_one_pattern_covers_every_room():
    """
    Keyed on the full name, "Install High Steel (Gen 315)" is seen once and the
    "seen at least twice" rule throws every pattern away. That is what made a
    first run learn 134 patterns and place none of them.
    """
    assert lt._step("Install High Steel (Gen 315)") == "INSTALL HIGH STEEL"
    assert lt._step("Paint - MV 106 - Data Center B") == "PAINT"
    assert lt._step("Engine Start Up and Burn-ins - GEN 315") \
        == "ENGINE START UP AND BURN-INS"


def test_a_name_with_no_room_on_it_is_left_whole():
    assert lt._step("Overhead Rough In/ FA") == "OVERHEAD ROUGH IN/ FA"


def test_patterns_are_learned_from_the_source():
    pat = lt.patterns(_source(), min_seen=2)
    assert ("INSTALL HIGH STEEL", "INSTALL HANGERS") in pat
    assert pat[("INSTALL HIGH STEEL", "INSTALL HANGERS")]["seen"] == 2


def test_only_logic_inside_one_folder_is_learned():
    """
    A cross-folder tie says something about the SOURCE's layout — this room
    feeds that corridor — and does not transfer to a building laid out
    differently.
    """
    src = _source()
    src.relations.append(Relation(uid="x", predecessor_uid="u2",
                                  successor_uid="u3", type=lt.FS, lag=0.0))
    src.build_lookups()
    pat = lt.patterns(src, min_seen=1)
    assert ("INSTALL HANGERS", "INSTALL HIGH STEEL") not in pat


# ── the invariant ────────────────────────────────────────────────────────────

def test_adding_logic_does_not_move_the_printed_dates():
    """
    The acceptance test. Logic taken from a schedule's own dates is consistent
    with them, so rescheduling is a no-op. This is the whole reason the lag is
    computed from the print rather than copied from the source.
    """
    rows = [("A.10", "Install High Steel", "Room", "2026-02-02", "2026-02-04"),
            ("A.20", "Install Hangers", "Room", "2026-04-01", "2026-04-03"),
            ("A.30", "Pull Wire", "Room", "2026-06-01", "2026-06-05")]
    p = _job(rows)
    lt.apply(p, _source())
    assert p.relations, "no logic was added at all"
    check = lt.verify(p)
    assert check["counts"]["moved"] == 0, (
        f"{check['counts']['moved']} dates moved, worst "
        f"{check['counts']['worst_days']:+}: {check['moved'][:3]}")


def test_a_chain_of_three_still_reproduces_its_dates():
    rows = [("A.10", "One", "Room", "2026-02-02", "2026-02-06"),
            ("A.20", "Two", "Room", "2026-02-09", "2026-02-13"),
            ("A.30", "Three", "Room", "2026-02-16", "2026-02-20")]
    p = _job(rows)
    lt.apply(p, _source())
    assert lt.verify(p)["counts"]["moved"] == 0


# ── what it refuses ─────────────────────────────────────────────────────────

def test_a_tie_that_would_close_a_loop_is_dropped():
    """P6 imports a cycle and then refuses to schedule."""
    rows = [("A.10", "Install High Steel", "Room", "2026-02-02", "2026-02-04"),
            ("A.20", "Install Hangers", "Room", "2026-02-05", "2026-02-10")]
    p = _job(rows, relations=[Relation(uid="pre", predecessor_uid="u2",
                                       successor_uid="u1", type=lt.FS, lag=0.0)])
    r = lt.plan(p, _source())
    assert r["dropped"].get("would make a loop", 0) >= 1
    assert not any(t["pred"] == "u1" and t["succ"] == "u2" for t in r["ties"])


def test_logic_already_there_is_never_touched():
    """A schedule that has been worked on keeps every relation it has."""
    rows = [("A.10", "One", "Room", "2026-02-02", "2026-02-06"),
            ("A.20", "Two", "Room", "2026-02-09", "2026-02-13")]
    mine = Relation(uid="MINE", predecessor_uid="u1", successor_uid="u2",
                    type=lt.SS, lag=16.0)
    p = _job(rows, relations=[mine])
    lt.apply(p, _source())
    kept = [r for r in p.relations if r.uid == "MINE"]
    assert len(kept) == 1
    assert kept[0].type == lt.SS and kept[0].lag == 16.0


def test_the_same_pair_is_not_tied_twice():
    rows = [("A.10", "Install High Steel", "Room", "2026-02-02", "2026-02-04"),
            ("A.20", "Install Hangers", "Room", "2026-02-05", "2026-02-10")]
    p = _job(rows)
    lt.apply(p, _source())
    pairs = [(r.predecessor_uid, r.successor_uid) for r in p.relations]
    assert len(pairs) == len(set(pairs))


def test_planning_changes_nothing():
    rows = [("A.10", "One", "Room", "2026-02-02", "2026-02-06"),
            ("A.20", "Two", "Room", "2026-02-09", "2026-02-13")]
    p = _job(rows)
    lt.plan(p, _source())
    assert p.relations == []


def test_an_activity_with_no_dates_is_not_tied_from_dates():
    p = _job([("A.10", "One", "Room", "2026-02-02", "2026-02-06")])
    p.activities[0].planned_start = None
    p.build_lookups()
    assert lt.from_dates(p) == []


def test_the_report_counts_every_tie_it_proposes():
    rows = [("A.10", "Install High Steel", "Room", "2026-02-02", "2026-02-04"),
            ("A.20", "Install Hangers", "Room", "2026-02-05", "2026-02-10"),
            ("A.30", "Pull Wire", "Room", "2026-02-11", "2026-02-13")]
    r = lt.plan(_job(rows), _source())
    c = r["counts"]
    assert c["from_dates"] + c["from_patterns"] == c["adding"] == len(r["ties"])
    assert c["after"] == c["before"] + c["adding"]
