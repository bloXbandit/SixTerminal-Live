"""
test_tie_options.py — "what should this connect to?" answered with buttons.

Asking about ONE activity's logic returns ranked predecessor and successor
candidates, each with its confidence and reasons, for the user to apply with a
click. The whole path is deterministic: the candidates are scored from the
schedule, so nothing on the card can be an activity that does not exist.

It fires only on a QUESTION of that shape. "Link A to B" is an instruction and
must still go to the interpreter.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import logic_advisor as la
from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation


def _proj():
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="str", name="Structure", code="STR", parent_uid=None)]
    p.activities = []
    p.relations = []
    return p


def _act(p, uid, aid, name, start, finish, dur=5.0):
    a = Activity(uid=uid, activity_id=aid, name=name, wbs_uid="str", calendar_uid="1",
                 activity_type="Task Dependent", status="Not Started",
                 planned_duration=dur * 8, remaining_duration=dur * 8,
                 planned_start=start, planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _chain():
    """Precast erection, its turnover, and the work either side."""
    p = _proj()
    _act(p, "a0", "STR.100", "Deep Foundations Grid 12-6", "2026-01-05", "2026-01-30")
    _act(p, "a1", "STR.110", "Precast Erection Area 7", "2026-02-02", "2026-02-27")
    _act(p, "a2", "STR.120", "Precast Area 7 Turnover", "2026-03-02", "2026-03-06")
    _act(p, "a3", "STR.130", "Roofing Area 7", "2026-03-09", "2026-03-20")
    return p


# ── when the card appears ─────────────────────────────────────────────────────

def test_a_question_about_ties_is_recognised():
    for q in ("What would be the best connection for Precast Erection Area 7?",
              "what should the predecessor be for STR.110",
              "which activity should Precast Erection Area 7 tie to",
              "recommend logic ties for STR.110",
              "best successor for STR.110?"):
        assert la.tie_question(q), q


def test_an_instruction_is_not_a_question():
    for q in ("link STR.100 to STR.110",
              "add a predecessor STR.100 to STR.110",
              "connect Precast Erection Area 7 to Roofing Area 7",
              "make STR.100 the predecessor of STR.110",
              "delete the tie between STR.100 and STR.110"):
        assert not la.tie_question(q), q


def test_ordinary_conversation_is_not_a_tie_question():
    for q in ("how many activities are open ended",
              "what is the project finish date",
              "add 5 days to STR.110"):
        assert not la.tie_question(q), q


# ── finding the activity, without inventing one ───────────────────────────────

def test_an_id_in_the_question_is_resolved():
    p = _chain()
    hits = la.find_activity_in(p, "best predecessor for STR.110 please")
    assert [a.activity_id for a in hits] == ["STR.110"]


def test_a_quoted_name_is_resolved():
    p = _chain()
    hits = la.find_activity_in(p, 'what should "Precast Area 7 Turnover" connect to')
    assert [a.activity_id for a in hits] == ["STR.120"]


def test_a_bare_name_in_the_sentence_is_resolved():
    p = _chain()
    hits = la.find_activity_in(p, "what would be the best tie for Roofing Area 7")
    assert [a.activity_id for a in hits] == ["STR.130"]


def test_an_id_that_does_not_exist_resolves_to_nothing():
    p = _chain()
    assert la.find_activity_in(p, "best predecessor for STR.999") == []


# ── the options themselves ────────────────────────────────────────────────────

def test_both_directions_are_offered():
    p = _chain()
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    assert opts["predecessors"] and opts["successors"]


def test_the_predecessor_comes_before_and_the_successor_after():
    p = _chain()
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    assert opts["predecessors"][0]["successor_id"] == "STR.110"
    assert opts["successors"][0]["predecessor_id"] == "STR.110"


def test_the_precast_turnover_is_the_top_successor():
    p = _chain()
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    assert opts["successors"][0]["successor_id"] == "STR.120"


def test_every_option_carries_confidence_and_reasons():
    p = _chain()
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    for r in opts["predecessors"] + opts["successors"]:
        assert 0 < r["confidence"] <= 1
        assert r["signals"]


def test_an_existing_tie_is_not_offered_again():
    p = _chain()
    p.relations = [Relation(uid="r", predecessor_uid="a1", successor_uid="a2",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    assert not any(r["successor_id"] == "STR.120" for r in opts["successors"])


def test_every_offered_id_is_a_real_activity():
    p = _chain()
    real = {a.activity_id for a in p.activities}
    opts = la.tie_options(p, p.get_activity(activity_id="STR.110"))
    for r in opts["predecessors"] + opts["successors"]:
        assert r["predecessor_id"] in real and r["successor_id"] in real


def test_nothing_plausible_yields_no_options():
    p = _proj()
    _act(p, "a1", "X.100", "Paving and Striping", "2025-01-05", "2025-01-09")
    a = _act(p, "a2", "X.200", "Switchgear Energization", "2026-06-01", "2026-06-05")
    opts = la.tie_options(p, a)
    assert opts["predecessors"] == [] and opts["successors"] == []


# ── through the API, the way the browser sees it ──────────────────────────────

def _client_with(p):
    c = server.app.test_client()
    server._projects["t"] = server._make_session("t", "t.xml")
    server._projects["t"]["project"] = p
    server._active_id[0] = "t"
    return c


def test_the_endpoint_returns_a_tie_options_card():
    c = _client_with(_chain())
    r = c.post("/api/edit", json={"instruction":
                                  "What would be the best connection for STR.110?"})
    body = r.get_json()
    assert body["type"] == "tie_options"
    assert body["activity_id"] == "STR.110"
    assert body["predecessors"] and body["successors"]


def test_an_instruction_does_not_return_a_card():
    c = _client_with(_chain())
    r = c.post("/api/edit", json={"instruction": "link STR.100 to STR.110",
                                  "force_commands": [
                                      {"action": "add_relation",
                                       "predecessor_id": "STR.100",
                                       "successor_id": "STR.110"}]})
    assert r.get_json().get("type") != "tie_options"


def test_an_unresolvable_activity_falls_through_to_the_interpreter():
    """No card, and no invented activity — it just goes down the normal path."""
    c = _client_with(_chain())
    r = c.post("/api/edit", json={"instruction": "best predecessor for STR.999"})
    assert r.get_json().get("type") != "tie_options"
