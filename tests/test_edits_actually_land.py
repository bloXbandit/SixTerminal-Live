"""
test_edits_actually_land.py — why the agent "rarely makes executions".

Reported from real use, and it was not the model being lazy. Three separate
places quietly dropped work on the floor:

  1. The reply parser matched `\\[.*\\]` greedily. A model that emitted perfect
     commands and then added "see [DCMA 4]" produced a span from the first
     bracket to that last one, which cannot parse — so the whole reply fell
     through to "treat it as chat" and every edit vanished. Same for a single
     command returned as a bare object, for a trailing comma, and for two
     arrays in one reply.

  2. A schedule screenshot produced a diff with an Apply button, and nothing
     else. A user who answered "yes, do it" in words got a polite offer to
     help rather than the edit they had just asked for.

  3. The model list was a whitelist, so a key with access to a newer model
     could not use it until this file changed.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import Project, Activity, WBSNode, Calendar
from interpreter.llm_interpreter import _parse_commands, resolve_model, MODELS


def _edits(cmds):
    return [c for c in cmds if c.get("action") not in ("chat", "clarify")]


# ── 1. the parser must not throw edits away ──────────────────────────────────

_TIE = '{"action":"add_relation","predecessor_id":"A","successor_id":"B"}'


def test_a_clean_array_still_works():
    assert len(_edits(_parse_commands(f"[{_TIE}]"))) == 1


def test_prose_on_either_side_is_ignored():
    assert len(_edits(_parse_commands(f"Sure, wiring these now.\n[{_TIE}]"))) == 1
    assert len(_edits(_parse_commands(f"[{_TIE}]\nThat ties A into B."))) == 1


def test_a_bracket_in_the_note_afterwards_no_longer_eats_the_commands():
    """The exact greedy-regex failure: [ … ] … [DCMA 4]."""
    raw = f"[{_TIE}]\nNote: see [DCMA metric 4] on relationship types."
    assert len(_edits(_parse_commands(raw))) == 1


def test_one_command_returned_as_a_bare_object_is_accepted():
    assert len(_edits(_parse_commands(_TIE))) == 1


def test_commands_wrapped_in_an_object_are_accepted():
    assert len(_edits(_parse_commands('{"commands":[%s]}' % _TIE))) == 1


def test_a_trailing_comma_does_not_cost_the_edit():
    assert len(_edits(_parse_commands(f"[{_TIE},]"))) == 1


def test_a_chat_array_does_not_hide_the_edits_after_it():
    raw = '[{"action":"chat","message":"ok"}]\nand then\n[%s]' % _TIE
    assert len(_edits(_parse_commands(raw))) == 1


def test_a_fenced_block_is_unwrapped():
    assert len(_edits(_parse_commands(f"```json\n[{_TIE}]\n```"))) == 1


def test_a_long_batch_survives_a_trailing_sentence():
    body = ",".join('{"action":"add_relation","predecessor_id":"P%d",'
                    '"successor_id":"S%d"}' % (i, i) for i in range(50))
    assert len(_edits(_parse_commands(f"[{body}]\nWired [50] ties."))) == 50


def test_genuine_prose_is_still_shown_as_chat():
    out = _parse_commands("I would tie Deep Foundations into Start Precast.")
    assert out[0]["action"] == "chat" and "Deep Foundations" in out[0]["message"]


def test_prose_that_merely_contains_brackets_is_still_chat():
    out = _parse_commands("See [DCMA metric 4] — over 90% should be FS.")
    assert out[0]["action"] == "chat"


def test_an_empty_reply_does_not_crash():
    assert _parse_commands("")[0]["action"] == "chat"
    assert _parse_commands(None)[0]["action"] == "chat"


# ── 2. saying yes to a screenshot diff applies it ────────────────────────────

def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities, p.relations = [], []
    p.activities.append(Activity(
        uid="a1", activity_id="A1", name="Pull Wire MV 105", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=5.0, remaining_duration=5.0,
        planned_start="2026-02-02", planned_finish="2026-02-06"))
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


_READ = {"source_title": "Owner Lookahead", "data_date": "2026-03-02",
         "rows": [{"activity_id": "A1", "name": "Pull Wire MV 105",
                   "start": None, "finish": "2026-02-27",
                   "actual_start": "2026-02-02", "actual_finish": None,
                   "percent_complete": None, "status": None}],
         "notes": []}


def _upload(c, monkeypatch, question="match my dates to this"):
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_schedule", lambda *a, **k: dict(_READ))
    return c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "shot.png"),
                        "question": question},
                  content_type="multipart/form-data").get_json()


def _act():
    return server._projects["t"]["project"].get_activity(activity_id="A1")


def test_saying_yes_applies_the_diff():
    c = _client()
    import interpreter.vision as vz
    _upload(c, __import__("pytest").MonkeyPatch())
    body = c.post("/api/edit", json={"instruction": "yes"}).get_json()
    assert body.get("commands_applied", 0) >= 1
    assert _act().planned_finish == "2026-02-27"


def test_do_it_works_the_same_way():
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    c.post("/api/edit", json={"instruction": "do it"})
    assert _act().planned_finish == "2026-02-27"


def test_yes_does_not_quietly_write_an_actual_date():
    """"Yes" answers the question that was asked — it was about dates."""
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    c.post("/api/edit", json={"instruction": "yes"})
    assert not _act().actual_start


def test_reaching_for_the_actuals_in_words_includes_them():
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    c.post("/api/edit", json={"instruction": "yes, include the actuals"})
    assert _act().actual_start == "2026-02-02"


def test_the_diff_is_consumed_so_a_later_yes_does_not_reapply_it():
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    c.post("/api/edit", json={"instruction": "yes"})
    assert server._projects["t"].get("pending_sheet") is None


def test_yes_with_nothing_pending_is_left_to_the_model(monkeypatch):
    c = _client()
    captured = {}

    def fake(instruction, **kw):
        captured["seen"] = instruction
        return [{"action": "chat", "message": "yes to what?"}], "raw"

    monkeypatch.setattr(server, "interpret", fake)
    c.post("/api/edit", json={"instruction": "yes"})
    assert captured["seen"] == "yes"


def test_a_yes_that_reaches_further_is_left_to_the_model(monkeypatch):
    from server import _is_apply_yes
    assert not _is_apply_yes("yes, then delete the milestones")
    assert not _is_apply_yes("no")


def test_a_real_instruction_is_never_swallowed_as_a_yes(monkeypatch):
    """"yes, now delete everything" must not be read as a bare confirmation."""
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    captured = {}

    def fake(instruction, **kw):
        captured["seen"] = instruction
        return [{"action": "chat", "message": "ok"}], "raw"

    monkeypatch.setattr(server, "interpret", fake)
    c.post("/api/edit", json={"instruction": "yes but first rename A1 to Foo"})
    assert captured.get("seen", "").startswith("yes but first rename A1 to Foo")
    assert _act().planned_finish == "2026-02-06"      # untouched


def test_an_applied_sheet_is_undoable():
    c = _client()
    _upload(c, __import__("pytest").MonkeyPatch())
    c.post("/api/edit", json={"instruction": "apply"})
    c.post("/api/undo")
    assert _act().planned_finish == "2026-02-06"


# ── 3. any model the key can reach ───────────────────────────────────────────

def test_the_named_presets_still_resolve():
    for key, cfg in MODELS.items():
        assert resolve_model(key)["model_id"] == cfg["model_id"]


def test_a_model_that_is_not_in_the_list_is_passed_through():
    """New models ship faster than this file changes."""
    r = resolve_model("gpt-5.2")
    assert r["model_id"] == "gpt-5.2" and r["provider"] == "openai"


def test_a_dated_model_id_is_passed_through_too():
    assert resolve_model("gpt-5.2-2026-01-15")["model_id"] == "gpt-5.2-2026-01-15"


def test_the_provider_is_read_from_the_name():
    assert resolve_model("claude-opus-4-6")["provider"] == "anthropic"
    assert resolve_model("o4-mini")["provider"] == "openai"


def test_nothing_chosen_falls_back_to_the_default():
    from interpreter.llm_interpreter import DEFAULT_MODEL
    assert resolve_model("")["model_id"] == MODELS[DEFAULT_MODEL]["model_id"]
    assert resolve_model(None)["model_id"] == MODELS[DEFAULT_MODEL]["model_id"]


def test_settings_accepts_a_model_id_it_has_never_heard_of():
    c = _client()
    body = c.post("/api/settings", json={"model_key": "gpt-5.2"}).get_json()
    assert body["success"] and body["model_key"] == "gpt-5.2"


def test_settings_still_refuses_an_empty_model():
    c = _client()
    assert c.post("/api/settings", json={"model_key": "  "}).status_code == 400


def test_listing_models_without_a_key_says_so():
    c = _client()
    server._settings["api_key"] = None
    old = os.environ.pop("OPENAI_API_KEY", None)
    try:
        r = c.get("/api/models/available")
        assert r.status_code == 400 and "key" in r.get_json()["error"].lower()
    finally:
        if old:
            os.environ["OPENAI_API_KEY"] = old
