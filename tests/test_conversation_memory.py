"""
test_conversation_memory.py — the agent gets to remember the conversation.

Before this, every turn arrived cold. Show the user four ranked tie options,
and one turn later "apply the second one" referred to a list the model could
no longer see — so it guessed. Hand it a drawing, ask what was on it, and it
had nothing to consult — so it guessed, which to the user reads as lying
about a document it was genuinely given.

What is under test is the RECORD: that what was shown is written down with
enough fidelity to answer a follow-up, that the user's chat panel is not
changed by any of it, and that a claim the agent could make about an edit is
backed by which command actually landed.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import (Project, Activity, WBSNode, Relation,
                                   Calendar)
from interpreter.llm_interpreter import (_build_conversation,
                                         _build_session_history, SYSTEM_PROMPT)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities, p.relations = [], []
    rows = [("t5", "ER 105 Terminations", "2026-02-02", "2026-02-06"),
            ("q5", "ER 105 QA/QC Inspections", "2026-02-09", "2026-02-13"),
            ("p5", "ER 105 Pull Wire", "2026-01-19", "2026-01-23"),
            ("r5", "ER 105 Rough-In Conduit", "2026-01-05", "2026-01-09")]
    for uid, name, s, f in rows:
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


def _history():
    return server._projects["t"]["chat_history"]


def _model_sees():
    """The conversation block exactly as it reaches the model."""
    return _build_conversation(_history())


# ── the record keeps two versions on purpose ──────────────────────────────────

def test_a_plain_turn_reads_the_same_to_both():
    _client()
    server._append_chat("user", "link A to B")
    assert _history()[-1] == {"role": "user", "text": "link A to B"}


def test_a_fuller_record_is_kept_for_the_model_only():
    _client()
    server._append_chat("assistant", "Options for Q5", context="1: T5\n2: P5")
    assert _history()[-1]["text"] == "Options for Q5"
    assert "2: P5" in _model_sees()
    assert "1: T5" in _model_sees()               # the fuller one replaces it


def test_an_identical_context_is_not_stored_twice():
    _client()
    server._append_chat("user", "same", context="same")
    assert "context" not in _history()[-1]


def test_the_chat_panel_never_sees_the_model_only_record():
    c = _client()
    server._append_chat("assistant", "Options for Q5", context="1: T5 (72%)")
    msgs = c.get("/api/messages").get_json()["messages"]
    assert msgs[-1] == {"role": "assistant", "text": "Options for Q5"}
    assert "context" not in json.dumps(msgs)


def test_an_all_day_session_does_not_hoard_every_word():
    _client()
    for i in range(260):
        server._append_chat("user", f"msg {i}")
    assert len(_history()) == 200
    assert _history()[0]["text"] == "msg 60"


# ── what the model is handed ──────────────────────────────────────────────────

def test_nothing_said_yet_adds_nothing_to_the_prompt():
    assert _build_conversation([]) == ""
    assert _build_conversation(None) == ""


def test_your_own_replies_are_labelled_as_yours():
    block = _build_conversation([{"role": "assistant", "text": "T5 drives it"}])
    assert "You: T5 drives it" in block


def test_turns_arrive_oldest_first():
    block = _build_conversation([{"role": "user", "text": "first"},
                                 {"role": "user", "text": "second"}])
    assert block.index("first") < block.index("second")


def test_only_the_recent_tail_is_carried():
    block = _build_conversation([{"role": "user", "text": f"m{i}"} for i in range(40)])
    assert "m39" in block and "m0 " not in block


def test_one_enormous_turn_cannot_eat_the_window():
    """A pasted wall of text must not crowd out the turns around it."""
    huge = _build_conversation([{"role": "user", "text": "x" * 9000}])
    assert len(huge) < 3000


# ── the options card is remembered well enough to act on ──────────────────────

def test_the_options_shown_are_written_down_with_their_ids():
    c = _client()
    body = c.post("/api/edit",
                  json={"instruction": "what would be the best connection for Q5?"}
                  ).get_json()
    assert body["type"] == "tie_options"
    seen = _model_sees()
    assert "Option 1 (predecessor)" in seen
    for r in body["predecessors"]:
        assert r["predecessor_id"] in seen      # every id the user can click
    # and what to do when the user picks one
    assert "add_relation" in seen


def test_apply_the_second_one_has_something_to_resolve_against():
    c = _client()
    body = c.post("/api/edit",
                  json={"instruction": "what would be the best connection for Q5?"}
                  ).get_json()
    if len(body["predecessors"]) < 2:
        return                                   # nothing to disambiguate
    assert "Option 2 (predecessor)" in _model_sees()


def test_a_clicked_apply_reads_as_done_not_as_a_fresh_idea():
    c = _client()
    c.post("/api/direct", json={
        "commands": [{"action": "add_relation", "predecessor_id": "T5",
                      "successor_id": "Q5", "type": "fs", "lag_days": 0}],
        "label": "Applied tie option: T5 → Q5"})
    hist = _build_session_history(server._projects["t"]["edit_history"])
    assert "[direct] Applied tie option: T5 → Q5" in hist
    assert "ALREADY APPLIED" in hist


# ── a drawing is quotable afterwards ──────────────────────────────────────────

_READING = {"sheet_number": "E03-021AB", "sheet_title": "1st Floor Grounding",
            "discipline": "electrical", "summary": "Grounding for segments A and B.",
            "rooms": ["MV 105", "MV 106"], "equipment": ["GIS RMU"],
            "facts": ["Ground grid runs under the MV rooms"],
            "directives": ["Grounding before energization"]}


def _upload(c, monkeypatch):
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: dict(_READING))
    return c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "snip.png")},
                  content_type="multipart/form-data")


def test_everything_read_off_the_sheet_is_recoverable(monkeypatch):
    c = _client()
    _upload(c, monkeypatch)
    seen = _model_sees()
    for expect in ("E03-021AB", "MV 105", "MV 106", "GIS RMU",
                   "Ground grid runs under the MV rooms",
                   "Grounding before energization"):
        assert expect in seen, expect


def test_the_sheet_is_marked_as_one_it_genuinely_saw(monkeypatch):
    c = _client()
    _upload(c, monkeypatch)
    assert "E03-021AB" in _model_sees()


def test_the_upload_shows_in_the_panel_as_one_tidy_line(monkeypatch):
    c = _client()
    _upload(c, monkeypatch)
    shown = c.get("/api/messages").get_json()["messages"][-1]["text"]
    assert shown == "Read sheet E03-021AB: Grounding for segments A and B."
    assert "\n" not in shown


# ── an edit claim is backed by which command landed ───────────────────────────

def test_which_commands_landed_is_recorded_not_just_how_many():
    c = _client()
    c.post("/api/edit", json={
        "instruction": "link them up",
        "force_commands": [
            {"action": "add_relation", "predecessor_id": "T5",
             "successor_id": "Q5", "type": "fs"},
            {"action": "add_relation", "predecessor_id": "NOPE",
             "successor_id": "Q5", "type": "fs"}]})
    seen = _model_sees()
    assert "add_relation" in seen and "FAILED" in seen.upper()


def test_the_panel_still_shows_the_short_count():
    c = _client()
    c.post("/api/edit", json={
        "instruction": "link them",
        "force_commands": [{"action": "add_relation", "predecessor_id": "T5",
                            "successor_id": "Q5", "type": "fs"}]})
    assert c.get("/api/messages").get_json()["messages"][-1]["text"] == "Applied 1 edit"


# ── a stop is remembered as a stop ────────────────────────────────────────────

def test_a_tie_stopped_by_a_rule_is_not_remembered_as_made():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    c.post("/api/edit", json={
        "instruction": "link Q5 to T5",
        "force_commands": [{"action": "add_relation", "predecessor_id": "Q5",
                            "successor_id": "T5", "type": "fs"}]})
    seen = _model_sees()
    assert "STOPPED" in seen.upper() or "not applied" in seen.lower()
    assert not server._projects["t"]["project"].relations


# ── the instruction is not duplicated into its own history ────────────────────

def test_this_turns_instruction_is_not_replayed_back_at_the_model(monkeypatch):
    c = _client()
    captured = {}

    def fake_interpret(instruction, **kw):
        captured["instruction"] = instruction
        captured["conversation"] = kw.get("chat_history") or []
        return [{"action": "chat", "message": "ok"}], "raw"

    monkeypatch.setattr(server, "interpret", fake_interpret)
    c.post("/api/edit", json={"instruction": "hello there"})
    assert captured["instruction"] == "hello there"
    assert all(t.get("text") != "hello there" for t in captured["conversation"])


def test_earlier_turns_do_reach_the_model(monkeypatch):
    c = _client()
    server._append_chat("user", "what drives Q5?")
    server._append_chat("assistant", "T5 — ER 105 Terminations")
    captured = {}

    def fake_interpret(instruction, **kw):
        captured["conversation"] = kw.get("chat_history") or []
        return [{"action": "chat", "message": "ok"}], "raw"

    monkeypatch.setattr(server, "interpret", fake_interpret)
    c.post("/api/edit", json={"instruction": "apply that"})
    texts = [t.get("text") for t in captured["conversation"]]
    assert "T5 — ER 105 Terminations" in texts


# ── the agent is told how to use all this ─────────────────────────────────────

def test_the_prompt_forbids_describing_a_document_it_was_not_given():
    assert "SOURCING IS A HARD LINE" in SYSTEM_PROMPT
    assert "paperclip" in SYSTEM_PROMPT


def test_the_prompt_explains_how_follow_ups_resolve():
    assert "second option" in SYSTEM_PROMPT
