"""
test_envfile.py — a .env that is read, and read early enough to matter.

The bug this covers was total and silent. Nothing in the app loaded a .env
file, so writing one — the obvious thing to do, and what every other Python
web project does — changed nothing at all: R2_BUCKET stayed unset,
cloud_store reported "not configured", and the restore at import time returned
immediately. An empty app, no error message, and a correct-looking .env
sitting right beside it.

The ordering test is the one that matters most. cloud_store reads R2_PREFIX at
module import and server.py restores from the cloud at import, so loading the
file even slightly late is indistinguishable from not loading it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import envfile


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("R2_BUCKET", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
              "R2_SECRET_ACCESS_KEY", "R2_PREFIX", "ZZ_TEST"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ── parsing ──────────────────────────────────────────────────────────────────

def test_plain_key_value(tmp_path):
    f = tmp_path / ".env"
    f.write_text("R2_BUCKET=six-terminal\nR2_PREFIX=schedules/\n")
    assert envfile.read_env_file(str(f)) == {
        "R2_BUCKET": "six-terminal", "R2_PREFIX": "schedules/"}


def test_quotes_are_stripped(tmp_path):
    f = tmp_path / ".env"
    f.write_text('R2_BUCKET="six-terminal"\nR2_ACCOUNT_ID=\'abc123\'\n')
    got = envfile.read_env_file(str(f))
    assert got["R2_BUCKET"] == "six-terminal"
    assert got["R2_ACCOUNT_ID"] == "abc123"


def test_comments_and_blank_lines_are_ignored(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# a comment\n\nR2_BUCKET=b\n   \n# another\n")
    assert envfile.read_env_file(str(f)) == {"R2_BUCKET": "b"}


def test_an_export_prefix_is_accepted():
    """A file that was being sourced by a shell is exactly what someone
    pastes in first."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write("export R2_BUCKET=b\nexport   OPENAI_API_KEY=sk-x\n")
        path = f.name
    assert envfile.read_env_file(path) == {"R2_BUCKET": "b",
                                           "OPENAI_API_KEY": "sk-x"}
    os.unlink(path)


def test_a_value_containing_equals_survives(tmp_path):
    """Secrets routinely end in '=' padding — splitting on every '=' would
    truncate the key and the failure would look like a wrong credential."""
    f = tmp_path / ".env"
    f.write_text("R2_SECRET_ACCESS_KEY=abc/def+ghi=\n")
    assert envfile.read_env_file(str(f))["R2_SECRET_ACCESS_KEY"] == "abc/def+ghi="


def test_a_missing_file_is_empty_rather_than_an_error():
    """This runs at startup and must never be why the app will not boot."""
    assert envfile.read_env_file("/nope/nothing/.env") == {}


def test_an_empty_value_is_skipped(tmp_path):
    """.env.example ships with bare 'OPENAI_API_KEY=' lines; copying it must
    not set the variable to empty string, which reads as 'set' everywhere."""
    f = tmp_path / ".env"
    f.write_text("OPENAI_API_KEY=\nR2_BUCKET=b\n")
    assert envfile.read_env_file(str(f)) == {"R2_BUCKET": "b"}


# ── precedence ───────────────────────────────────────────────────────────────

def test_a_real_environment_variable_always_wins(tmp_path, clean_env):
    """An explicitly exported value beats a file somebody forgot about."""
    clean_env.setenv("R2_BUCKET", "from-shell")
    f = tmp_path / ".env"
    f.write_text("R2_BUCKET=from-file\n")
    envfile.load_into_env(str(f))
    assert os.environ["R2_BUCKET"] == "from-shell"


def test_env_local_beats_env(tmp_path, clean_env):
    """What a '.local' file means everywhere else."""
    (tmp_path / ".env").write_text("R2_BUCKET=plain\n")
    (tmp_path / ".env.local").write_text("R2_BUCKET=local\n")
    envfile.load(str(tmp_path))
    assert os.environ["R2_BUCKET"] == "local"


def test_the_two_files_combine_rather_than_one_replacing_the_other(tmp_path, clean_env):
    (tmp_path / ".env").write_text("R2_BUCKET=b\nR2_ACCOUNT_ID=acct\n")
    (tmp_path / ".env.local").write_text("R2_ACCESS_KEY_ID=key\n")
    envfile.load(str(tmp_path))
    assert os.environ["R2_BUCKET"] == "b"
    assert os.environ["R2_ACCESS_KEY_ID"] == "key"


def test_load_reports_which_files_contributed(tmp_path, clean_env):
    """'found the file' and 'the file added nothing' are different problems."""
    (tmp_path / ".env").write_text("ZZ_TEST=1\n")
    assert envfile.load(str(tmp_path)) == [".env"]
    # second call adds nothing, because the value is now already in the env
    assert envfile.load(str(tmp_path)) == []


def test_loading_nothing_is_not_an_error(tmp_path):
    assert envfile.load(str(tmp_path)) == []


# ── the diagnostic never leaks a value ───────────────────────────────────────

def test_describe_reports_names_but_never_values(tmp_path):
    (tmp_path / ".env").write_text("R2_SECRET_ACCESS_KEY=super-secret\n")
    d = envfile.describe(str(tmp_path))
    blob = repr(d)
    assert "R2_SECRET_ACCESS_KEY" in blob
    assert "super-secret" not in blob, "describe() leaked a secret"


# ── it is loaded early enough to matter ──────────────────────────────────────

def test_the_server_loads_env_before_importing_cloud_store():
    """
    THE ORDERING TEST. cloud_store reads R2_PREFIX at import and server.py
    restores from the cloud at import, so a .env loaded even slightly late is
    indistinguishable from one never loaded at all.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "server.py"),
               encoding="utf-8").read()
    load_at = src.index("_envfile.load(")
    for line in ("from engine import cloud_store",
                 "from engine.xml_reader import load_xml"):
        assert load_at < src.index(line), (
            f"server.py imports '{line}' before loading .env — the variables "
            f"would arrive too late to configure it")


def test_main_loads_env_before_it_warns_about_a_missing_key():
    """Otherwise a key sitting in .env is reported as missing on every start."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "main.py"),
               encoding="utf-8").read()
    assert src.index("_load_env(") < src.index("No LLM API key found")


def test_the_eval_helper_shares_this_parser():
    """One parser, so a file the evals accept cannot be one the server
    silently ignores."""
    import tempfile
    from evals import keys
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write("export ANTHROPIC_API_KEY=ant-1\n")
        path = f.name
    assert keys.read_env_file(path) == {"ANTHROPIC_API_KEY": "ant-1"}
    os.unlink(path)


def test_cloud_store_sees_credentials_that_came_from_a_file(tmp_path, clean_env):
    """End to end: the thing the whole change is for."""
    from engine import cloud_store
    (tmp_path / ".env").write_text(
        "R2_ACCOUNT_ID=acct\nR2_ACCESS_KEY_ID=k\n"
        "R2_SECRET_ACCESS_KEY=s\nR2_BUCKET=six-terminal\n")
    cloud_store.reset_client()
    assert cloud_store.status()["configured"] is False, "should start unset"
    envfile.load(str(tmp_path))
    cloud_store.reset_client()
    st = cloud_store.status()
    assert st.get("configured") is True or "boto3" in str(st.get("error", ""))
    cloud_store.reset_client()
