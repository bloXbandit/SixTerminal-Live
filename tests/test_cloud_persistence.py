"""
test_cloud_persistence.py — a Render restart must not cost the schedule, the
conversation, or what the agent was taught.

test_cloud_store.py proves the low-level save/load_all mechanics work against
a fake S3 surface. Nothing until now proved the layer ON TOP of it — the
actual server.py orchestration the app runs — carries everything a restart
would otherwise destroy: the project, the chat history the agent reasons
from, and the project-brain rules the user taught it. That gap is closed
here, end to end: through the real `_persist`/`_restore_from_cloud` functions
and the real HTTP routes, with only the S3 transport faked (the same
in-memory fake test_cloud_store.py already uses — this exercises the genuine
save → serialize → list → parse → rehydrate path, not a mocked shortcut).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine import cloud_store, project_brain
from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation
from tests.test_cloud_store import FakeS3


@pytest.fixture(autouse=True)
def _clean_slate():
    """
    server._projects / server._brains are module-level and shared by every
    test in the process. Without this, a rule taught to "25-1539-INT-1" in
    one test is still sitting in server._brains for the NEXT test that
    reuses the same project id — a real cross-test leak, not a restore bug.
    """
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    server._dirty_pids.clear()
    yield


def _install_fake():
    """
    A fully "configured" environment, not just enough to satisfy _client().

    is_configured() (gates the save/restore code paths) only checks whether
    a client is memoized — but status() (what the UI's cloud badge and the
    Save-to-cloud button's visibility actually read) independently re-checks
    all four R2_* variables via _env_ready(), even with a client already
    cached. Setting only R2_BUCKET reproduced that split and made the status
    route look unconfigured despite a working fake client underneath it —
    which is a real fixture gap, not a bug in cloud_store.py: production
    never hits it, because _client() only ever populates the cache AFTER
    _env_ready() passes. Setting all four here is what actually mirrors the
    user's real, now-configured Render environment.
    """
    os.environ["R2_ACCOUNT_ID"] = "test-account"
    os.environ["R2_ACCESS_KEY_ID"] = "test-key"
    os.environ["R2_SECRET_ACCESS_KEY"] = "test-secret"
    os.environ["R2_BUCKET"] = "test-bucket"
    cloud_store.reset_client()
    fake = FakeS3()
    cloud_store._client_cache.clear()
    cloud_store._client_cache.append(fake)
    # A fresh bucket has none of the history the previous one had, so the
    # record of what was last written to it must go too — otherwise a save is
    # skipped as "unchanged" against a bucket that never received it.
    server._last_saved_digest.clear()
    return fake


def _wipe_all_memory():
    """What a Render restart actually does to this process: empties it."""
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    server._dirty_pids.clear()


def _proj(pid_hint="25-1539-INT-1"):
    p = Project(uid="p", name="DC", id=pid_hint, data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities = [
        Activity(uid="a1", activity_id="A1", name="Pull Wire MV 105", wbs_uid="w",
                calendar_uid="1", activity_type="Task Dependent", status="Not Started",
                planned_duration=5.0, remaining_duration=5.0,
                planned_start="2026-02-02", planned_finish="2026-02-06"),
        Activity(uid="a2", activity_id="A2", name="Terminations MV 105", wbs_uid="w",
                calendar_uid="1", activity_type="Task Dependent", status="Not Started",
                planned_duration=5.0, remaining_duration=5.0,
                planned_start="2026-02-09", planned_finish="2026-02-13"),
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="a1", successor_uid="a2",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    return p


def _seed_session(pid, project, taught=None, chats=None):
    sess = server._make_session(pid, f"{pid}.xml")
    sess["project"] = project
    server._projects[pid] = sess
    server._active_id[0] = pid
    for text in (taught or []):
        server._brain_for(project).add(text, project)
    for role, text in (chats or []):
        server._append_chat(role, text)
    return sess


# ── the schedule itself survives ──────────────────────────────────────────────

def test_the_schedule_survives_a_full_wipe():
    _install_fake()
    p = _proj()
    _seed_session("t", p)
    ok, msg = server._persist("t")
    assert ok, msg

    _wipe_all_memory()
    server._restore_from_cloud()

    assert "t" in server._projects
    back = server._projects["t"]["project"]
    assert len(back.activities) == 2 and len(back.relations) == 1
    assert back.get_activity(activity_id="A2").name == "Terminations MV 105"


def test_the_restored_project_becomes_the_active_one_automatically():
    """So the frontend's normal page-load flow picks it up with no extra
    click — a restore that required the user to manually re-select their
    project would not feel like it 'just worked'."""
    _install_fake()
    _seed_session("t", _proj())
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()
    assert server._active_id[0] == "t"


# ── the conversation survives ──────────────────────────────────────────────────

def test_the_chat_panel_history_survives():
    _install_fake()
    p = _proj()
    _seed_session("t", p, chats=[
        ("user", "wire MV 105 to MV 106"),
        ("assistant", "Tied A1 -> A2."),
    ])
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    hist = server._projects["t"]["chat_history"]
    texts = [m["text"] for m in hist]
    assert "wire MV 105 to MV 106" in texts
    assert "Tied A1 -> A2." in texts


def test_the_agents_own_memory_of_the_conversation_survives_too():
    """Not just what the CHAT PANEL shows — what the model itself reads back
    next turn, including the richer context field a plain text log drops."""
    _install_fake()
    p = _proj()
    sess = _seed_session("t", p)
    server._append_chat("assistant", "Options for A1",
                        context="Option 1 (predecessor): add_relation X -> A1")
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    from interpreter.llm_interpreter import _build_conversation
    seen = _build_conversation(server._projects["t"]["chat_history"])
    assert "Option 1 (predecessor): add_relation X -> A1" in seen


def test_only_the_last_80_turns_are_kept():
    """Persist already caps this on the way out; confirm restore respects it
    rather than silently accepting whatever the manifest happens to hold."""
    _install_fake()
    p = _proj()
    sess = _seed_session("t", p)
    for i in range(120):
        server._append_chat("user", f"turn {i}")
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    hist = server._projects["t"]["chat_history"]
    assert len(hist) <= 80
    assert hist[-1]["text"] == "turn 119"        # the recent tail, not the head


# ── what was taught survives ────────────────────────────────────────────────────

def test_a_taught_project_brain_rule_survives():
    _install_fake()
    p = _proj()
    _seed_session("t", p, taught=["Terminations follow Pull Wire in the same room"])
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    key = project_brain.project_key(server._projects["t"]["project"])
    brain = server._brains.get(key)
    assert brain is not None
    assert any("Terminations" in d.text for d in brain.directives)


def test_a_project_with_nothing_taught_does_not_grow_a_phantom_brain():
    _install_fake()
    p = _proj()
    _seed_session("t", p)          # no rules taught
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    key = project_brain.project_key(server._projects["t"]["project"])
    assert key not in server._brains or not server._brains[key].directives


def test_a_rule_survives_ranking_after_restore_not_just_storage():
    """The point of a taught rule is that it changes the tie ranker's
    output — prove that still works post-restore, not just that the text
    made it back into a list somewhere."""
    _install_fake()
    p = _proj()
    _seed_session("t", p, taught=["Terminations follow Pull Wire in the same room"])
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    from engine.logic_advisor import _Ctx, score_tie, implied_lag
    restored = server._projects["t"]["project"]
    directives = server._brain_for(restored).directives
    ctx = _Ctx(restored, directives)
    a1 = restored.get_activity(activity_id="A1")
    a2 = restored.get_activity(activity_id="A2")
    told, why = score_tie(ctx, a1, a2, implied_lag(restored, a1, a2))
    plain, _ = score_tie(_Ctx(restored, []), a1, a2, implied_lag(restored, a1, a2))
    assert told > plain


# ── the actual HTTP routes the button and page-load use ───────────────────────

def test_the_save_button_route_reports_success_and_a_bucket():
    _install_fake()
    _seed_session("t", _proj())
    c = server.app.test_client()
    body = c.post("/api/cloud/save").get_json()
    assert body.get("success") and "t" in body.get("saved", [])


def test_the_save_button_route_refuses_when_not_configured():
    cloud_store.reset_client()
    cloud_store._client_cache.append(None)     # simulate "not configured"
    _seed_session("t", _proj())
    c = server.app.test_client()
    resp = c.post("/api/cloud/save")
    assert resp.status_code == 400
    assert "isn't configured" in resp.get_json()["error"].lower()


def test_the_status_route_flips_true_once_a_fake_client_is_installed():
    _install_fake()
    c = server.app.test_client()
    body = c.get("/api/cloud/status").get_json()
    assert body["configured"] is True


def test_after_a_wipe_and_restore_status_and_messages_serve_the_restored_session():
    """The exact sequence the frontend runs on every page load: /api/status
    for what project is active, /api/messages for the chat panel."""
    _install_fake()
    p = _proj()
    _seed_session("t", p, chats=[("user", "how's phase 1 tracking")])
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()

    c = server.app.test_client()
    status = c.get("/api/status").get_json()
    assert status.get("loaded") is True
    assert status.get("active_project_id") == "t"

    msgs = c.get("/api/messages").get_json()["messages"]
    assert any(m["text"] == "how's phase 1 tracking" for m in msgs)
