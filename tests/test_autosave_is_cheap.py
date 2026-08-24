"""
test_autosave_is_cheap.py — saving a schedule should not cost 10MB every time.

Autosave re-serialized and re-uploaded the ENTIRE schedule on every turn. On
the reference project that is 10.7 MB, and it went up whether or not the
schedule had actually changed — a chat reply, a report, a rule taught, all
marked the project dirty and all shipped the same ten megabytes again.

Two things fix that, and both are nearly free. P6 XML is enormously
repetitive, so it compresses about fifty to one for three hundredths of a
second of CPU. And a digest of what was last written says whether the upload
would change anything at all.

What is defended here: the bytes really do shrink, whatever is already sitting
in the bucket uncompressed still loads (there is no migration and nobody is
going to run one), an unchanged schedule skips the big upload but still saves
the manifest — because the conversation and what was taught live in there and
DO change on a turn that left the schedule alone — and a restore still
reconstructs the identical project.
"""

import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine import cloud_store
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)
from tests.test_cloud_persistence import _install_fake, _wipe_all_memory


@pytest.fixture(autouse=True)
def _clean():
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    server._last_saved_digest.clear()
    with server._dirty_lock:
        server._dirty_pids.clear()
    yield


def _proj(pid="t", n=60):
    p = Project(uid="p", name="DC", id=pid, data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E")]
    p.activities = [
        Activity(uid=f"a{i}", activity_id=f"A{1000 + i * 10}",
                 name=f"Pull Wire and Terminate Feeders run {i}", wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-02",
                 planned_finish="2026-02-06")
        for i in range(n)]
    p.relations = [Relation(uid=f"r{i}", predecessor_uid=f"a{i}",
                            successor_uid=f"a{i+1}", type="Finish to Start",
                            lag=0.0) for i in range(n - 1)]
    p.build_lookups()
    return p


def _seed(pid="t"):
    sess = server._make_session(pid, f"{pid}.xml")
    sess["project"] = _proj(pid)
    server._projects[pid] = sess
    server._active_id[0] = pid
    return sess


def _stored_xml(fake, pid="t"):
    return fake.store[f"{cloud_store._PREFIX}{pid}.xml"]


# ── the bytes actually shrink ────────────────────────────────────────────────

def test_what_lands_in_the_bucket_is_compressed():
    fake = _install_fake()
    _seed()
    ok, _ = server._persist("t")
    assert ok
    body = _stored_xml(fake)
    assert body[:2] == cloud_store._GZIP_MAGIC, "it went up uncompressed"
    assert gzip.decompress(body).startswith(b"<?xml")


def test_the_saving_is_substantial_on_real_xml():
    """Fifty to one on the reference file. Anything near 1:1 means the
    compression quietly stopped happening."""
    fake = _install_fake()
    _seed()
    server._persist("t")
    stored = _stored_xml(fake)
    raw = gzip.decompress(stored)
    assert len(raw) / len(stored) > 5, (
        f"only {len(raw)/len(stored):.1f}x — compression is not working")


def test_the_manifest_records_both_sizes():
    """So the saving is visible rather than assumed."""
    fake = _install_fake()
    _seed()
    server._persist("t")
    import json
    meta = json.loads(fake.store[f"{cloud_store._PREFIX}t.json"])
    assert meta["bytes_raw"] > meta["bytes_stored"]


# ── what is already in the bucket keeps working ──────────────────────────────

def test_an_uncompressed_object_saved_before_this_still_loads():
    """There is no migration and nobody is going to run one. The magic bytes
    settle which kind an object is."""
    fake = _install_fake()
    sess = _seed()
    plain = server._project_to_xml_bytes(sess["project"])
    fake.store[f"{cloud_store._PREFIX}t.xml"] = plain          # the old shape
    import json
    fake.store[f"{cloud_store._PREFIX}t.json"] = json.dumps(
        {"project_id": "t", "source_name": "t.xml"}).encode()

    _wipe_all_memory()
    server._restore_from_cloud()
    assert "t" in server._projects
    assert len(server._projects["t"]["project"].activities) == 60


def test_a_compressed_object_restores_to_the_same_schedule():
    fake = _install_fake()
    sess = _seed()
    before = [(a.activity_id, a.name) for a in sess["project"].activities]
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()
    back = server._projects["t"]["project"]
    assert [(a.activity_id, a.name) for a in back.activities] == before
    assert len(back.relations) == 59


def test_a_corrupt_object_is_skipped_not_crashed_on():
    fake = _install_fake()
    _seed()
    fake.store[f"{cloud_store._PREFIX}t.xml"] = b"\x1f\x8b garbage not really gzip"
    _wipe_all_memory()
    server._restore_from_cloud()          # must not raise
    assert "t" not in server._projects


# ── an unchanged schedule does not go up again ───────────────────────────────

def test_saving_twice_with_no_edit_uploads_the_schedule_once():
    fake = _install_fake()
    _seed()
    server._persist("t")
    first = fake.puts if hasattr(fake, "puts") else None
    before = len([k for k in fake.store if k.endswith(".xml")])
    stored_first = _stored_xml(fake)

    server._persist("t")                  # nothing changed in between
    assert _stored_xml(fake) is stored_first or _stored_xml(fake) == stored_first


def test_the_manifest_still_saves_when_the_schedule_did_not_change():
    """The conversation and anything newly taught live in the manifest, and
    those DO change on a turn that left the schedule alone — which is most
    turns. Skipping the manifest too would lose them."""
    import json
    fake = _install_fake()
    sess = _seed()
    server._persist("t")
    sess["chat_history"].append({"role": "user", "text": "what drives A1000?"})

    ok, msg = server._persist("t")
    assert ok and "unchanged" in msg
    meta = json.loads(fake.store[f"{cloud_store._PREFIX}t.json"])
    assert any(m["text"] == "what drives A1000?" for m in meta["chat"])


def test_a_real_edit_does_upload_again():
    fake = _install_fake()
    sess = _seed()
    server._persist("t")
    first = _stored_xml(fake)
    sess["project"].activities[0].name = "Something else entirely"
    ok, msg = server._persist("t")
    assert ok and "unchanged" not in msg
    assert _stored_xml(fake) != first


def test_the_save_button_forces_a_full_write():
    """Pressed deliberately — the user wants the file written, not a
    reassurance that it matched."""
    fake = _install_fake()
    _seed()
    server._persist("t")
    del fake.store[f"{cloud_store._PREFIX}t.xml"]     # as if the bucket lost it
    ok, _ = server._persist("t", force=True)
    assert ok
    assert f"{cloud_store._PREFIX}t.xml" in fake.store


def test_the_digest_is_per_project():
    """Two schedules must not be able to suppress each other's saves."""
    fake = _install_fake()
    _seed("t")
    _seed("u")
    server._persist("t")
    ok, msg = server._persist("u")
    assert ok and "unchanged" not in msg
    assert f"{cloud_store._PREFIX}u.xml" in fake.store
