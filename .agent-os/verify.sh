#!/bin/bash
# Deterministic gate for SixTerminal-Live.
# Exit 0 = PASS, 2 = unconfigured (never auto-ship), else FAIL.
set -uo pipefail

# Run from the project root regardless of caller's cwd
cd "$(dirname "$0")/.." || exit 1

# Prefer the project virtualenv so pytest + deps resolve on scheduled ticks
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

# 1) Engine / unit tests
"$PY" -m pytest -q tests/ || exit 1

# 2) Import smoke test — server + engine + interpreter must import cleanly
"$PY" -c "import server, engine.edit_engine, engine.schedule_model, interpreter.llm_interpreter" || exit 1

echo "PASS"
exit 0
