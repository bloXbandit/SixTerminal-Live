"""
test_cloud_store.py — R2 persistence round-trip, with an in-memory fake client.

No network, no boto3: we inject a stand-in S3 client that stores objects in a
dict, so the save → list → load path is exercised deterministically.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import cloud_store


class _Body:
    def __init__(self, data): self._d = data
    def read(self): return self._d


class _Paginator:
    def __init__(self, store): self._store = store
    def paginate(self, Bucket, Prefix=""):
        contents = [{"Key": k} for k in self._store if k.startswith(Prefix)]
        yield {"Contents": contents}


class FakeS3:
    """Minimal in-memory S3 surface: the four calls cloud_store uses."""
    def __init__(self): self.store = {}
    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.store[Key] = Body
    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise KeyError(Key)
        return {"Body": _Body(self.store[Key])}
    def delete_object(self, Bucket, Key):
        self.store.pop(Key, None)
    def get_paginator(self, _name):
        return _Paginator(self.store)


def _install_fake():
    os.environ["R2_BUCKET"] = "test-bucket"
    cloud_store.reset_client()
    fake = FakeS3()
    cloud_store._client_cache.clear()
    cloud_store._client_cache.append(fake)   # memoize the fake
    return fake


def test_save_and_load_all_round_trip():
    _install_fake()
    ok, _ = cloud_store.save("PROJ1", b"<xml>one</xml>", {"project_name": "One"})
    assert ok
    ok, _ = cloud_store.save("PROJ2", b"<xml>two</xml>", {"project_name": "Two"})
    assert ok
    items = cloud_store.load_all()
    got = {i["pid"]: i for i in items}
    assert set(got) == {"PROJ1", "PROJ2"}
    assert got["PROJ1"]["xml_bytes"] == b"<xml>one</xml>"
    assert got["PROJ1"]["meta"]["project_name"] == "One"
    assert "saved_at" in got["PROJ1"]["meta"]


def test_delete_removes_both_objects():
    fake = _install_fake()
    cloud_store.save("GONE", b"<xml/>", {})
    assert any(k.endswith("GONE.xml") for k in fake.store)
    cloud_store.delete("GONE")
    assert not any("GONE" in k for k in fake.store)


def test_not_configured_is_safe():
    # No fake, no env → everything reports not configured and never raises
    for k in ("R2_BUCKET", "R2_ACCOUNT_ID", "R2_ENDPOINT",
              "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        os.environ.pop(k, None)
    cloud_store.reset_client()
    assert cloud_store.is_configured() is False
    assert cloud_store.status()["configured"] is False
    ok, msg = cloud_store.save("X", b"x", {})
    assert ok is False
    assert cloud_store.load_all() == []
