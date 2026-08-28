"""
test_procurement_wire.py — delivery to install, and copying a sequence that works.

The refusal is the important half. procurement_report already knew which LLE
line feeds which installation; what it could not do was make the tie. Making
it is easy and making it CORRECTLY means never tying an install that is dated
before its own delivery — forcing that relationship pushes the work out and
quietly resolves a conflict that is a decision about the job, not about the
network.

The equipment match is deliberately loose so it finds things, which means it
also finds things it should not: on the reference schedule a generator
submittal matches "Install High Steel (Gen 317)" on the word "Gen" alone. The
negative-lag guard is what stops that becoming a relationship.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import procurement_wire as pw
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


def _act(p, uid, name, folder, start, finish):
    a = Activity(uid=uid, activity_id=uid, name=name, wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
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


def _proc_job(install_start="2026-06-01"):
    """A switchgear delivery, and the install it feeds."""
    p = _p([("LLE", "Procurement / LLE"), ("W", "MV 101")])
    _act(p, "S1", "MV Switchgear Delivery", "LLE", "2026-01-05", "2026-03-02")
    _act(p, "I1", "Install MV Switchgear", "W", install_start, "2026-06-12")
    return p


# ── the refusal ──────────────────────────────────────────────────────────────

def test_an_install_dated_before_its_delivery_is_never_tied():
    """Tying it would push the work out and hide a conflict only the user can
    resolve — either the procurement date is wrong or the work cannot happen."""
    p = _proc_job(install_start="2026-01-12")     # before delivery finishes
    r = pw.wire_procurement(p)
    assert r["commands"] == []
    assert r["blocked"], "it must be reported rather than silently dropped"


def test_no_proposed_tie_ever_has_a_negative_gap():
    """The equipment match is loose on purpose so it finds things; this is
    what stops a loose match becoming a wrong relationship."""
    p = _proc_job(install_start="2026-02-02")
    r = pw.wire_procurement(p)
    for item in r["tied"]:
        lag = item.get("implied_lag_days")
        assert lag is None or lag >= 0


def test_a_delivery_that_lands_first_is_tied():
    p = _proc_job(install_start="2026-06-01")
    r = pw.wire_procurement(p)
    assert len(r["commands"]) == 1
    c = r["commands"][0]
    assert c["predecessor_id"] == "S1" and c["successor_id"] == "I1"


def test_an_existing_tie_is_not_duplicated():
    p = _proc_job()
    _rel(p, "S1", "I1")
    assert pw.wire_procurement(p)["commands"] == []


def test_the_report_explains_why_something_was_not_tied():
    p = _proc_job(install_start="2026-01-12")
    txt = pw.procurement_report_text(p)
    assert "BEFORE its own delivery" in txt
    assert "I1" in txt


def test_reporting_changes_nothing():
    p = _proc_job()
    before = (len(p.activities), len(p.relations))
    pw.procurement_report_text(p)
    pw.wire_procurement(p)
    assert (len(p.activities), len(p.relations)) == before


def test_the_action_reports_by_default_and_applies_when_told():
    p = _proc_job()
    rels = len(p.relations)
    ok, _ = apply_command(p, {"action": "procurement_report"})
    assert ok and len(p.relations) == rels
    ok, msg = apply_command(p, {"action": "procurement_report", "apply": True})
    assert ok and len(p.relations) > rels
    assert "undo reverts" in msg.lower()


# ── replicating a sequence ───────────────────────────────────────────────────

def _pattern_job():
    """MV 101 wired; MV 105 has the same work with no logic; MV 106 is short."""
    p = _p([("A", "MV 101"), ("B", "MV 105"), ("C", "MV 106")])
    for uid, nm, d in [("a1", "Set CRAHs MV 101", "2026-02-02"),
                       ("a2", "Overhead Rough In MV 101", "2026-02-09"),
                       ("a3", "Wall Rough Ins MV 101", "2026-02-16")]:
        _act(p, uid, nm, "A", d, d)
    _rel(p, "a1", "a2")
    _rel(p, "a2", "a3")

    for uid, nm, d in [("b1", "Set CRAHs MV 105", "2026-06-01"),
                       ("b2", "Overhead Rough In MV 105", "2026-06-08"),
                       ("b3", "Wall Rough Ins MV 105", "2026-06-15")]:
        _act(p, uid, nm, "B", d, d)

    _act(p, "c1", "Set CRAHs MV 106", "C", "2026-07-01", "2026-07-01")
    return p


def test_the_pattern_is_copied_onto_a_folder_that_has_the_same_work():
    p = _pattern_job()
    r = pw.replicate_pattern(p, "MV 101", ["MV 105"])
    pairs = {(c["predecessor_id"], c["successor_id"]) for c in r["commands"]}
    assert pairs == {("b1", "b2"), ("b2", "b3")}


def test_work_the_target_does_not_have_is_skipped_and_counted():
    """MV 106 has only one of the three rows, so neither tie can be laid —
    and saying so is better than half-wiring it."""
    p = _pattern_job()
    r = pw.replicate_pattern(p, "MV 101", ["MV 106"])
    f = r["per_folder"][0]
    assert f["ties"] == 0 and f["not_present"] == 2


def test_logic_the_target_already_has_is_not_duplicated():
    p = _pattern_job()
    _rel(p, "b1", "b2")
    r = pw.replicate_pattern(p, "MV 101", ["MV 105"])
    pairs = {(c["predecessor_id"], c["successor_id"]) for c in r["commands"]}
    assert pairs == {("b2", "b3")}, "it re-laid a tie that was already there"


def test_running_it_twice_adds_nothing_the_second_time():
    p = _pattern_job()
    apply_command(p, {"action": "replicate_pattern", "source": "MV 101",
                      "targets": ["MV 105"], "apply": True})
    n = len(p.relations)
    apply_command(p, {"action": "replicate_pattern", "source": "MV 101",
                      "targets": ["MV 105"], "apply": True})
    assert len(p.relations) == n


def test_a_source_with_no_logic_is_refused_rather_than_copied():
    """Copying nothing onto everything would report success and do nothing."""
    p = _pattern_job()
    r = pw.replicate_pattern(p, "MV 105", ["MV 106"])
    assert r.get("error") and "no internal logic" in r["error"]


def test_replication_reports_by_default():
    p = _pattern_job()
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "replicate_pattern",
                                "source": "MV 101", "targets": ["MV 105"]})
    assert ok and len(p.relations) == rels
    assert "Nothing applied" in msg


def test_replication_never_deletes():
    p = _pattern_job()
    _rel(p, "b3", "b1")                      # an odd tie the user made
    before = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    apply_command(p, {"action": "replicate_pattern", "source": "MV 101",
                      "targets": ["MV 105"], "apply": True})
    after = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    assert before <= after
