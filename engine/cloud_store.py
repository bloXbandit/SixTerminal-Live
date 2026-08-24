# -*- coding: utf-8 -*-
"""
cloud_store.py — Optional Cloudflare R2 persistence for loaded schedules.

R2 is S3-compatible, so we talk to it with boto3. Each schedule is stored as its
P6 XML (the app's existing round-trip format) plus a small JSON manifest, under a
key prefix. On startup the server restores everything; on edit it saves back.

FAIL-SOFT BY DESIGN
  • If boto3 isn't installed, or the R2_* env vars aren't set, the whole module
    reports "not configured" and the app runs exactly as before (in memory only).
  • Every network call is wrapped: a cloud hiccup never takes down an edit — it
    returns (False, message) and the in-memory project is still correct.

CONFIGURATION (set these as environment variables — e.g. Render → Environment)
  R2_ACCOUNT_ID          your Cloudflare account id  (used to build the endpoint)
  R2_ENDPOINT            optional — full endpoint URL, overrides R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID       R2 API token access key id
  R2_SECRET_ACCESS_KEY   R2 API token secret
  R2_BUCKET              the bucket name to store schedules in
  R2_PREFIX              optional key prefix (default "schedules/")

The secret values live only in the environment — they are never logged, never
sent to the browser, and never handled in application code beyond boto3.
"""

import os
import io
import gzip
import json
import datetime as _dt
from typing import Dict, Any, List, Optional, Tuple

_PREFIX = os.environ.get("R2_PREFIX", "schedules/")


def _endpoint() -> Optional[str]:
    ep = os.environ.get("R2_ENDPOINT")
    if ep:
        return ep.rstrip("/")
    acct = os.environ.get("R2_ACCOUNT_ID")
    return f"https://{acct}.r2.cloudflarestorage.com" if acct else None


def _env_ready() -> bool:
    return bool(_endpoint()
                and os.environ.get("R2_ACCESS_KEY_ID")
                and os.environ.get("R2_SECRET_ACCESS_KEY")
                and os.environ.get("R2_BUCKET"))


_client_cache: List[Any] = []   # memoized [client] or [None]


def _client():
    if _client_cache:
        return _client_cache[0]
    client = None
    if _env_ready():
        try:
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "s3",
                endpoint_url=_endpoint(),
                aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                region_name="auto",
                config=Config(signature_version="s3v4",
                              retries={"max_attempts": 3, "mode": "standard"}),
            )
        except Exception:
            client = None
    _client_cache.append(client)
    return client


def reset_client():
    """Drop the memoized client (e.g. after env vars change in a test)."""
    _client_cache.clear()


def status() -> Dict[str, Any]:
    """A safe, secret-free snapshot for the UI."""
    if not _env_ready():
        missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
                   if not os.environ.get(k)]
        if not _endpoint():
            missing.append("R2_ACCOUNT_ID or R2_ENDPOINT")
        return {"configured": False, "missing": missing}
    if _client() is None:
        return {"configured": False,
                "error": "boto3 not installed (add boto3 to requirements) "
                         "or the client failed to initialize"}
    return {"configured": True,
            "bucket": os.environ.get("R2_BUCKET"),
            "prefix": _PREFIX}


def is_configured() -> bool:
    return _client() is not None


# ──────────────────────────────────────────────────────────────────────────────
# Object I/O — one schedule = one .xml (data) + one .json (manifest)
# ──────────────────────────────────────────────────────────────────────────────
def _xml_key(pid: str) -> str:
    return f"{_PREFIX}{pid}.xml"


def _meta_key(pid: str) -> str:
    return f"{_PREFIX}{pid}.json"


# P6 XML is enormously repetitive, so it compresses about 50 to 1: the
# reference schedule goes from 10.7 MB to 0.23 MB for 0.03 seconds of CPU.
# That is the difference between an autosave that saturates a small instance's
# uplink and one nobody notices. Level 1 on purpose — levels above it buy a
# further 10% for twice the CPU, and CPU is the scarcer resource here.
_GZIP_LEVEL = 1
_GZIP_MAGIC = b"\x1f\x8b"


def save(pid: str, xml_bytes: bytes, meta: Dict[str, Any]) -> Tuple[bool, str]:
    """Persist one schedule. Returns (ok, message). Never raises."""
    client = _client()
    if client is None:
        return False, "cloud storage not configured"
    bucket = os.environ["R2_BUCKET"]
    try:
        meta = dict(meta or {})
        meta["saved_at"] = _dt.datetime.utcnow().isoformat() + "Z"
        meta["project_id"] = pid
        # Same key as before, gzipped contents. Keeping the key means an
        # existing uncompressed object is simply overwritten in place — no
        # migration, no second copy, and nothing orphaned in the bucket.
        body = gzip.compress(xml_bytes, _GZIP_LEVEL)
        meta["bytes_raw"] = len(xml_bytes)
        meta["bytes_stored"] = len(body)
        client.put_object(Bucket=bucket, Key=_xml_key(pid), Body=body,
                          ContentType="application/gzip")
        client.put_object(Bucket=bucket, Key=_meta_key(pid),
                          Body=json.dumps(meta).encode("utf-8"),
                          ContentType="application/json")
        return True, f"saved {pid}"
    except Exception as e:
        return False, f"cloud save failed: {e}"


def save_meta(pid: str, meta: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Write just the manifest.

    For the common turn that changed the conversation or what was taught but
    left the schedule byte-identical: the 10MB half of the save is pointless
    there, and the few kilobytes of manifest are the part that actually moved.
    """
    client = _client()
    if client is None:
        return False, "cloud storage not configured"
    try:
        meta = dict(meta or {})
        meta["saved_at"] = _dt.datetime.utcnow().isoformat() + "Z"
        meta["project_id"] = pid
        client.put_object(Bucket=os.environ["R2_BUCKET"], Key=_meta_key(pid),
                          Body=json.dumps(meta).encode("utf-8"),
                          ContentType="application/json")
        return True, f"manifest saved {pid}"
    except Exception as e:
        return False, f"cloud save failed: {e}"


def delete(pid: str) -> Tuple[bool, str]:
    client = _client()
    if client is None:
        return False, "cloud storage not configured"
    bucket = os.environ["R2_BUCKET"]
    try:
        client.delete_object(Bucket=bucket, Key=_xml_key(pid))
        client.delete_object(Bucket=bucket, Key=_meta_key(pid))
        return True, f"deleted {pid}"
    except Exception as e:
        return False, f"cloud delete failed: {e}"


def load_all() -> List[Dict[str, Any]]:
    """
    Return [{pid, xml_bytes, meta}] for every stored schedule.
    On any failure returns [] — the app then simply starts empty.
    """
    client = _client()
    if client is None:
        return []
    bucket = os.environ["R2_BUCKET"]
    out: List[Dict[str, Any]] = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        xml_keys: List[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=_PREFIX):
            for obj in page.get("Contents", []) or []:
                if obj["Key"].endswith(".xml"):
                    xml_keys.append(obj["Key"])
        for key in xml_keys:
            pid = key[len(_PREFIX):-4]  # strip prefix + ".xml"
            try:
                body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            except Exception:
                continue
            # Sniffed, not assumed: anything saved before compression existed
            # is still sitting in the bucket as plain XML and has to keep
            # loading. The magic bytes settle it either way.
            if body[:2] == _GZIP_MAGIC:
                try:
                    body = gzip.decompress(body)
                except Exception:
                    continue
            meta: Dict[str, Any] = {}
            try:
                mb = client.get_object(Bucket=bucket, Key=_meta_key(pid))["Body"].read()
                meta = json.loads(mb.decode("utf-8"))
            except Exception:
                meta = {}
            out.append({"pid": pid, "xml_bytes": body, "meta": meta})
    except Exception:
        return []
    return out
