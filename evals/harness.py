"""
harness.py — run the cases, score them, and say whether anything got worse.

Two decisions shape this.

REPEATS, NOT A SINGLE SAMPLE. The model is stochastic, so one run of a case
tells you almost nothing: a pass can be luck and a fail can be noise. Every
case is run several times and scored as a RATE, which is the only way a
"regression" means something other than the dice landing differently. The
comparison against a baseline then has a threshold, so normal variance does
not read as damage.

NOTHING IS MOCKED. That is the entire point — the existing test suite already
covers every path with a fake client, and it is exactly why a prompt change
could not be evaluated. These calls go to a real model and cost real money,
which is why they never run under pytest.
"""

import datetime as _dt
import json
import os
import statistics
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from evals.cases import Case
from interpreter.llm_interpreter import interpret

BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baselines")

# Below this pass rate a case is failing, not merely flaky. A prompt
# instruction that only lands two times in three is not being followed.
PASS_BAR = 0.8
# How far a case may drop against the baseline before it is called a
# regression rather than variance. With 5 repeats one flipped run is 0.2.
REGRESSION_MARGIN = 0.25


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class RunResult:
    case_id: str
    category: str
    claim: str
    passed: bool
    checks: List[CheckResult]
    chat: str = ""
    commands: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


@dataclass
class CaseScore:
    case_id: str
    category: str
    claim: str
    runs: int
    passes: int
    rate: float
    errors: int
    failing_checks: Dict[str, int]      # check name -> times it failed
    sample_failure: str = ""

    @property
    def ok(self) -> bool:
        return self.rate >= PASS_BAR


def _chat_message(commands: List[Dict[str, Any]]) -> str:
    for c in commands:
        if c.get("action") == "chat":
            return c.get("message") or ""
    for c in commands:
        if c.get("action") == "clarify":
            return c.get("question") or ""
    return ""


def run_once(case: Case, model_key: str, api_key: Optional[str],
             call=interpret) -> RunResult:
    """
    One real call, scored.

    `call` is injectable for the harness's OWN tests — the checks and the
    scoring have to be verifiable without spending anything, even though the
    cases themselves are meaningless against a fake.
    """
    project = case.project()
    from engine import project_brain
    context = project.llm_context() + project_brain.Brain("eval").context_block(project)
    started = time.time()
    try:
        commands, _raw = call(
            case.instruction,
            project_summary=context,
            chat_history=case.chat or None,
            edit_history=case.edits or None,
            model_key=model_key,
            api_key=api_key,
        )
    except Exception as e:
        return RunResult(case.id, case.category, case.claim, False, [],
                         error=f"{type(e).__name__}: {e}",
                         seconds=time.time() - started)

    chat = _chat_message(commands)
    results = []
    for check in case.checks:
        try:
            ok, detail = check(project, commands, chat)
        except Exception as e:
            ok, detail = False, f"check raised {type(e).__name__}: {e}"
        results.append(CheckResult(getattr(check, "__name__", "check"), ok, detail))
    return RunResult(case.id, case.category, case.claim,
                     all(r.ok for r in results), results, chat, commands,
                     seconds=time.time() - started)


def score(case: Case, runs: List[RunResult]) -> CaseScore:
    failing: Dict[str, int] = {}
    sample = ""
    for r in runs:
        if r.error:
            failing["<error>"] = failing.get("<error>", 0) + 1
            sample = sample or r.error
            continue
        for c in r.checks:
            if not c.ok:
                failing[c.name] = failing.get(c.name, 0) + 1
                sample = sample or f"{c.name}: {c.detail}"
    passes = sum(1 for r in runs if r.passed)
    return CaseScore(
        case_id=case.id, category=case.category, claim=case.claim,
        runs=len(runs), passes=passes,
        rate=(passes / len(runs)) if runs else 0.0,
        errors=sum(1 for r in runs if r.error),
        failing_checks=failing, sample_failure=sample,
    )


def run_suite(cases: List[Case], model_key: str, api_key: Optional[str],
              repeats: int = 5, call=interpret,
              on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    scores, transcripts = [], []
    for case in cases:
        runs = []
        for _ in range(repeats):
            r = run_once(case, model_key, api_key, call=call)
            runs.append(r)
            transcripts.append(asdict(r))
        s = score(case, runs)
        scores.append(s)
        if on_progress:
            on_progress(s)
    return {
        "model": model_key,
        "repeats": repeats,
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "cases": [asdict(s) for s in scores],
        "overall": round(statistics.mean([s.rate for s in scores]), 4) if scores else 0.0,
        "passing": sum(1 for s in scores if s.ok),
        "total": len(scores),
        "transcripts": transcripts,
    }


# ── comparing against what it used to do ─────────────────────────────────────

def baseline_path(name: str) -> str:
    return os.path.join(BASELINE_DIR, f"{name}.json")


def save_baseline(report: Dict[str, Any], name: str) -> str:
    os.makedirs(BASELINE_DIR, exist_ok=True)
    path = baseline_path(name)
    # Transcripts are for reading a failure, not for diffing runs — they are
    # large and change every call, so they never go in a baseline.
    slim = {k: v for k, v in report.items() if k != "transcripts"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=2)
    return path


def load_baseline(name: str) -> Optional[Dict[str, Any]]:
    try:
        with open(baseline_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def compare(report: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """
    What moved, with a margin so noise is not reported as damage.

    A case appearing or disappearing is called out separately: a prompt change
    that quietly stops a case being run would otherwise look like an
    improvement in the average.
    """
    was = {c["case_id"]: c for c in baseline.get("cases", [])}
    now = {c["case_id"]: c for c in report.get("cases", [])}
    regressions, improvements = [], []
    for cid, cur in now.items():
        prev = was.get(cid)
        if prev is None:
            continue
        delta = round(cur["rate"] - prev["rate"], 4)
        row = {"case_id": cid, "claim": cur["claim"],
               "was": prev["rate"], "now": cur["rate"], "delta": delta,
               "failing": cur.get("failing_checks", {}),
               "sample": cur.get("sample_failure", "")}
        if delta <= -REGRESSION_MARGIN:
            regressions.append(row)
        elif delta >= REGRESSION_MARGIN:
            improvements.append(row)
    regressions.sort(key=lambda r: r["delta"])
    improvements.sort(key=lambda r: -r["delta"])
    return {
        "regressions": regressions,
        "improvements": improvements,
        "new_cases": sorted(set(now) - set(was)),
        "dropped_cases": sorted(set(was) - set(now)),
        "overall_was": baseline.get("overall"),
        "overall_now": report.get("overall"),
        "overall_delta": round((report.get("overall") or 0)
                               - (baseline.get("overall") or 0), 4),
    }
