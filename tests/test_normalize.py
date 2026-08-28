"""
test_normalize.py — the bulk repair pass, and the rails on it.

The ask was a single button that wires a schedule green. On a real half-built
job that is the wrong shape, and the reasons are what this file defends:
order matters (wiring a folder that holds duplicated rows ties one twin and
leaves the other floating), a batch nobody can review is a batch nobody can
trust, and a pass that does not measurably improve anything must not be
applied at all.

So the contract is: report by default, apply only when told, never delete,
never touch logic the user set, skip what cannot be safely wired, and prove
the improvement on a copy before touching the real project.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import normalize
from engine.edit_engine import apply_command, is_advisory
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p(folders):
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid=u, name=n, code=u) for u, n in folders]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, folder, start, name=None, dur=5):
    a = Activity(uid=uid, activity_id=uid, name=name or f"Work {uid}",
                 wbs_uid=folder, calendar_uid="1",
                 activity_type="Task Dependent", status="Not Started",
                 planned_duration=dur * 8, remaining_duration=dur * 8,
                 planned_start=start, planned_finish=start)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start",
                                lag=0.0))
    p.build_lookups()


def _chain_job():
    """A wired feeder folder, and an isolated folder dated to follow it."""
    p = _p([("A", "Feeder"), ("B", "MV 101")])
    _act(p, "a1", "A", "2026-02-02")
    _act(p, "a2", "A", "2026-02-09")
    _rel(p, "a1", "a2")
    for i, d in enumerate(["2026-02-16", "2026-02-23", "2026-03-02"]):
        _act(p, f"b{i}", "B", d)
    return p


# ── the plan reads the whole job ─────────────────────────────────────────────

def test_the_plan_is_read_only():
    p = _chain_job()
    before = (len(p.activities), len(p.relations))
    normalize.plan(p)
    normalize.diagnose(p)
    assert (len(p.activities), len(p.relations)) == before


def test_the_plan_names_isolated_folders_and_floating_work():
    p = _chain_job()
    txt = normalize.plan(p)
    assert "MV 101" in txt
    assert "no logic" in txt.lower() or "floating" in txt.lower()


def test_a_healthy_schedule_is_told_it_is_healthy():
    """A straight chain: the middle folder is fully connected, and the first
    and last are legitimately one-way because a job has a start and an end."""
    p = _p([("A", "One"), ("B", "Two"), ("C", "Three")])
    _act(p, "a1", "A", "2026-02-02")
    _act(p, "b1", "B", "2026-02-09")
    _act(p, "c1", "C", "2026-02-16")
    _rel(p, "a1", "b1")
    _rel(p, "b1", "c1")
    assert "good shape" in normalize.plan(p)


def test_duplicates_are_ranked_above_everything_else():
    """Order is not cosmetic — wiring a duplicated folder bakes the
    duplication into the network."""
    p = _chain_job()
    _act(p, "dup1", "B", "2026-02-16", name="Set CRAHs")
    _act(p, "dup2", "B", "2026-02-16", name="Set CRAHs")
    d = normalize.diagnose(p)
    assert d["findings"][0]["kind"] == "duplicates"
    assert d["findings"][0]["severity"] == normalize.BLOCKER


# ── the wiring pass, and what it refuses to do ───────────────────────────────

def test_it_reports_without_applying_by_default():
    p = _chain_job()
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "normalize_logic"})
    assert ok
    assert len(p.relations) == rels, "reporting must not wire anything"


def test_it_applies_only_when_explicitly_told():
    p = _chain_job()
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "normalize_logic", "apply": True})
    assert ok
    assert len(p.relations) > rels
    assert "Undo reverts" in msg


def test_it_never_deletes_anything():
    p = _chain_job()
    acts = {a.uid for a in p.activities}
    rels = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    apply_command(p, {"action": "normalize_logic", "apply": True})
    assert acts == {a.uid for a in p.activities}
    assert rels <= {(r.predecessor_uid, r.successor_uid) for r in p.relations}


def test_it_never_touches_an_end_the_user_already_closed():
    """Only ALREADY-OPEN ends are candidates, so deliberate logic survives."""
    p = _chain_job()
    _rel(p, "b0", "b1")                       # the user's own tie
    before = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    apply_command(p, {"action": "normalize_logic", "apply": True})
    after = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    assert before <= after


def test_a_folder_holding_duplicates_is_skipped_not_wired():
    p = _chain_job()
    _act(p, "dup1", "B", "2026-02-16", name="Set CRAHs")
    _act(p, "dup2", "B", "2026-02-16", name="Set CRAHs")
    r = normalize.normalize_logic(p)
    assert "MV 101" in r["skipped_for_duplicates"]
    touched = {t["folder"] for t in r["folders_touched"]}
    assert "MV 101" not in touched


def test_a_pass_that_cannot_help_is_held_back():
    """Refusing to apply a no-op pass is the point — it stops the tool
    churning the schedule to look busy."""
    p = _p([("A", "Only")])
    _act(p, "a1", "A", "2026-02-02")
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "normalize_logic", "apply": True})
    assert ok
    assert len(p.relations) == rels
    assert ("no measurable improvement" in msg
            or "Nothing met the confidence bar" in msg)


def test_the_cap_keeps_a_batch_reviewable():
    p = _chain_job()
    r = normalize.normalize_logic(p, limit=1)
    assert len(r["commands"]) <= 1


def test_raising_the_confidence_bar_proposes_fewer_ties():
    p = _chain_job()
    loose = normalize.normalize_logic(p, min_confidence=0.3)
    strict = normalize.normalize_logic(p, min_confidence=0.95)
    assert len(strict["commands"]) <= len(loose["commands"])


# ── measuring, honestly ──────────────────────────────────────────────────────

def test_verify_runs_on_a_copy_and_leaves_the_project_alone():
    p = _chain_job()
    before = (len(p.relations), [a.planned_start for a in p.activities])
    r = normalize.normalize_logic(p)
    normalize.verify(p, r["commands"])
    assert (len(p.relations), [a.planned_start for a in p.activities]) == before


def test_isolated_becoming_partly_wired_counts_as_improvement():
    """The raw counts show 'dangling went UP' when an isolated folder gets
    some rows wired. Ranking the verdicts is what tells progress from
    regression, and getting this backwards would make the tool report its
    own successes as failures."""
    p = _chain_job()
    r = normalize.normalize_logic(p)
    v = normalize.verify(p, r["commands"])
    assert v["folders_improved"] >= 1
    assert v["folders_worsened"] == 0
    assert v["floating_removed"] >= 1


def test_the_report_says_when_nothing_reaches_fully_connected():
    """Not overselling is the whole difference between this and a red button
    that claims to have greened a schedule it did not."""
    p = _chain_job()
    txt = normalize.normalize_report(p)
    assert "WHAT THIS WOULD ACTUALLY DO" in txt
    assert ("no folder reaches fully connected" in txt
            or "folders fully connected" in txt)


def test_measure_reports_the_numbers_that_matter():
    p = _chain_job()
    m = normalize.measure(p)
    for k in ("connected", "isolated", "dangling", "floating_activities",
              "backward_edges", "relations", "unlinked"):
        assert k in m


# ── wiring into the engine ───────────────────────────────────────────────────

def test_the_plan_is_advisory_but_the_pass_is_not():
    assert is_advisory("normalize_plan")
    assert not is_advisory("normalize_logic"), (
        "it can edit, so a turn that runs it must not be counted as a report")


def test_normalize_plan_runs_through_the_engine():
    p = _chain_job()
    ok, msg = apply_command(p, {"action": "normalize_plan"})
    assert ok and "NORMALIZATION PLAN" in msg
