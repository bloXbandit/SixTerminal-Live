"""
test_bridge.py — attaching a folder to the job, and clearing backward flags.

Ordering the work inside a folder and attaching that folder to the job are
different questions with different candidate sets. Getting the second wrong
is subtle: a tie into the middle of an already-chained folder looks like
progress, changes no date, and leaves the folder just as unfed as before.

The backward-flow half is about telling three things apart. A tie that is
genuinely upside down should be reversed; one whose dates merely drifted is
correct logic that needs a reflow, and reversing it would break the schedule.
On the reference file that split is 34 reversed against 60 stale — so a tool
that "fixed" all 94 would wreck sixty working ties.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import bridge
from engine.edit_engine import apply_command
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p(folders):
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid=u, name=n, code=u) for u, n in folders]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, folder, start, finish, name=None, atype="Task Dependent"):
    a = Activity(uid=uid, activity_id=uid, name=name or f"Work {uid}",
                 wbs_uid=folder, calendar_uid="1", activity_type=atype,
                 status="Not Started", planned_duration=40,
                 remaining_duration=40, planned_start=start,
                 planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start",
                                lag=0.0))
    p.build_lookups()


def _chained_folder():
    """Feeder folder, then an isolated folder already chained inside itself."""
    p = _p([("A", "Feeder"), ("B", "MV 101")])
    _act(p, "a1", "A", "2026-02-02", "2026-02-06")
    _act(p, "b1", "B", "2026-02-09", "2026-02-13")
    _act(p, "b2", "B", "2026-02-16", "2026-02-20")
    _act(p, "b3", "B", "2026-02-23", "2026-02-27")
    _rel(p, "b1", "b2")
    _rel(p, "b2", "b3")
    return p


# ── which row the bridge belongs on ──────────────────────────────────────────

def test_the_head_of_an_internal_chain_is_the_entry_point():
    """Bridging onto b2 or b3 would add a tie that changes no date and leaves
    the folder just as unfed — the head is the only row worth feeding."""
    p = _chained_folder()
    acts = [a for a in p.activities if a.wbs_uid == "B"]
    ends = bridge.entry_and_exit(p, acts)
    assert ends["head"].activity_id == "b1"
    assert ends["tail"].activity_id == "b3"
    assert ends["internal_chain"] is True


def test_an_unchained_folder_uses_dates_for_head_and_tail():
    p = _p([("B", "MV 101")])
    _act(p, "x", "B", "2026-03-02", "2026-03-06")
    _act(p, "y", "B", "2026-02-02", "2026-02-06")
    ends = bridge.entry_and_exit(p, p.activities)
    assert ends["head"].activity_id == "y"
    assert ends["tail"].activity_id == "x"
    assert ends["internal_chain"] is False


def test_a_start_milestone_is_preferred_as_the_way_in():
    """That is what a milestone is for — entering the folder through it beats
    entering through whichever task happens to share the date."""
    p = _p([("B", "MV 101")])
    _act(p, "task", "B", "2026-02-02", "2026-02-06")
    _act(p, "ms", "B", "2026-02-02", "2026-02-02", name="MV 101 Start",
         atype="Start Milestone")
    ends = bridge.entry_and_exit(p, p.activities)
    assert ends["head"].activity_id == "ms"


def test_the_bridge_is_proposed_onto_the_head_not_a_middle_row():
    p = _chained_folder()
    r = bridge.propose(p, "B")
    assert r["head"]["activity_id"] == "b1"
    ties = [c for c in r["commands"] if c["action"] == "add_relation"]
    assert all(c["successor_id"] == "b1" for c in ties
               if c["successor_id"] in ("b1", "b2", "b3"))


def test_it_reports_whether_the_folder_is_already_bridged():
    p = _chained_folder()
    assert bridge.propose(p, "B")["already_fed"] is False
    _rel(p, "a1", "b1")
    assert bridge.propose(p, "B")["already_fed"] is True


def test_an_already_bridged_folder_proposes_nothing_into_it():
    p = _chained_folder()
    _rel(p, "a1", "b1")
    r = bridge.propose(p, "B")
    assert not [c for c in r["commands"] if c["successor_id"] == "b1"]


def test_candidates_carry_the_reasoning():
    """The choice of bridge is the part worth arguing with, so the ranked
    alternatives and why each scored come back rather than just a tie."""
    p = _chained_folder()
    r = bridge.propose(p, "B")
    assert r["in_candidates"]
    top = r["in_candidates"][0]
    assert top["activity_id"] == "a1"
    assert top["why"], "a candidate with no stated reason is not reviewable"


def test_it_says_when_nothing_outside_could_feed_the_folder():
    p = _p([("B", "MV 101")])
    _act(p, "b1", "B", "2026-02-09", "2026-02-13")
    r = bridge.propose(p, "B")
    assert r["in_candidates"] == []
    assert "not in the schedule" in bridge.report(p, "B")


def test_reporting_changes_nothing():
    p = _chained_folder()
    before = (len(p.activities), len(p.relations))
    bridge.report(p, "B")
    bridge.propose(p, "B")
    assert (len(p.activities), len(p.relations)) == before


def test_the_action_reports_by_default_and_applies_when_told():
    p = _chained_folder()
    rels = len(p.relations)
    ok, _ = apply_command(p, {"action": "bridge_folder", "wbs": "MV 101"})
    assert ok and len(p.relations) == rels
    ok, msg = apply_command(p, {"action": "bridge_folder", "wbs": "MV 101",
                                "apply": True})
    assert ok and len(p.relations) > rels
    assert "undo reverts" in msg.lower()


# ── backward flow: three kinds, only one safely fixable ──────────────────────

def _backward_job():
    p = _p([("EARLY", "Foundations"), ("LATE", "Commissioning")])
    _act(p, "e1", "EARLY", "2026-01-05", "2026-01-09")
    _act(p, "l1", "LATE", "2027-01-04", "2027-01-08")
    return p


def test_a_genuinely_upside_down_tie_is_called_reversed():
    p = _backward_job()
    _rel(p, "l1", "e1")            # commissioning feeding foundations
    rows = bridge.classify_backward(p)
    assert len(rows) == 1 and rows[0]["kind"] == bridge.REVERSED


def test_a_tie_whose_folder_dates_merely_drifted_is_called_stale():
    """The two ACTIVITIES are in order; only the folders' earliest dates
    disagree. Reversing this would break correct logic."""
    p = _p([("F1", "Area 1"), ("F2", "Area 2")])
    _act(p, "a", "F1", "2026-06-01", "2026-06-05")
    _act(p, "b", "F2", "2026-06-08", "2026-06-12")
    _act(p, "early", "F2", "2026-01-05", "2026-01-09")   # drags F2's start back
    _rel(p, "a", "b")                                     # a finishes before b starts
    rows = bridge.classify_backward(p)
    assert len(rows) == 1
    assert rows[0]["kind"] == bridge.STALE


def test_only_reversed_ties_are_proposed_for_flipping():
    p = _p([("F1", "Area 1"), ("F2", "Area 2")])
    _act(p, "a", "F1", "2026-06-01", "2026-06-05")
    _act(p, "b", "F2", "2026-06-08", "2026-06-12")
    _act(p, "early", "F2", "2026-01-05", "2026-01-09")
    _rel(p, "a", "b")              # stale — must be left alone
    assert bridge.fix_backward(p) == []


def test_flipping_a_reversed_tie_deletes_and_re_adds_as_one_batch():
    p = _backward_job()
    _rel(p, "l1", "e1")
    cmds = bridge.fix_backward(p)
    assert [c["action"] for c in cmds] == ["delete_relation", "add_relation"]
    assert cmds[1]["predecessor_id"] == "e1" and cmds[1]["successor_id"] == "l1"


def test_fixing_actually_clears_the_backward_flag():
    p = _backward_job()
    _rel(p, "l1", "e1")
    ok, msg = apply_command(p, {"action": "fix_backward", "apply": True})
    assert ok
    assert bridge.classify_backward(p) == []


def test_fix_backward_reports_before_it_applies():
    p = _backward_job()
    _rel(p, "l1", "e1")
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "fix_backward"})
    assert ok and len(p.relations) == rels
    assert "Nothing applied" in msg


def test_forward_ties_are_never_reported_as_backward():
    p = _backward_job()
    _rel(p, "e1", "l1")
    assert bridge.classify_backward(p) == []
    assert "No folder ties run backward" in bridge.backward_report(p)


def test_the_report_separates_the_two_kinds():
    p = _p([("F1", "A"), ("F2", "B")])
    _act(p, "a", "F1", "2026-06-01", "2026-06-05")
    _act(p, "b", "F2", "2026-06-08", "2026-06-12")
    _act(p, "early", "F2", "2026-01-05", "2026-01-09")
    _rel(p, "a", "b")                                    # stale
    txt = bridge.backward_report(p)
    assert "STALE DATES" in txt
    assert "Run Schedule rather than editing logic" in txt
