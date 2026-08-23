"""
test_room_order.py — the order rooms are actually built in.

"MV rooms run sequential" orders them 105 → 106 → 107, by the number in the
name. Real jobs rarely run that way: crane access, energisation order, or
which end the GC hands over first decides it, and until this rule existed
there was nowhere to put that knowledge. A drawing set can tell you the
building flows 107 → 105 → 106; the schedule had no way to be told.

Nothing here is specific to one job. The family is whatever the naming
actually uses — MV, ER, POD, BLDG — and the order is whatever was stated.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.project_brain import (Brain, Directive, ORDER, SEQUENCE, ROOM_ORDER,
                                  NOTE, parse_directive, ground, describe,
                                  directive_verdict, check)
from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation)


def _project(rooms=(105, 106, 107), family="MV", work=("Pull Wire", "Terminations")):
    """A room per folder, the same work repeated in each — the normal shape."""
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Electrical", code="E")]
    p.activities = []
    n = 0
    for r in rooms:
        uid = f"w{r}"
        p.wbs_nodes.append(WBSNode(uid=uid, name=f"{family} {r}", code=f"R{r}",
                                   parent_uid="root"))
        for w in work:
            n += 1
            p.activities.append(Activity(
                uid=f"a{n}", activity_id=f"A{1000 + n * 10}", name=w, wbs_uid=uid,
                calendar_uid="1", activity_type="Task Dependent",
                status="Not Started", planned_duration=40.0, remaining_duration=40.0,
                planned_start="2026-02-02", planned_finish="2026-02-06"))
    p.build_lookups()
    return p


def _rule(text, project=None):
    d = parse_directive(text)
    if project is not None:
        ground(project, d)
    return d


# ── the sentence becomes a rule ──────────────────────────────────────────────

def test_a_stated_room_order_parses_as_a_rule():
    d = _rule("MV rooms run 107, 105, 106")
    assert d.kind == ROOM_ORDER
    assert d.family.upper() == "MV"
    assert d.order == [107, 105, 106]


def test_arrows_and_then_are_read_as_separators():
    assert _rule("ER rooms go 3 -> 1 -> 2").order == [3, 1, 2]
    assert _rule("ER rooms go 3 → 1 → 2").order == [3, 1, 2]
    assert _rule("ER rooms run 3 then 1 then 2").order == [3, 1, 2]


def test_room_order_is_phrasing_tolerant():
    for text in ("MV room order is 107, 105, 106",
                 "MV rooms are sequenced 107, 105, 106",
                 "MV areas run 107, 105, 106",
                 "the MV lineups go 107, 105, 106"):
        assert _rule(text).kind == ROOM_ORDER, text


def test_a_bare_list_of_rooms_is_not_an_order():
    """"MV rooms 105, 106" names rooms; it does not claim an order, and
    enforcing one would be exactly the half-understood guess this refuses."""
    assert _rule("MV rooms 105, 106").kind == NOTE


def test_one_room_is_not_an_order():
    assert _rule("MV rooms run 105").kind == NOTE


def test_a_repeated_room_number_is_not_counted_twice():
    assert _rule("MV rooms run 107, 105, 107, 106").order == [107, 105, 106]


def test_a_stated_order_wins_over_plain_sequential():
    """The stated order is the more specific claim — it must not be swallowed
    by the older 'sequential' shape."""
    d = _rule("MV rooms run sequential 107, 105, 106")
    assert d.kind == ROOM_ORDER and d.order == [107, 105, 106]


def test_plain_sequential_still_parses_as_before():
    assert _rule("MV rooms run sequential").kind == SEQUENCE


def test_x_after_y_still_parses_as_before():
    assert _rule("Terminations follow Pull Wire in the same room").kind == ORDER


# ── it has to name rooms the job actually has ────────────────────────────────

def test_an_order_over_rooms_this_job_does_not_have_is_only_guidance():
    d = _rule("XX rooms run 900, 901, 902", _project())
    assert d.kind == NOTE
    assert "none of them" in d.note_reason


def test_one_real_room_out_of_the_list_is_not_an_order():
    d = _rule("MV rooms run 105, 900, 901", _project())
    assert d.kind == NOTE and "MV 105" in d.note_reason


def test_missing_rooms_are_named_but_the_rest_still_enforces():
    d = _rule("MV rooms run 107, 105, 999", _project())
    assert d.kind == ROOM_ORDER
    assert "999" in d.note_reason


def test_the_description_shows_the_run_order_back():
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert "MV 107 → MV 105 → MV 106" in describe(d)


# ── what it says about a candidate tie ───────────────────────────────────────

def _verdict(d, pred_room, succ_room, work="Pull Wire", family="MV"):
    return directive_verdict(d, work, work,
                             f"Electrical / {family} {pred_room}",
                             f"Electrical / {family} {succ_room}")


def test_consecutive_rooms_in_the_stated_order_are_supported():
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert _verdict(d, 107, 105) == "supports"
    assert _verdict(d, 105, 106) == "supports"


def test_running_against_the_stated_order_is_a_violation():
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert _verdict(d, 105, 107) == "violates"
    assert _verdict(d, 106, 105) == "violates"


def test_number_order_is_not_assumed_when_an_order_is_stated():
    """105 → 106 is the obvious tie by number, and it is right here only
    because the stated order happens to agree. 106 → 107 is the number-order
    tie that the stated order forbids."""
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert _verdict(d, 106, 107) == "violates"


def test_skipping_a_room_is_neither_endorsed_nor_forbidden():
    """107 → 106 skips 105. It is not the handoff the rule describes, but it
    does not run backwards either — saying nothing is the honest answer."""
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert _verdict(d, 107, 106) is None


def test_a_room_outside_the_stated_order_gets_no_verdict():
    p = _project(rooms=(105, 106, 107, 108))
    d = _rule("MV rooms run 107, 105, 106", p)
    assert _verdict(d, 106, 108) is None


def test_different_work_across_two_rooms_is_not_ordered():
    """Room order sequences the SAME work from room to room. Read loosely it
    would endorse every pair across two rooms — thousands of ties nobody
    asked for."""
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert directive_verdict(d, "Pull Wire", "Terminations",
                             "Electrical / MV 107", "Electrical / MV 105") is None


def test_the_room_can_be_read_from_the_activity_name_too():
    """Not every job puts the room in the folder — some put it in the name."""
    d = _rule("MV rooms run 107, 105, 106", _project())
    assert directive_verdict(d, "Pull Wire MV 107", "Pull Wire MV 105", "", "") == "supports"


def test_a_disabled_rule_says_nothing():
    d = _rule("MV rooms run 107, 105, 106", _project())
    d.enabled = False
    assert _verdict(d, 107, 105) is None


# ── it works on naming this job has never seen ───────────────────────────────

def test_the_family_is_whatever_the_job_calls_it():
    """Nothing here is keyed to MV/ER. A job of PODs or BLDGs works the same."""
    for family in ("POD", "BLDG", "CELL", "TR"):
        p = _project(rooms=(1, 2, 3), family=family)
        d = _rule(f"{family} rooms run 3, 1, 2", p)
        assert d.kind == ROOM_ORDER, family
        assert _verdict(d, 3, 1, family=family) == "supports"
        assert _verdict(d, 1, 3, family=family) == "violates"


# ── it reaches the places a rule is meant to reach ───────────────────────────

def test_it_is_reported_as_an_enforced_rule_not_a_note():
    p = _project()
    b = Brain("J")
    b.add("MV rooms run 107, 105, 106", p)
    assert len(b.rules) == 1 and not b.notes


def test_the_agent_is_told_about_it_in_the_prompt_block():
    p = _project()
    b = Brain("J")
    b.add("MV rooms run 107, 105, 106", p)
    block = b.context_block()
    assert "RULE" in block and "MV 107 → MV 105" in block


def test_it_survives_being_saved_and_reloaded():
    p = _project()
    b = Brain("J")
    b.add("MV rooms run 107, 105, 106", p)
    back = Brain.from_json(b.to_json())
    d = back.directives[0]
    assert d.kind == ROOM_ORDER and d.order == [107, 105, 106]
    assert _verdict(d, 107, 105) == "supports"


def test_the_schedule_is_checked_against_it():
    """A tie already in the file that runs against the stated order is
    findable — that is what makes it a rule rather than a preference."""
    p = _project()
    pull = {a.uid: a for a in p.activities if a.name == "Pull Wire"}
    uids = list(pull)
    a105 = next(u for u in uids if p.get_wbs(pull[u].wbs_uid).name == "MV 105")
    a107 = next(u for u in uids if p.get_wbs(pull[u].wbs_uid).name == "MV 107")
    p.relations = [Relation(uid="r1", predecessor_uid=a105, successor_uid=a107,
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    d = _rule("MV rooms run 107, 105, 106", p)
    rep = check(p, [d])
    assert rep["violations"], "a backwards tie was not reported"
    assert rep["violations"][0]["kind"] == "relationship"


def test_it_changes_which_tie_the_ranker_prefers():
    """The whole point: a stated order has to move the ranking, or it is just
    prose in a prompt."""
    from engine.logic_advisor import _Ctx, score_tie
    p = _project()
    d = _rule("MV rooms run 107, 105, 106", p)
    pull = [a for a in p.activities if a.name == "Pull Wire"]
    by_room = {p.get_wbs(a.wbs_uid).name: a for a in pull}
    told = _Ctx(p, [d])
    right, _ = score_tie(told, by_room["MV 107"], by_room["MV 105"], 0)
    wrong, _ = score_tie(told, by_room["MV 105"], by_room["MV 107"], 0)
    assert right > wrong
