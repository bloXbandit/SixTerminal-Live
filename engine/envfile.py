# -*- coding: utf-8 -*-
"""
envfile.py — read KEY=value files into the environment, at startup.

WHY THIS EXISTS
  Nothing in the app read a .env file. Writing one and expecting it to work is
  the obvious assumption — every other Python web project behaves that way —
  so the failure was silent and total: os.environ.get("R2_BUCKET") returned
  None, cloud_store reported "not configured", and the restore at import time
  returned immediately. An empty app, no error, and a .env sitting right there
  looking correct.

  This is deliberately not a dotenv dependency. It reads two small files once
  at startup; the parser is thirty lines and already existed in evals/keys.py,
  which now shares this one rather than keeping its own.

PRECEDENCE — most explicit wins
      1. a variable already exported in the real environment
      2. .env.local
      3. .env

  Nothing here ever overwrites a value that is already set, so an exported
  variable always beats a file somebody forgot about, and .env.local beats
  .env because that is what a ".local" file means everywhere else.

  Both filenames are gitignored. .env.local remains the one the eval suite
  documents, on the reasoning that a name deployment tooling does not read by
  convention cannot turn a local convenience into a deployed secret.

ORDER MATTERS AT THE CALL SITE
  cloud_store reads R2_PREFIX at module import, and server.py restores from
  the cloud at import too. So load() has to run BEFORE those imports, not at
  the top of a request. server.py calls it immediately after sys.path is set
  and before the first `from engine import ...`.
"""

import os
from typing import Dict, List, Optional

# Searched in this order; the first file to define a variable wins, and any
# real environment variable beats both.
FILENAMES = (".env.local", ".env")


def read_env_file(path: str) -> Dict[str, str]:
    """
    KEY=value lines. Quotes optional, blank lines and # comments ignored, and
    an unreadable or missing file is simply empty rather than an error — this
    runs at startup and must never be the reason the app will not boot.

    `export KEY=value` is accepted because a file that was being sourced by a
    shell is exactly what somebody pastes in here first.
    """
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                if val:
                    out[key.strip()] = val
    except OSError:
        pass
    return out


def load_into_env(path: str) -> int:
    """
    Put a file's values into os.environ WITHOUT overwriting anything already
    set. Returns how many it added.
    """
    n = 0
    for k, v in read_env_file(path).items():
        if not os.environ.get(k):
            os.environ[k] = v
            n += 1
    return n


def load(root: Optional[str] = None) -> List[str]:
    """
    Load .env.local then .env from the project root. Returns the files that
    actually contributed something, for the startup line — "it found the file"
    and "the file had nothing new in it" are different problems and the caller
    should be able to tell them apart.
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    used: List[str] = []
    for name in FILENAMES:
        path = os.path.join(root, name)
        if os.path.exists(path) and load_into_env(path):
            used.append(name)
    return used


def describe(root: Optional[str] = None) -> Dict[str, object]:
    """
    Which files exist and which variables they define — NAMES ONLY, never
    values. A wrong key and a missing key look identical from the outside, and
    this is how you tell them apart without printing a secret.
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []
    for name in FILENAMES:
        path = os.path.join(root, name)
        exists = os.path.exists(path)
        files.append({"name": name, "exists": exists,
                      "defines": sorted(read_env_file(path)) if exists else []})
    return {"root": root, "files": files}
