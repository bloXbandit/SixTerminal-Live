"""
test_autosave_is_off_the_request_path.py — the answer must not wait for the save.

Reported from the deployed app: a 502 with an empty body, "right as the agent
interprets". The cause was not memory, which the obvious guess said it was.
Autosave ran in @app.after_request — so every response waited for the whole
schedule to be serialized to P6 XML and uploaded to R2. Measured on a
2,776-activity file that is 5.25 seconds and 10.6 MB, landing on top of an LLM
call the user had already waited half a minute for. Long enough for the proxy
in front of the app to give up, drop the connection, and hand the browser
nothing at all.

The save is real work and still has to happen. It has no business being
between the user and their answer.

What is defended here: the response goes out while the save is still running,
the save genuinely still happens, a burst of edits collapses into one upload
instead of one per edit, and a save that fails cannot take a request — or the
process — down with it.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine import cloud_store
from engine.schedule_model import Activity, Calendar, Project, Relation, WBSNode
from tests.test_cloud_persistence import _install_fake


@pytest.fixture(autouse=True)
def _clean():
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    with server._dirty_lock:
        server._dirty_pids.clear()
    yield
    with server._dirty_lock:
        server._dirty_pids.clear()


def _proj(pid="t"):
    p = Project(uid="p", name="DC", id=pid, data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E")]
    p.activities = [
        Activity(uid="a1", activity_id="A1", name="Pull Wire", wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-02",
                 planned_finish="2026-02-06"),
        Activity(uid="a2", activity_id="A2", name="Terminations", wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-09",
                 planned_finish="2026-02-13"),
    ]
    p.relations = []
    p.build_lookups()
    return p


def _client(pid="t"):
    sess = server._make_session(pid, f"{pid}.xml")
    sess["project"] = _proj(pid)
    server._projects[pid] = sess
    server._active_id[0] = pid
    return server.app.test_client()


class _SlowS3:
    """
    A cloud that takes its time, like a real 10MB upload does.

    The whole bug was that this latency sat inside the request, so a fake that
    returns instantly could not have caught it and cannot prove it is fixed.
    """

    def __init__(self, delay=0.4):
        self.delay = delay
        self.puts = []
        self.lock = threading.Lock()

    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        time.sleep(self.delay)
        with self.lock:
            self.puts.append(Key)
        return {}

    def get_object(self, Bucket=None, Key=None, **kw):
        raise Exception("not found")

    def delete_object(self, **kw):
        return {}

    def get_paginator(self, _name):
        class _P:
            def paginate(self, **kw):
                return iter([{"Contents": []}])
        return _P()

    def saved_projects(self):
        with self.lock:
            return {k.rsplit("/", 1)[-1].rsplit(".", 1)[0] for k in self.puts}


def _install_slow(delay=0.4):
    _install_fake()                      # sets the R2_* vars
    fake = _SlowS3(delay)
    cloud_store._client_cache.clear()
    cloud_store._client_cache.append(fake)
    return fake


def _edit(c, instruction="tie them"):
    return c.post("/api/edit", json={
        "instruction": instruction,
        "force_commands": [{"action": "add_relation", "predecessor_id": "A1",
                            "successor_id": "A2", "type": "fs"}]})


# ── the response does not wait ───────────────────────────────────────────────

def test_an_edit_answers_without_waiting_for_the_upload():
    """The actual bug. With the save inline this took as long as the upload;
    the user's real upload was 10MB on top of a 30-second model call."""
    slow = _install_slow(delay=1.0)
    c = _client()
    started = time.time()
    resp = _edit(c)
    took = time.time() - started
    assert resp.status_code == 200
    assert took < 0.5, (f"the response waited {took:.2f}s for the save — "
                        f"that is the 502 all over again")
    assert slow.puts == [], "the upload had not even started yet"


def test_the_save_still_actually_happens():
    """Off the request path must not mean 'not at all'."""
    slow = _install_slow(delay=0.05)
    c = _client()
    _edit(c)
    server.flush_now()
    assert "t" in slow.saved_projects()


def test_a_burst_of_edits_collapses_into_one_save():
    """Twelve ties applied in a row would otherwise be twelve full 10MB
    uploads of the same schedule."""
    slow = _install_slow(delay=0.05)
    c = _client()
    for _ in range(6):
        _edit(c)
    server.flush_now()
    assert len([k for k in slow.puts if k.endswith(".xml")]) == 1


def test_the_background_saver_gets_there_on_its_own(monkeypatch):
    """
    Nothing calls flush_now in production — the thread has to do it.

    The saver is one process-wide thread that outlives any single test, so it
    may already be part-way through a debounce when this starts. The wait
    therefore has to cover a full cycle of the REAL debounce, not the short
    one patched in here — polling, so it returns as soon as the save lands.
    """
    monkeypatch.setattr(server, "_SAVE_DEBOUNCE_SECONDS", 0.05)
    slow = _install_slow(delay=0.02)
    c = _client()
    _edit(c)
    deadline = time.time() + server._SAVE_DEBOUNCE_SECONDS + 8.0
    while time.time() < deadline:
        if "t" in slow.saved_projects():
            break
        time.sleep(0.05)
    assert "t" in slow.saved_projects(), "the background saver never ran"


def test_only_one_saver_thread_is_ever_started():
    _install_slow(delay=0.02)
    c = _client()
    for _ in range(5):
        _edit(c)
    threads = [t for t in threading.enumerate() if t.name == "cloud-saver"]
    assert len(threads) == 1


# ── failures stay contained ──────────────────────────────────────────────────

def test_a_save_that_throws_does_not_break_the_request():
    class _Broken(_SlowS3):
        def put_object(self, **kw):
            raise RuntimeError("R2 is having a day")

    _install_fake()
    cloud_store._client_cache.clear()
    cloud_store._client_cache.append(_Broken(0))
    c = _client()
    assert _edit(c).status_code == 200
    server.flush_now()                   # must not raise either


def test_a_project_that_vanished_before_the_save_is_skipped():
    """The background save runs later, and 'later' includes after the project
    was closed."""
    _install_slow(delay=0.02)
    c = _client()
    _edit(c)
    server._projects.clear()
    assert server.flush_now() == []


# ── the manual Save button is still synchronous, on purpose ─────────────────

def test_the_save_button_still_waits_and_reports():
    """The user pressed it deliberately and wants to be told it worked."""
    slow = _install_slow(delay=0.02)
    c = _client()
    body = c.post("/api/cloud/save").get_json()
    assert body["success"] and "t" in body["saved"]
    assert "t" in slow.saved_projects(), "it reported success without saving"


def test_the_save_button_clears_what_the_background_saver_was_holding():
    """Everything is on disk now, so a queued save is redundant work."""
    _install_slow(delay=0.02)
    c = _client()
    _edit(c)
    assert server._dirty_pids
    c.post("/api/cloud/save")
    assert not server._dirty_pids


# ── with no cloud configured, none of this engages ──────────────────────────

def test_nothing_is_queued_when_there_is_no_cloud():
    cloud_store.reset_client()
    cloud_store._client_cache.append(None)
    c = _client()
    assert _edit(c).status_code == 200
    assert not server._dirty_pids
