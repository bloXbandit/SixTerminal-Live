"""
test_connect.py — "are they all tied in?", asked of the network rather than the Gantt.

A room can be wired end to end and still reach nothing. Every activity has a
date, the bars look right, and the milestone that is supposed to wait on
twenty-eight rooms is waiting on twelve. That is invisible on a Gantt and
obvious as a graph question, which is the whole reason this exists.

What has to hold for it to be trustworthy rather than merely fast:

  - it pairs each folder with ITS OWN target. A phase 3 room tying into phase
    1's commissioning is worse than no tie at all, and the two sides do not
    share a branch, so the pairing cannot come from the folder tree.
  - a folder that already gets there is left completely alone.
  - it adds, never removes — the user's standing rule about their own logic.
  - it refuses to guess. No scope key, no target, no tie.
  - it previews, because one sentence reaches every folder that matches.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.connect import audit, connect, describe, upstream
from engine.edit_engine import EditError, apply_command
from engine.schedule_model import Activity, Calendar, Project, Relation, WBSNode


def _job(rooms=(("Gen 301", 3), ("Gen 315", 1)), wire_rooms=(), chain=True):
    """
    A job shaped like the real one: rooms under a phase branch, commissioning
    milestones under a SEPARATE Milestones branch. The two only relate through
    the phase number, which is the case the folder tree cannot solve.
    """
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="miles", name="Milestones", code="M",
                           parent_uid="root")]
    p.activities, p.relations = [], []

    phases = sorted({ph for _, ph in rooms})
    for ph in phases:
        p.wbs_nodes.append(WBSNode(uid=f"ph{ph}", name=f"Phase {ph} (Build-Out)",
                                   code=f"P{ph}", parent_uid="root"))
        p.wbs_nodes.append(WBSNode(uid=f"mph{ph}", name=f"Phase {ph}", code=f"MP{ph}",
                                   parent_uid="miles"))
        p.activities.append(Activity(
            uid=f"m{ph}", activity_id=f"MIL.PH{ph}.9000",
            name=f"Level 3 Commissioning Start (PH{ph})", wbs_uid=f"mph{ph}",
            calendar_uid="1", activity_type="Start Milestone",
            planned_start="2026-06-01", planned_finish="2026-06-01"))

    n = 0
    for name, ph in rooms:
        fu = "f" + name.replace(" ", "")
        p.wbs_nodes.append(WBSNode(uid=fu, name=name, code=name.replace(" ", ""),
                                   parent_uid=f"ph{ph}"))
        made = []
        for i, nm in enumerate(("Install Conduit", "Pull Wire", "Engine Start Up")):
            n += 1
            a = Activity(uid=f"u{n}", activity_id=f"A{n}0", name=f"{nm} ({name})",
                         wbs_uid=fu, calendar_uid="1", planned_duration=40,
                         remaining_duration=40,
                         planned_start=f"2026-02-0{i + 2}",
                         planned_finish=f"2026-02-1{i}")
            p.activities.append(a)
            made.append(a)
        if chain:
            for a, b in zip(made, made[1:]):
                p.relations.append(Relation(uid=f"r{a.uid}", predecessor_uid=a.uid,
                                            successor_uid=b.uid))
        if name in wire_rooms:
            p.relations.append(Relation(uid=f"w{fu}", predecessor_uid=made[-1].uid,
                                        successor_uid=f"m{ph}"))
    p.build_lookups()
    return p


GENS = r"^\s*Gen\s*\d+"


# ── it pairs each folder with its own target ─────────────────────────────────

def test_each_room_is_tied_to_its_own_phases_milestone():
    """The point of the scope key. The folder tree cannot answer this: the
    rooms sit under Phase N (Build-Out) and the milestones under Milestones /
    Phase N, which share no ancestor but the project."""
    p = _job()
    r = connect(p, GENS, "commission", apply=True)
    got = {m["folder"]: m["target"]["activity_id"] for m in r["missing"]}
    assert got == {"Gen 301": "MIL.PH3.9000", "Gen 315": "MIL.PH1.9000"}


def test_a_room_that_already_reaches_is_left_alone():
    p = _job(wire_rooms=("Gen 315",))
    before = len(p.relations)
    r = connect(p, GENS, "commission", apply=True)
    assert [c["folder"] for c in r["connected"]] == ["Gen 315"]
    assert [m["folder"] for m in r["missing"]] == ["Gen 301"]
    assert len(p.relations) == before + 1, "it touched a room that was already tied"


def test_it_reports_the_route_a_connected_room_already_takes():
    """"Confirmed" has to be showable, or the audit is just an assertion."""
    p = _job(wire_rooms=("Gen 315",))
    r = audit(p, GENS, "commission")
    assert r["connected"][0]["reaches"][0]["activity_id"] == "MIL.PH1.9000"


def test_an_indirect_route_still_counts_as_connected():
    """Reachability, not a direct tie. A room that gets there through two
    other activities is connected, and re-tying it would be noise."""
    p = _job()
    hop = Activity(uid="hop", activity_id="HOP", name="Area Turnover",
                   wbs_uid="ph3", calendar_uid="1", planned_duration=8,
                   planned_start="2026-03-01", planned_finish="2026-03-02")
    p.activities.append(hop)
    tail = [a for a in p.activities if a.name.startswith("Engine Start Up (Gen 301")][0]
    p.relations.append(Relation(uid="x1", predecessor_uid=tail.uid, successor_uid="hop"))
    p.relations.append(Relation(uid="x2", predecessor_uid="hop", successor_uid="m3"))
    p.build_lookups()
    r = audit(p, GENS, "commission")
    assert [c["folder"] for c in r["connected"]] == ["Gen 301"]


# ── what carries the tie out ─────────────────────────────────────────────────

def test_the_tie_comes_off_the_rooms_termination_not_a_random_activity():
    p = _job(rooms=(("Gen 301", 3),))
    r = audit(p, GENS, "commission")
    assert [t["name"] for t in r["missing"][0]["ties"]] == ["Engine Start Up (Gen 301)"]


def test_tail_all_ties_every_loose_end():
    """An unchained folder has three loose ends. Tying only the latest leaves
    two that can slip past commissioning unnoticed."""
    p = _job(rooms=(("Gen 301", 3),), chain=False)
    assert len(audit(p, GENS, "commission", tail="all")["missing"][0]["ties"]) == 3
    assert len(audit(p, GENS, "commission", tail="last")["missing"][0]["ties"]) == 1


def test_a_rooms_subfolders_are_one_room_not_three():
    """"Gen 315", "Gen 315 - JER" and "Gen 315 - WBO" are one room split by
    trade. Counting them separately would tie a room's WBO sub-folder into
    commissioning as if it were a room in its own right."""
    p = _job(rooms=(("Gen 315", 1),))
    p.wbs_nodes.append(WBSNode(uid="sub", name="Gen 315 - WBO", code="W",
                               parent_uid="fGen315"))
    p.activities.append(Activity(uid="w1", activity_id="W10", name="Set Gear",
                                 wbs_uid="sub", calendar_uid="1",
                                 planned_duration=8, planned_start="2026-02-20",
                                 planned_finish="2026-02-21"))
    p.build_lookups()
    r = audit(p, GENS, "commission")
    assert r["folders_checked"] == 1
    assert r["missing"][0]["activities"] == 4, "the sub-folder's work was not counted"


# ── it refuses to guess ──────────────────────────────────────────────────────

def test_a_folder_with_no_scope_key_is_named_never_wired():
    """A room in a branch with no phase number has no right answer. Tying it
    to whichever milestone came first is the failure this avoids."""
    p = _job(rooms=(("Gen 301", 3),))
    p.wbs_nodes.append(WBSNode(uid="orph", name="Gen 999", code="G9",
                               parent_uid="root"))
    p.activities.append(Activity(uid="o1", activity_id="O10", name="Set Gear",
                                 wbs_uid="orph", calendar_uid="1",
                                 planned_duration=8, planned_start="2026-02-02",
                                 planned_finish="2026-02-03"))
    p.build_lookups()
    r = connect(p, GENS, "commission", apply=True)
    assert [u["folder"] for u in r["unresolved"]] == ["Gen 999"]
    assert "scope key" in r["unresolved"][0]["why"]
    assert all(m["folder"] != "Gen 999" for m in r["missing"])


def test_a_scope_with_no_target_is_skipped_not_borrowed_from_another_phase():
    p = _job(rooms=(("Gen 301", 3), ("Gen 315", 1)))
    p.activities = [a for a in p.activities if a.uid != "m3"]
    p.build_lookups()
    r = connect(p, GENS, "commission", apply=True)
    assert [u["folder"] for u in r["unresolved"]] == ["Gen 301"]
    assert "scope 3" in r["unresolved"][0]["why"]
    assert [m["folder"] for m in r["missing"]] == ["Gen 315"]


def test_a_tie_that_would_close_a_loop_is_refused_with_the_reason():
    """P6 will not schedule a loop, and an import is a bad place to find out."""
    p = _job(rooms=(("Gen 301", 3),))
    head = [a for a in p.activities if a.name.startswith("Install Conduit")][0]
    p.relations.append(Relation(uid="back", predecessor_uid="m3",
                                successor_uid=head.uid))
    p.build_lookups()
    r = connect(p, GENS, "commission", apply=True)
    assert not r["missing"]
    assert "loop" in r["unresolved"][0]["why"]


def test_a_phase_number_is_not_confused_with_a_level_number():
    """"Level 3 Commissioning Start (PH1)" carries two numbers and only one of
    them pairs. Reading the wrong one wires every room to the wrong phase
    while looking entirely correct."""
    p = _job(rooms=(("Gen 315", 1),))
    r = audit(p, GENS, "commission")
    assert r["missing"][0]["scope"] == "1"


def test_no_folder_matches_says_so_rather_than_reporting_success():
    p = _job()
    r = audit(p, r"^Chiller", "commission")
    assert r["folders_checked"] == 0
    assert "No folders matched" in describe(r)


def test_no_target_matches_says_so():
    p = _job()
    assert "nothing to connect" in describe(audit(p, GENS, "energisation"))


def test_a_bad_pattern_says_which_one():
    with pytest.raises(ValueError, match="folder_pattern"):
        audit(_job(), "[", "commission")


# ── it does not disturb what it did not fix ──────────────────────────────────

def test_it_only_ever_adds_relations():
    """The standing rule: logic already set is not removed, only added over."""
    p = _job(wire_rooms=("Gen 315",))
    before = {(r.predecessor_uid, r.successor_uid, r.type, r.lag)
              for r in p.relations}
    connect(p, GENS, "commission", apply=True)
    after = {(r.predecessor_uid, r.successor_uid, r.type, r.lag)
             for r in p.relations}
    assert before <= after, "an existing relation was changed or dropped"


def test_running_it_twice_adds_nothing_the_second_time():
    p = _job()
    connect(p, GENS, "commission", apply=True)
    n = len(p.relations)
    second = connect(p, GENS, "commission", apply=True)
    assert len(p.relations) == n
    assert not second["missing"] and len(second["connected"]) == 2


def test_tying_does_not_reschedule_the_job():
    """A tie is logic, not a Schedule run. Dates stay where the user put them."""
    p = _job()
    before = {a.uid: (a.planned_start, a.planned_finish) for a in p.activities}
    connect(p, GENS, "commission", apply=True)
    assert {a.uid: (a.planned_start, a.planned_finish)
            for a in p.activities} == before


# ── upstream, the counterpart to ripple.downstream ───────────────────────────

def test_upstream_is_everything_the_activity_waits_on():
    p = _job(rooms=(("Gen 301", 3),), wire_rooms=("Gen 301",))
    got = upstream(p, "m3", include_self=False)
    assert got == {"u1", "u2", "u3"}


def test_upstream_terminates_on_a_loop_rather_than_hanging():
    """Real schedules do contain loops — a round trip through another tool is
    enough. Walking one must end, and include_self=False must still mean the
    start is not in the answer even when the loop leads back to it."""
    p = _job(rooms=(("Gen 301", 3),))
    p.relations.append(Relation(uid="loop", predecessor_uid="u3", successor_uid="u1"))
    p.build_lookups()
    assert upstream(p, "u3", include_self=False) == {"u1", "u2"}
    assert upstream(p, "u3") == {"u1", "u2", "u3"}


# ── as a command ─────────────────────────────────────────────────────────────

def test_it_reports_without_changing_anything_by_default():
    p = _job()
    before = len(p.relations)
    ok, msg = apply_command(p, {"action": "connect_folders",
                                "folder_pattern": GENS,
                                "target_pattern": "commission"})
    assert ok and len(p.relations) == before
    assert "Nothing has been changed" in msg


def test_apply_true_writes():
    p = _job()
    before = len(p.relations)
    ok, msg = apply_command(p, {"action": "connect_folders",
                                "folder_pattern": GENS,
                                "target_pattern": "commission", "apply": True})
    assert ok and len(p.relations) == before + 2
    assert "Tied 2" in msg


def test_the_command_says_which_argument_is_missing():
    for cmd, want in (
        ({"action": "connect_folders", "target_pattern": "x"}, "folder_pattern"),
        ({"action": "connect_folders", "folder_pattern": "x"}, "target_pattern"),
    ):
        ok, msg = apply_command(_job(), cmd)
        assert not ok and want in msg


def test_the_relation_type_and_lag_are_honoured():
    p = _job(rooms=(("Gen 301", 3),))
    apply_command(p, {"action": "connect_folders", "folder_pattern": GENS,
                      "target_pattern": "commission", "type": "ss",
                      "lag_days": 2, "apply": True})
    new = [r for r in p.relations if r.successor_uid == "m3"]
    assert len(new) == 1
    assert new[0].type == "Start to Start" and new[0].lag == 16.0
