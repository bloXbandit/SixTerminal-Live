"""
test_wbs_flow.py — is each folder actually wired into the job?

A schedule can pass every per-activity open-ends check and still be a pile of
islands: each folder tidily chained to itself, nothing leaving it. A slip in
one area then never reaches the milestone it ought to drive, and the critical
path runs through whichever island happens to be longest. Counting open ends
per ACTIVITY cannot show that — the question is about the FOLDER.

The bar defended here is the strict one, chosen deliberately: one stray tie
out of a fifty-activity folder does not make it connected. A folder is green
only when nothing inside it floats AND work both enters and leaves.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import wbs_flow
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p(folders=("A", "B")):
    p = Project(uid="p", name="J", id="J", data_date="2026-01-01",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid=u, name=f"Folder {u}", code=u) for u in folders]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, folder, start="2026-02-02"):
    a = Activity(uid=uid, activity_id=uid, name=f"Work {uid}", wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=8, remaining_duration=8,
                 planned_start=start, planned_finish=start)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start", lag=0.0))
    p.build_lookups()


def _verdict(p, uid):
    return wbs_flow.analyse(p)["folders"][uid]["verdict"]


# ── the verdicts ─────────────────────────────────────────────────────────────

def test_a_folder_that_flows_in_and_out_is_connected():
    p = _p(("A", "B", "C"))
    _act(p, "a1", "A"); _act(p, "b1", "B"); _act(p, "c1", "C")
    _rel(p, "a1", "b1")
    _rel(p, "b1", "c1")
    assert _verdict(p, "B") == wbs_flow.CONNECTED


def test_a_folder_nothing_leaves_is_one_way():
    p = _p(("A", "B"))
    _act(p, "a1", "A"); _act(p, "b1", "B")
    _rel(p, "a1", "b1")
    assert _verdict(p, "B") == wbs_flow.ONE_WAY, "B is fed but drives nothing"
    assert _verdict(p, "A") == wbs_flow.ONE_WAY, "A drives but nothing feeds it"


def test_a_folder_touching_nothing_outside_is_isolated():
    """Even when it is perfectly chained INSIDE itself — which is exactly the
    case a per-activity open-ends check calls healthy."""
    p = _p(("A", "B"))
    _act(p, "a1", "A"); _act(p, "b1", "B"); _act(p, "b2", "B")
    _rel(p, "b1", "b2")           # tidy internal chain, touches nothing outside
    assert _verdict(p, "B") == wbs_flow.ISOLATED


def test_one_stray_tie_does_not_make_a_folder_connected():
    """The whole point of the strict bar: a folder where 1 of 4 activities is
    linked and the other 3 float is not wired in."""
    p = _p(("A", "B"))
    _act(p, "a1", "A")
    _act(p, "b1", "B"); _act(p, "b2", "B"); _act(p, "b3", "B")
    _rel(p, "a1", "b1")           # b2 and b3 float
    assert _verdict(p, "B") == wbs_flow.DANGLING
    f = wbs_flow.analyse(p)["folders"]["B"]
    assert f["floating_count"] == 2
    assert set(f["floating"]) == {"b2", "b3"}


def test_internal_chaining_counts_so_not_every_activity_must_reach_out():
    """Work inside a folder is expected to be chained to itself — requiring
    every activity to leave the folder would be wrong."""
    p = _p(("A", "B", "C"))
    _act(p, "a1", "A")
    _act(p, "b1", "B"); _act(p, "b2", "B"); _act(p, "b3", "B")
    _act(p, "c1", "C")
    _rel(p, "a1", "b1")
    _rel(p, "b1", "b2")
    _rel(p, "b2", "b3")
    _rel(p, "b3", "c1")           # one way in, one way out, all chained
    assert _verdict(p, "B") == wbs_flow.CONNECTED


def test_a_folder_with_no_activities_is_a_container_not_a_fault():
    p = _p(("A",))
    assert _verdict(p, "A") == wbs_flow.EMPTY
    assert wbs_flow.analyse(p)["totals"]["with_activities"] == 0


# ── backward flow ────────────────────────────────────────────────────────────

def test_a_tie_feeding_earlier_work_is_reported_as_backward():
    p = _p(("LATE", "EARLY"))
    _act(p, "l1", "LATE", start="2027-01-04")
    _act(p, "e1", "EARLY", start="2026-01-05")
    _rel(p, "l1", "e1")
    data = wbs_flow.analyse(p)
    assert data["totals"]["backward_edges"] == 1
    b = data["backward"][0]
    assert b["from"] == "LATE" and b["to"] == "EARLY"
    assert "BACKWARD" in wbs_flow.report(p)


def test_forward_flow_is_not_flagged():
    p = _p(("EARLY", "LATE"))
    _act(p, "e1", "EARLY", start="2026-01-05")
    _act(p, "l1", "LATE", start="2027-01-04")
    _rel(p, "e1", "l1")
    assert wbs_flow.analyse(p)["totals"]["backward_edges"] == 0


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_names_the_floating_activities():
    p = _p(("A", "B"))
    _act(p, "a1", "A")
    _act(p, "b1", "B"); _act(p, "b2", "B")
    _rel(p, "a1", "b1")
    txt = wbs_flow.report(p)
    assert "b2" in txt, "the report must name what is floating, not just count it"


def test_the_first_and_last_folders_of_a_chain_are_not_treated_as_faults():
    """A -> B -> C: A has nothing feeding it and C nothing after it, which is
    what the start and end of a job look like. They are still reported as
    one-way, because that is factually what they are — but the report says
    plainly that those two are expected, so they are not chased."""
    p = _p(("A", "B", "C"))
    _act(p, "a1", "A"); _act(p, "b1", "B"); _act(p, "c1", "C")
    _rel(p, "a1", "b1"); _rel(p, "b1", "c1")
    txt = wbs_flow.report(p)
    assert _verdict(p, "B") == wbs_flow.CONNECTED
    assert "FIRST folder legitimately has nothing" in txt
    assert wbs_flow.analyse(p)["totals"]["isolated"] == 0
    assert wbs_flow.analyse(p)["totals"]["dangling"] == 0


def test_a_fully_wired_schedule_says_so_rather_than_listing_nothing():
    """Every folder both fed and feeding — a closed loop of three."""
    p = _p(("A", "B", "C"))
    _act(p, "a1", "A"); _act(p, "b1", "B"); _act(p, "c1", "C")
    _rel(p, "a1", "b1"); _rel(p, "b1", "c1"); _rel(p, "c1", "a1")
    txt = wbs_flow.report(p)
    assert "Every folder holding work flows in and out" in txt


def test_analysis_changes_nothing():
    p = _p(("A", "B"))
    _act(p, "a1", "A"); _act(p, "b1", "B")
    _rel(p, "a1", "b1")
    before = (len(p.activities), len(p.relations),
              [a.planned_start for a in p.activities])
    wbs_flow.analyse(p)
    wbs_flow.report(p)
    assert (len(p.activities), len(p.relations),
            [a.planned_start for a in p.activities]) == before


# ── duplicates ───────────────────────────────────────────────────────────────

def _named(p, uid, name, folder, start):
    a = Activity(uid=uid, activity_id=uid, name=name, wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=8, remaining_duration=8,
                 planned_start=start, planned_finish=start)
    p.activities.append(a)
    p.build_lookups()
    return a


def test_identical_rows_in_one_folder_are_reported():
    p = _p(("MV",))
    _named(p, "X1", "Set CRAHs **WBO", "MV", "2025-11-03")
    _named(p, "X2", "Set CRAHs **WBO", "MV", "2025-11-03")
    txt = wbs_flow.duplicates(p)
    assert "Set CRAHs" in txt and "X1" in txt and "X2" in txt


def test_the_same_work_in_different_areas_is_not_a_duplicate():
    """Repeated work across areas is how a real schedule is built."""
    p = _p(("MV101", "MV102"))
    _named(p, "X1", "Set CRAHs", "MV101", "2025-11-03")
    _named(p, "X2", "Set CRAHs", "MV102", "2025-11-03")
    assert "No duplicated activities" in wbs_flow.duplicates(p)


def test_the_same_work_on_different_dates_is_not_a_duplicate():
    p = _p(("MV",))
    _named(p, "X1", "Set CRAHs", "MV", "2025-11-03")
    _named(p, "X2", "Set CRAHs", "MV", "2026-04-01")
    assert "No duplicated activities" in wbs_flow.duplicates(p)


def test_duplicates_never_deletes_anything():
    p = _p(("MV",))
    _named(p, "X1", "Set CRAHs", "MV", "2025-11-03")
    _named(p, "X2", "Set CRAHs", "MV", "2025-11-03")
    wbs_flow.duplicates(p)
    assert len(p.activities) == 2


# ── wired up as read-only actions ────────────────────────────────────────────

def test_both_reports_are_advisory():
    from engine.edit_engine import is_advisory
    assert is_advisory("wbs_flow_report")
    assert is_advisory("find_duplicates")


def test_the_actions_run_through_the_engine():
    from engine.edit_engine import apply_command
    p = _p(("A", "B"))
    _act(p, "a1", "A"); _act(p, "b1", "B")
    _rel(p, "a1", "b1")
    ok, msg = apply_command(p, {"action": "wbs_flow_report"})
    assert ok and "WBS FLOW" in msg
    ok2, msg2 = apply_command(p, {"action": "find_duplicates"})
    assert ok2 and "duplicat" in msg2.lower()
