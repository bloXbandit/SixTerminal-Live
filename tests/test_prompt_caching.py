"""
test_prompt_caching.py — the same request, billed once instead of every turn.

Measured against a real 2,776-activity schedule: the system prompt runs about
11,500 tokens and the schedule context about 22,000 — roughly 33,500 tokens
that were byte-identical from one call to the next in the same session (the
schedule context only changes when an edit actually touches the project).
Everything that genuinely varies turn to turn — conversation history, session
history, the instruction itself — came to under 2,000 tokens for a realistic
16-turn exchange. Over 90% of every request was the same bytes, billed again,
purely because they were flattened into one string before reaching the API.

What is under test here is the SHAPE of the outgoing request, not the model's
answer: that the static part (system prompt, schedule context) sits in its own
stable block so Anthropic's explicit cache_control and OpenAI's automatic
prefix caching can both actually find it, that nothing in that block changes
between two calls with the same schedule (so it stays a cache HIT), that it
DOES change the moment the schedule genuinely differs (so stale content is
never served), and that no information is lost by the split — the model still
receives everything it received before, just organized differently.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import types

import interpreter.llm_interpreter as li


# ── a fake Anthropic client, since the real package is not installed here ────
# `_ANTHROPIC_AVAILABLE` is a plain module-level flag interpret() reads before
# using the `anthropic` name, so both can be substituted directly regardless
# of whether the real package ever imported successfully.

class _FakeAnthropicResponse:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(text=text)]


class _FakeAnthropicMessages:
    def __init__(self, sink):
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeAnthropicResponse('[{"action": "chat", "message": "ok"}]')


class _FakeAnthropicClient:
    def __init__(self, calls, **_kw):
        self.messages = _FakeAnthropicMessages(calls)


def _fake_anthropic_module(calls):
    mod = types.SimpleNamespace()
    mod.Anthropic = lambda **kw: _FakeAnthropicClient(calls, **kw)
    return mod


def _anthropic_call(monkeypatch, instruction="wire this up", project_summary=None,
                    chat_history=None, edit_history=None):
    calls = []
    monkeypatch.setattr(li, "_ANTHROPIC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(li, "anthropic", _fake_anthropic_module(calls), raising=False)
    li.interpret(instruction, project_summary=project_summary,
                chat_history=chat_history, edit_history=edit_history,
                model_key="claude", api_key="test-key")
    return calls[0]


# ── the static block gets its own cache_control breakpoint ───────────────────

def test_the_system_prompt_is_its_own_cached_block(monkeypatch):
    kw = _anthropic_call(monkeypatch)
    system = kw["system"]
    assert isinstance(system, list)
    assert system[0]["text"] == li.SYSTEM_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_the_schedule_context_is_a_second_cached_block(monkeypatch):
    kw = _anthropic_call(monkeypatch, project_summary="Project: DC (25-1539)\n...")
    system = kw["system"]
    assert len(system) == 2
    assert "Project: DC (25-1539)" in system[1]["text"]
    assert system[1]["cache_control"] == {"type": "ephemeral"}


def test_no_schedule_context_means_no_empty_second_block(monkeypatch):
    """An empty cached block is pure waste — never emit one with nothing in it."""
    kw = _anthropic_call(monkeypatch, project_summary=None)
    assert len(kw["system"]) == 1


def test_the_schedule_context_no_longer_lives_inside_the_user_message(monkeypatch):
    """The whole point of the split — it has to actually leave the flattened
    string, or moving it into `system` accomplishes nothing."""
    kw = _anthropic_call(monkeypatch, instruction="what should I tie next?",
                         project_summary="UNIQUE-MARKER-9182")
    user_content = kw["messages"][0]["content"]
    assert "UNIQUE-MARKER-9182" not in user_content
    assert "UNIQUE-MARKER-9182" in kw["system"][1]["text"]


# ── nothing is lost — same information, different container ──────────────────

def test_the_instruction_conversation_and_edit_history_all_still_arrive(monkeypatch):
    kw = _anthropic_call(
        monkeypatch, instruction="apply the second one",
        project_summary="schedule context here",
        chat_history=[{"role": "user", "text": "what should X connect to?"},
                      {"role": "assistant", "text": "Options",
                       "context": "Option 2 (predecessor): add_relation P -> S"}],
        edit_history=[{"instruction": "earlier edit", "commands": [],
                       "results": [{"action": "add_relation", "success": True,
                                   "message": "Added Finish to Start relation"}]}])
    user_content = kw["messages"][0]["content"]
    assert "apply the second one" in user_content
    assert "Option 2 (predecessor)" in user_content
    assert "Added Finish to Start relation" in user_content


# ── the property that makes caching actually work ────────────────────────────

def test_two_calls_with_the_same_schedule_produce_byte_identical_static_blocks(monkeypatch):
    """This is what a cache hit depends on: identical bytes, not just
    equivalent content."""
    kw1 = _anthropic_call(monkeypatch, project_summary="schedule v1")
    kw2 = _anthropic_call(monkeypatch, instruction="a different question",
                          project_summary="schedule v1")
    assert kw1["system"] == kw2["system"]


def test_the_system_prompt_block_stays_identical_even_when_the_schedule_changes(monkeypatch):
    """After an edit the schedule context legitimately differs — but the
    system-prompt block must still hit cache on its own, independent of it."""
    kw1 = _anthropic_call(monkeypatch, project_summary="schedule v1")
    kw2 = _anthropic_call(monkeypatch, project_summary="schedule v2, after an edit")
    assert kw1["system"][0] == kw2["system"][0]
    assert kw1["system"][1] != kw2["system"][1]


# ── OpenAI: no explicit cache_control API, but the same stable-prefix shape ──
# OpenAI caches automatically off the longest identical prefix of `messages`
# across calls — no marker needed, it just needs the static content to sit in
# its own message, in the same position, every time.

def _openai_call(monkeypatch, instruction="wire this up", project_summary=None,
                 chat_history=None, edit_history=None):
    calls = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            msg = types.SimpleNamespace(content='[{"action": "chat", "message": "ok"}]')
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, **_kw):
            self.chat = _FakeChat()

    monkeypatch.setattr(li, "_OPENAI_AVAILABLE", True, raising=False)
    monkeypatch.setattr(li, "OpenAI", _FakeOpenAIClient, raising=False)
    li.interpret(instruction, project_summary=project_summary,
                chat_history=chat_history, edit_history=edit_history,
                model_key="gpt-4.1-mini", api_key="test-key")
    return calls[0]


def test_openai_keeps_the_system_prompt_and_schedule_context_as_separate_leading_messages(monkeypatch):
    kw = _openai_call(monkeypatch, project_summary="Project: DC")
    msgs = kw["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == li.SYSTEM_PROMPT
    assert msgs[1]["role"] == "system" and "Project: DC" in msgs[1]["content"]


def test_openai_with_no_schedule_context_has_no_empty_middle_message(monkeypatch):
    """
    With nothing to cache in a second block, msgs[1] must be the user turn,
    not an empty schedule-context slot. (A trailing JSON-mode instruction is
    appended separately, after the user turn, by an unrelated fix — that one
    is expected and is not what this checks.)
    """
    kw = _openai_call(monkeypatch, project_summary=None)
    assert kw["messages"][0]["role"] == "system"
    assert kw["messages"][1]["role"] == "user"


def test_openai_message_order_is_stable_across_calls(monkeypatch):
    """Automatic prefix caching needs this specifically: identical ORDER,
    identical bytes, every time — not just the same information."""
    kw1 = _openai_call(monkeypatch, project_summary="schedule v1")
    kw2 = _openai_call(monkeypatch, instruction="something else",
                       project_summary="schedule v1")
    assert kw1["messages"][:2] == kw2["messages"][:2]
