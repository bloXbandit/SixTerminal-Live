"""
keys.py — find an API key without it ever reaching the repository.

The eval suite is the one thing here that calls a real model, so it is the one
thing that needs a key. Three places are checked, in this order:

  1. --api-key on the command line
  2. the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY)
  3. a .env.local file beside the repo

.env.local is gitignored and is the easy one: write the key there once and
every eval run picks it up. It is deliberately a DIFFERENT filename from the
.env that deployment tooling tends to read, so a local convenience cannot
become a deployed secret by accident.
"""

import os
from typing import Dict, Optional

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env.local")

_PROVIDER_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def read_env_file(path: Optional[str] = None) -> Dict[str, str]:
    """
    KEY=value lines, quotes optional, # comments ignored.

    Deliberately not a dotenv library: this reads one small file at the start
    of a run, and a dependency for that is not worth it.

    The path is resolved at CALL time rather than defaulting to ENV_FILE in
    the signature — a default argument is bound once at import, so the module
    constant could never be pointed anywhere else afterwards.
    """
    path = path or ENV_FILE
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                if val:
                    out[key.strip()] = val
    except OSError:
        pass
    return out


def load_into_env(path: Optional[str] = None) -> int:
    """
    Put anything in the file into os.environ, WITHOUT overwriting a variable
    that is already set — an explicitly exported key should always win over a
    file somebody forgot about.
    """
    n = 0
    for k, v in read_env_file(path).items():
        if not os.environ.get(k):
            os.environ[k] = v
            n += 1
    return n


def for_provider(provider: str, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    var = _PROVIDER_VARS.get(provider)
    if not var:
        return None
    return os.environ.get(var) or read_env_file().get(var)


def resolve(model_key: str, explicit: Optional[str] = None) -> Optional[str]:
    """The key for whichever provider this model belongs to."""
    from interpreter.llm_interpreter import resolve_model
    return for_provider(resolve_model(model_key)["provider"], explicit)


def status() -> Dict[str, object]:
    """
    What is available, WITHOUT ever returning a key.

    Where it came from is read BEFORE anything is loaded into the environment
    — otherwise a key out of .env.local has already been put in os.environ by
    the time it is asked about, and every key reports as "environment", which
    is the one thing this is for telling apart.
    """
    from_file = read_env_file()
    found = {}
    for provider, var in _PROVIDER_VARS.items():
        in_env = os.environ.get(var)
        val = in_env or from_file.get(var)
        found[provider] = {
            "set": bool(val),
            "source": ("environment" if in_env
                       else ".env.local" if val else None),
            # Enough to tell two keys apart when one is wrong; never enough to use.
            "ends_with": val[-4:] if val else None,
        }
    return {"providers": found, "env_file": ENV_FILE,
            "env_file_exists": os.path.exists(ENV_FILE)}
