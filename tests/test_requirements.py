"""
test_requirements.py — the one-liners a PM says, made checkable.

"Phase 1 substantial completion is 15 March 27." "Every generator termination
has to lead to commissioning for its phase." "The first activity in each MV
room follows energization." Each of those is a testable property of the
network, but until now they could only sit in the brain as prose nobody
verified — so nothing could say WHICH activities broke them.

The load-bearing decision here is that "leads to" means a forward PATH, not a
direct tie. Work reaches a milestone through a chain, so demanding a direct
link would fail correct schedules and invite ties that skip the real
sequence. What actually matters is whether a slip in that activity can reach
the milestone at all.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import project_brain as pb
from engine import requirements as rq
from engine import edit_engine as ee
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p():
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="P1", name="Phase 1", code="PH1"),
                   WBSNode(uid="P2", name="Phase 2", code="PH2")]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, name, folder, start, finish=None):
    a = Activity(uid=uid, activity_id=uid, name=name, wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40,
                 remaining_duration=40, planned_start=start,
                 planned_finish=finish or start)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start",
                                lag=0.0))
    p.build_lookups()


def _job():
    """Two phases. In PH1 the burn-in reaches commissioning through a chain;
    in PH2 it does not reach it at all."""
    p = _p()
    _act(p, "B1", "Engine Start Up and Burn-ins", "P1", "2026-02-02")
    _act(p, "T1", "Terminations", "P1", "2026-02-09")
    _act(p, "C1", "Level 3 Commissioning Start", "P1", "2026-02-16")
    _rel(p, "B1", "T1")
    _rel(p, "T1", "C1")            # reaches, via a chain — not directly

    _act(p, "B2", "Engine Start Up and Burn-ins", "P2", "2026-03-02")
    _act(p, "C2", "Level 3 Commissioning Start", "P2", "2026-03-16")
    return p


# ── reaches: a PATH, not a direct link ───────────────────────────────────────

def test_reaching_through_a_chain_counts_as_reaching():
    """The whole reason this is reachability: demanding a direct tie would
    fail a schedule that is correct."""
    r = rq.check(_job(), {"kind": "reaches", "from": "Burn",
                          "to": "Commissioning", "scope": "Phase 1"})
    assert r["passed"], r
    assert r["matched"] == 1


def test_no_path_at_all_is_a_violation_and_names_the_activity():
    r = rq.check(_job(), {"kind": "reaches", "from": "Burn",
                          "to": "Commissioning", "scope": "Phase 2"})
    assert not r["passed"]
    assert r["violations"] == 1
    assert r["detail"][0]["activity_id"] == "B2"


def test_scope_keeps_the_phases_apart():
    """Without scope, PH2's burn-in could be 'satisfied' by PH1's
    commissioning, which is exactly the wrong answer on a phased job."""
    unscoped = rq.check(_job(), {"kind": "reaches", "from": "Burn",
                                 "to": "Commissioning"})
    scoped = rq.check(_job(), {"kind": "reaches", "from": "Burn",
                               "to": "Commissioning", "scope": "Phase 2"})
    assert unscoped["matched"] == 2
    assert scoped["matched"] == 1
    assert not scoped["passed"]


def test_a_pattern_matching_nothing_says_so_rather_than_passing():
    """Silently passing a requirement whose subject does not exist is the
    worst possible answer — it reports safety that was never checked."""
    r = rq.check(_job(), {"kind": "reaches", "from": "Nonexistent Work",
                          "to": "Commissioning"})
    assert not r["passed"]
    assert r.get("error")


def test_a_cycle_does_not_hang_the_check():
    p = _job()
    _rel(p, "C1", "B1")            # illegal, but must not spin
    r = rq.check(p, {"kind": "reaches", "from": "Burn",
                     "to": "Commissioning", "scope": "Phase 1"})
    assert r["passed"]


# ── follows ──────────────────────────────────────────────────────────────────

def test_follows_walks_backward_through_the_chain():
    r = rq.check(_job(), {"kind": "follows", "from": "Commissioning",
                          "driver": "Burn", "scope": "Phase 1"})
    assert r["passed"]


def test_follows_fails_when_nothing_drives_it():
    r = rq.check(_job(), {"kind": "follows", "from": "Commissioning",
                          "driver": "Burn", "scope": "Phase 2"})
    assert not r["passed"] and r["violations"] == 1


# ── deadlines ────────────────────────────────────────────────────────────────

def test_a_deadline_passes_when_the_finish_is_inside_it():
    p = _p()
    _act(p, "S1", "Substantial Completion", "P1", "2027-03-01", "2027-03-01")
    r = rq.check(p, {"kind": "deadline", "what": "Substantial Completion",
                     "scope": "PH1", "date": "2027-03-15"})
    assert r["passed"]


def test_a_deadline_fails_and_names_the_late_activity():
    p = _p()
    _act(p, "S1", "Substantial Completion", "P1", "2027-04-01", "2027-04-01")
    r = rq.check(p, {"kind": "deadline", "what": "Substantial Completion",
                     "scope": "PH1", "date": "2027-03-15"})
    assert not r["passed"]
    assert r["detail"][0]["activity_id"] == "S1"


def test_enforcing_a_deadline_pins_it_as_a_DEADLINE_not_a_pull():
    """Finish On Or Before caps the late date so the slip shows as negative
    float. Forcing the work onto the date would schedule the problem away."""
    p = _p()
    _act(p, "S1", "Substantial Completion", "P1", "2027-04-01", "2027-04-01")
    r = rq.enforce(p, {"kind": "deadline", "what": "Substantial Completion",
                       "scope": "PH1", "date": "2027-03-15"})
    assert r["commands"][0]["constraint_type"] == "Finish On Or Before"
    assert r["commands"][0]["constraint_date"] == "2027-03-15"


# ── enforcing a reaches ──────────────────────────────────────────────────────

def test_enforce_ties_the_violator_forward_in_time():
    """The tie must run forward — picking a target that starts before the
    activity finishes would create the backward flow the flow report exists
    to catch."""
    p = _job()
    r = rq.enforce(p, {"kind": "reaches", "from": "Burn",
                       "to": "Commissioning", "scope": "Phase 2"})
    c = r["commands"][0]
    assert c["predecessor_id"] == "B2" and c["successor_id"] == "C2"


def test_enforce_proposes_nothing_when_the_requirement_holds():
    r = rq.enforce(_job(), {"kind": "reaches", "from": "Burn",
                            "to": "Commissioning", "scope": "Phase 1"})
    assert r["commands"] == []


def test_enforce_never_deletes_or_repoints():
    p = _job()
    before = {(x.predecessor_uid, x.successor_uid) for x in p.relations}
    r = rq.enforce(p, {"kind": "reaches", "from": "Burn",
                       "to": "Commissioning", "scope": "Phase 2"})
    from engine.edit_engine import apply_commands
    apply_commands(p, r["commands"])
    assert before <= {(x.predecessor_uid, x.successor_uid) for x in p.relations}


# ── stored on the brain, and driven through the engine ───────────────────────

def _with_brain(brain):
    ee.set_brain_lookup(lambda proj: brain)
    return ee


def test_requirements_are_kept_on_the_brain_and_survive_a_round_trip():
    b = pb.Brain("k")
    b.requirements.append({"label": "PH1", "kind": "deadline",
                           "what": "Substantial Completion",
                           "date": "2027-03-15"})
    assert not b.is_empty(), "a job taught only requirements must still be saved"
    assert pb.Brain.from_json(b.to_json()).requirements == b.requirements


def test_adding_a_requirement_checks_it_immediately():
    p = _job()
    b = pb.Brain("k")
    e = _with_brain(b)
    ok, msg = e.apply_command(p, {
        "action": "requirements", "op": "add",
        "requirements": [{"label": "burn-ins reach commissioning",
                          "kind": "reaches", "from": "Burn",
                          "to": "Commissioning", "scope": "Phase 2"}]})
    assert ok
    assert len(b.requirements) == 1
    assert "break this" in msg and "B2" in msg


def test_the_same_requirement_is_not_stored_twice():
    p = _job()
    b = pb.Brain("k")
    e = _with_brain(b)
    spec = {"label": "dup", "kind": "reaches", "from": "Burn",
            "to": "Commissioning"}
    e.apply_command(p, {"action": "requirements", "op": "add",
                        "requirements": [spec]})
    e.apply_command(p, {"action": "requirements", "op": "add",
                        "requirements": [spec]})
    assert len(b.requirements) == 1


def test_enforce_reports_by_default_and_applies_only_when_told():
    p = _job()
    b = pb.Brain("k")
    b.requirements.append({"label": "reach", "kind": "reaches", "from": "Burn",
                           "to": "Commissioning", "scope": "Phase 2"})
    e = _with_brain(b)
    rels = len(p.relations)
    ok, msg = e.apply_command(p, {"action": "requirements", "op": "enforce"})
    assert ok and len(p.relations) == rels
    assert "Nothing applied" in msg

    ok, msg = e.apply_command(p, {"action": "requirements", "op": "enforce",
                                  "apply": True})
    assert ok and len(p.relations) > rels
    assert rq.check(p, b.requirements[0])["passed"], (
        "after enforcing, the requirement must actually hold")


def test_listing_and_removing():
    p = _job()
    b = pb.Brain("k")
    b.requirements.append({"label": "PH1 date", "kind": "deadline",
                           "what": "Substantial Completion", "date": "2027-03-15"})
    e = _with_brain(b)
    ok, msg = e.apply_command(p, {"action": "requirements", "op": "list"})
    assert ok and "PH1 date" in msg
    ok, msg = e.apply_command(p, {"action": "requirements", "op": "remove",
                                  "label": "PH1 date"})
    assert ok and not b.requirements


def test_the_report_says_how_many_hold():
    p = _job()
    txt = rq.report(p, [
        {"label": "a", "kind": "reaches", "from": "Burn",
         "to": "Commissioning", "scope": "Phase 1"},
        {"label": "b", "kind": "reaches", "from": "Burn",
         "to": "Commissioning", "scope": "Phase 2"},
    ])
    assert "1 of 2 hold" in txt
    assert "Nothing has been changed" in txt
