"""
test_report_is_not_an_edit.py — the tool must not say it changed something.

Observed in a live session: the agent said "I'll begin wiring key work
activities…", the screen said "Applied 5 edits", and the schedule had exactly
zero new relationships. Asked directly, the agent then said "Correct, I
haven't made any edits yet."

Both statements came from the same record. recommend_logic READS the schedule
and returns findings; it was counted as an applied edit, so the UI announced
edits and the agent — reading that same history back — described work it had
never done. Neither was lying so much as trusting a record that lied to both.

A report is not an edit, and everything that reports on a turn has to say so.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.edit_engine import ADVISORY_ACTIONS, is_advisory, apply_commands
from engine.schedule_model import Project, Activity, WBSNode, Calendar
from interpreter.llm_interpreter import _build_conversation, SYSTEM_PROMPT


def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities, p.relations = [], []
    for uid, name, s, f in [("a1", "Pull Wire MV 105", "2026-02-02", "2026-02-06"),
                            ("a2", "Terminations MV 105", "2026-02-09", "2026-02-13")]:
        p.activities.append(Activity(
            uid=uid, activity_id=uid.upper(), name=name, wbs_uid="w",
            calendar_uid="1", activity_type="Task Dependent", status="Not Started",
            planned_duration=5.0, remaining_duration=5.0,
            planned_start=s, planned_finish=f))
    p.build_lookups()
    return p


def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _proj()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _seen():
    return _build_conversation(server._projects["t"]["chat_history"])


# ── the classification ────────────────────────────────────────────────────────

def test_recommend_logic_is_known_to_be_advisory():
    assert is_advisory("recommend_logic")
    assert "recommend_logic" in ADVISORY_ACTIONS


def test_the_action_that_actually_makes_a_tie_is_not_advisory():
    assert not is_advisory("add_relation")


def test_the_check_is_not_case_or_space_sensitive():
    assert is_advisory("  Recommend_Logic  ")


def test_recommend_logic_really_does_change_nothing():
    p = _proj()
    before = len(p.relations)
    ok, _ = apply_commands(p, [{"action": "recommend_logic",
                                "scope": "milestones"}])[0]
    assert ok and len(p.relations) == before


# ── what the turn reports ─────────────────────────────────────────────────────

def _run(c, commands, instruction="wire this up"):
    return c.post("/api/edit", json={"instruction": instruction,
                                     "force_commands": commands}).get_json()


def test_a_turn_of_pure_reports_does_not_claim_edits():
    c = _client()
    body = _run(c, [{"action": "recommend_logic", "scope": "milestones"}])
    assert body["edits_made"] == 0
    assert body["checks_run"] == 1


def test_the_panel_line_says_read_only():
    c = _client()
    _run(c, [{"action": "recommend_logic", "scope": "milestones"}])
    shown = server._projects["t"]["chat_history"][-1]["text"]
    assert "check" in shown and "nothing changed" in shown
    assert "Applied" not in shown


def test_the_agent_is_told_in_writing_that_it_wired_nothing():
    c = _client()
    _run(c, [{"action": "recommend_logic", "scope": "milestones"}])
    seen = _seen()
    assert "REPORT ONLY (nothing changed)" in seen
    assert "schedule was NOT modified" in seen
    assert "Do not tell the user you wired" in seen


def test_a_real_tie_still_counts_as_an_edit():
    c = _client()
    body = _run(c, [{"action": "add_relation", "predecessor_id": "A1",
                     "successor_id": "A2", "type": "fs"}])
    assert body["edits_made"] == 1 and body["checks_run"] == 0
    assert len(server._projects["t"]["project"].relations) == 1


def test_a_mixed_turn_separates_the_two():
    c = _client()
    body = _run(c, [{"action": "recommend_logic", "scope": "milestones"},
                    {"action": "add_relation", "predecessor_id": "A1",
                     "successor_id": "A2", "type": "fs"}])
    assert body["edits_made"] == 1 and body["checks_run"] == 1
    shown = server._projects["t"]["chat_history"][-1]["text"]
    assert "1 edit" in shown and "1 check" in shown


def test_a_mixed_turn_does_not_carry_the_nothing_changed_note():
    """Something DID change — the warning would be false."""
    c = _client()
    _run(c, [{"action": "recommend_logic", "scope": "milestones"},
             {"action": "add_relation", "predecessor_id": "A1",
              "successor_id": "A2", "type": "fs"}])
    assert "schedule was NOT modified" not in _seen()


def test_a_failure_is_still_named_a_failure():
    c = _client()
    body = _run(c, [{"action": "add_relation", "predecessor_id": "NOPE",
                     "successor_id": "A2", "type": "fs"}])
    assert body["commands_failed"] == 1
    assert "failed" in server._projects["t"]["chat_history"][-1]["text"]
    assert "FAILED add_relation" in _seen()


# ── the agent is instructed, not just recorded against ────────────────────────

def test_the_prompt_states_that_a_report_is_not_an_edit():
    assert "A REPORT IS NOT AN EDIT" in SYSTEM_PROMPT


def test_the_prompt_names_the_only_action_that_makes_a_tie():
    assert "The ONLY action that creates a relationship is add_relation" in SYSTEM_PROMPT


def test_the_prompt_forbids_announcing_intent_as_completion():
    assert "Announcing intent is not doing it" in SYSTEM_PROMPT
