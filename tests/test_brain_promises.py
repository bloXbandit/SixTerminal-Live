"""
test_brain_promises.py — plain sentences that become checked promises.

THE GAP THIS CLOSES
  parse_directive could produce three shapes — ORDER, SEQUENCE, ROOM_ORDER —
  or NOTE, which is inert. So most of what a scheduler knows arrived as an
  inert note:

    "every generator burn-in must lead to commissioning"  -> note
    "nothing in Phase 2 may finish after 4/26/27"         -> note
    "Phase 1 must finish by 3/15/27"                      -> note

  requirements.py could already CHECK all of those — REACHES, FOLLOWS,
  DEADLINE, NOT_AFTER — but the only way in was an explicit spec dict through
  the requirements action, which is not the door anyone walks through. Two
  rule systems, one enforcement engine each, and the more capable one behind
  the door nobody used.

THE TWO THINGS MOST WORTH PROTECTING
  The gate rule contains the word "after", so it has to beat the ordering
  parser to the sentence — otherwise "nothing in Phase 2 may finish AFTER
  4/26/27" reads as "'nothing in Phase 2 may finish' comes after '4/26/27'",
  an ordering rule about two things that do not exist.

  Ordering rules must be untouched. "X must come after Y" was working before
  and stealing it would change behaviour people rely on, which is why the
  FOLLOWS shape deliberately does not claim "after" or "follows".
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.project_brain import (NOTE, ORDER, REQUIREMENT, ROOM_ORDER,
                                  SEQUENCE, Brain, contradictions, describe,
                                  parse_date, parse_directive)
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _job(orphan=True):
    p = Project(uid="p", name="M", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="w", name="Phase 2 (Build-Out)", code="A")]
    p.activities, p.relations = [], []
    for u, n, fin in [("B1", "Engine Burn-in Gen 315", "2027-05-01"),
                      ("B2", "Engine Burn-in Gen 316", "2027-05-01"),
                      ("CX", "Commissioning", "2027-05-20")]:
        p.activities.append(Activity(
            uid=u, activity_id=u, name=n, wbs_uid="w", calendar_uid="1",
            activity_type="Task Dependent", status="Not Started",
            planned_duration=40, remaining_duration=40,
            planned_start="2027-04-01", planned_finish=fin))
    p.relations.append(Relation(uid="r1", predecessor_uid="B1",
                                successor_uid="CX", type="Finish to Start",
                                lag=0.0))
    if not orphan:
        p.relations.append(Relation(uid="r2", predecessor_uid="B2",
                                    successor_uid="CX",
                                    type="Finish to Start", lag=0.0))
    p.build_lookups()
    return p


# ── the four new shapes ──────────────────────────────────────────────────────

def test_a_forward_path_promise_becomes_a_requirement():
    d = parse_directive("every generator burn-in must lead to commissioning")
    assert d.kind == REQUIREMENT
    assert d.spec["kind"] == "reaches"
    assert d.spec["what"] == "generator burn-in"
    assert d.spec["to"] == "commissioning"


def test_a_gate_becomes_a_requirement_and_beats_the_ordering_parser():
    """It contains the word 'after'. Left to _AFTER_RE it parses as an
    ordering rule about two things that do not exist."""
    d = parse_directive("nothing in Phase 2 may finish after 4/26/27")
    assert d.kind == REQUIREMENT, f"parsed as {d.kind}, not a gate"
    assert d.spec["kind"] == "not_after"
    assert d.spec["scope"] == "Phase 2"
    assert d.spec["date"] == "2027-04-26"


def test_a_contract_date_becomes_a_deadline():
    d = parse_directive("Phase 1 must finish by 3/15/27")
    assert d.kind == REQUIREMENT and d.spec["kind"] == "deadline"
    assert d.spec["what"] == "Phase 1" and d.spec["date"] == "2027-03-15"


def test_a_driven_by_promise_becomes_follows():
    d = parse_directive("every MV room must be driven by its transformer delivery")
    assert d.kind == REQUIREMENT and d.spec["kind"] == "follows"


def test_a_promise_needs_a_universal_word():
    """'burn-in leads to commissioning' is an observation about one activity;
    'EVERY burn-in must lead to commissioning' is a rule about all of them."""
    assert parse_directive("the burn-in leads to commissioning").kind == NOTE


# ── the shapes that already worked must not change ───────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("Set Chiller must come after Chillers", ORDER),
    ("Pull Wire must come after Rough In", ORDER),
    ("MV rooms run sequential", SEQUENCE),
    ("MV rooms run 107, 105, 106", ROOM_ORDER),
    ("this is a data centre fit-out", NOTE),
])
def test_the_existing_shapes_are_untouched(text, kind):
    assert parse_directive(text).kind == kind


def test_follows_does_not_steal_the_word_after():
    """ORDER is the more specific claim for 'after'/'follows' and was working
    first; the FOLLOWS shape asks for 'driven by' instead."""
    assert parse_directive("every termination must come after its rough in").kind == ORDER


# ── dates, in the forms a scheduler types ────────────────────────────────────

@pytest.mark.parametrize("text,iso", [
    ("4/26/27", "2027-04-26"),
    ("3/15/2027", "2027-03-15"),
    ("2027-06-07", "2027-06-07"),
    ("June 7 2027", "2027-06-07"),
    ("7 June 2027", "2027-06-07"),
    ("15 Mar 27", "2027-03-15"),
])
def test_dates_parse(text, iso):
    assert parse_date(text) == iso


def test_an_all_numeric_date_is_read_US_order():
    """4/26/27 is April 26th. A schedule is not the place to discover the
    guess went the other way — and 26 is not a month, so the alternative
    reading would silently fail rather than differ."""
    assert parse_date("4/26/27") == "2027-04-26"


def test_a_date_that_is_not_one_is_refused():
    assert parse_date("sometime next spring") is None
    assert parse_directive("Phase 1 must finish by sometime next spring").kind == NOTE


# ── grounding holds promises to the same standard as rules ───────────────────

def test_a_promise_that_names_nothing_is_demoted_with_a_reason():
    p = _job()
    b = Brain(key="J")
    d = b.add("every widget must lead to commissioning", p)
    assert d.kind == NOTE and d.note_reason
    assert d in b.open_questions


def test_a_promise_that_binds_reports_what_it_bears_on():
    p = _job()
    b = Brain(key="J")
    d = b.add("every engine burn-in must lead to commissioning", p)
    assert d.kind == REQUIREMENT and d.matched_subject == 2
    assert "forward path" in describe(d)


def test_a_demoted_promise_recovers_when_the_work_appears():
    """Grounding must not be a one-way door — parsed_kind never changes."""
    p = _job()
    b = Brain(key="J")
    d = b.add("every widget must lead to commissioning", p)
    assert d.kind == NOTE
    p.activities.append(Activity(
        uid="W1", activity_id="W1", name="Widget Install", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=40, remaining_duration=40,
        planned_start="2027-04-01", planned_finish="2027-05-01"))
    p.build_lookups()
    b.reground(p)
    assert b.directives[0].kind == REQUIREMENT


# ── both doors feed one check ────────────────────────────────────────────────

def test_active_specs_merges_what_was_taught_with_what_was_set():
    p = _job()
    b = Brain(key="J")
    b.requirements = [{"kind": "deadline", "what": "Commissioning",
                       "date": "2027-06-01", "label": "cx"}]
    b.add("every engine burn-in must lead to commissioning", p)
    assert len(b.active_specs()) == 2


def test_a_demoted_promise_is_not_in_force():
    p = _job()
    b = Brain(key="J")
    b.add("every widget must lead to commissioning", p)
    assert b.active_specs() == []


def test_the_agent_is_told_about_promises():
    p = _job()
    b = Brain(key="J")
    b.add("every engine burn-in must lead to commissioning", p)
    block = b.context_block(p)
    assert "PROMISES" in block and "engine burn-in" in block


def test_a_brain_holding_only_promises_still_produces_a_block():
    """The emptiness guard listed rules, notes and questions but not promises,
    so a brain holding nothing else returned an empty block and the agent
    never heard about them."""
    p = _job()
    b = Brain(key="J")
    b.add("every engine burn-in must lead to commissioning", p)
    assert not b.rules and not b.notes and not b.open_questions
    assert b.context_block(p).strip() != ""


def test_a_taught_promise_is_actually_checked():
    from engine.edit_engine import apply_command
    import engine.edit_engine as ee
    p = _job(orphan=True)
    b = Brain(key="J")
    b.add("every engine burn-in must lead to commissioning", p)
    old = ee._BRAIN_FOR
    ee._BRAIN_FOR = lambda _p: b
    try:
        ok, msg = apply_command(p, {"action": "requirements", "op": "check"})
    finally:
        ee._BRAIN_FOR = old
    assert ok and "B2" in msg, "the orphaned burn-in was not reported"


# ── contradictions ───────────────────────────────────────────────────────────

def test_two_rules_that_reverse_each_other_are_caught():
    b = Brain(key="J")
    b.add("Pull Wire must come after Rough In")
    b.add("Rough In must come after Pull Wire")
    c = contradictions(b.directives)
    assert [x["kind"] for x in c] == ["reversed"]


def test_a_loop_of_three_is_caught():
    b = Brain(key="J")
    b.add("Terminations must come after Trim")
    b.add("Trim must come after Devices")
    b.add("Devices must come after Terminations")
    assert any(x["kind"] == "cycle" for x in contradictions(b.directives))


def test_two_dates_on_the_same_work_are_caught():
    b = Brain(key="J")
    b.add("Phase 1 must finish by 3/15/27")
    b.add("Phase 1 must finish by 4/1/27")
    c = [x for x in contradictions(b.directives) if x["kind"] == "dates"]
    assert c and "2027-03-15" in c[0]["why"] and "2027-04-01" in c[0]["why"]


def test_a_stated_order_against_sequential_is_caught():
    b = Brain(key="J")
    b.add("MV rooms run 107, 105, 106")
    b.add("MV rooms run sequential")
    assert any(x["kind"] == "ordering" for x in contradictions(b.directives))


def test_rules_that_agree_are_not_flagged():
    b = Brain(key="J")
    b.add("Pull Wire must come after Rough In")
    b.add("Terminations must come after Pull Wire")
    b.add("Phase 1 must finish by 3/15/27")
    assert contradictions(b.directives) == []


def test_a_disabled_rule_cannot_contradict_anything():
    b = Brain(key="J")
    d1 = b.add("Pull Wire must come after Rough In")
    b.add("Rough In must come after Pull Wire")
    b.toggle(d1.id, False)
    assert contradictions(b.directives) == []


def test_a_long_chain_does_not_report_a_false_cycle():
    b = Brain(key="J")
    for a, c in [("B", "A"), ("C", "B"), ("D", "C"), ("E", "D")]:
        b.add(f"Task {a} must come after Task {c}")
    assert contradictions(b.directives) == []


def test_promises_survive_a_save_and_reload():
    p = _job()
    b = Brain(key="J")
    b.add("nothing in Phase 2 may finish after 4/26/27", p)
    back = Brain.from_json(b.to_json())
    d = back.promises[0]
    assert d.spec["kind"] == "not_after" and d.spec["date"] == "2027-04-26"
