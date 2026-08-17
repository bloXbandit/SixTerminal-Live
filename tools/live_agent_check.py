#!/usr/bin/env python3
"""
live_agent_check.py — does the agent actually behave, against a real model?

Everything in tests/ runs with the model mocked, which proves the plumbing and
proves nothing about what the model does with it. These are the four behaviours
that went wrong in a real session, checked against a real API key on a real
schedule. Run it after any prompt change.

    export OPENAI_API_KEY=sk-...            # or ANTHROPIC_API_KEY
    python tools/live_agent_check.py path/to/schedule.xml --model gpt-4.1-mini

    # include the image checks by pointing at your own screenshots
    python tools/live_agent_check.py sched.xml \
        --drawing snips/E03-021AB.png --status-shot snips/lookahead.png

Each check prints PASS / FAIL and the model's own words, so a failure tells you
what it said, not just that it said something wrong. Nothing is written to the
schedule file — edits are applied to an in-memory copy and thrown away.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.edit_engine import apply_commands, is_advisory
from engine.xer_reader import load_xer
from engine.xml_reader import load_xml
from interpreter.llm_interpreter import interpret

_PASS, _FAIL = "PASS", "FAIL"
_results = []


def report(name, ok, detail=""):
    _results.append((name, ok))
    print(f"\n[{_PASS if ok else _FAIL}] {name}")
    for line in str(detail).strip().splitlines():
        print(f"        {line}")


def ask(project, text, model, key, history=None, chat=None):
    """One turn, exactly as the server would send it."""
    from engine import project_brain
    ctx = project.llm_context() + project_brain.Brain(
        project_brain.project_key(project)).context_block()
    cmds, raw = interpret(text, project_summary=ctx, edit_history=history or [],
                          model_key=model, api_key=key, chat_history=chat or [])
    return cmds, raw


def say(cmds):
    """What the user would read."""
    return " ".join(c.get("message", "") or c.get("question", "")
                    for c in cmds if c.get("action") in ("chat", "clarify")).strip()


def edits(cmds):
    return [c for c in cmds if c.get("action") not in ("chat", "clarify")]


# ── 1. does "wire it" wire it, or just talk about it? ────────────────────────

def check_wiring(project, model, key, folder):
    cmds, _ = ask(project, f"Wire the activities in {folder}. Add the actual "
                           f"relationships — do not just analyse.", model, key)
    e = edits(cmds)
    ties = [c for c in e if c.get("action") == "add_relation"]
    reports = [c for c in e if is_advisory(c.get("action"))]
    words = say(cmds)

    if ties:
        report("A request to wire produces real ties", True,
               f"{len(ties)} add_relation command(s); "
               f"e.g. {ties[0].get('predecessor_id')} -> {ties[0].get('successor_id')}")
    elif reports and not ties:
        # The failure mode from the live session: says "I'll begin wiring",
        # emits only reports, and the schedule is untouched.
        claimed = any(w in words.lower() for w in
                      ("wired", "connected", "tied", "linked", "i'll begin wiring",
                       "i have connected", "i've connected"))
        report("A request to wire produces real ties", False,
               f"Only {len(reports)} report command(s), no ties.\n"
               f"Claimed to have wired anyway: {claimed}\nSaid: {words[:400]}")
    else:
        report("A request to wire produces real ties", False,
               f"No ties and no reports. Said: {words[:400]}")
    return cmds


# ── 2. does it invent ids? ───────────────────────────────────────────────────

def check_no_invented_ids(project, model, key):
    real = {a.activity_id for a in project.activities}
    cmds, _ = ask(project, "Give me three activities that should have a "
                           "predecessor and what you would tie each to.", model, key)
    used = set()
    for c in cmds:
        for k in ("activity_id", "predecessor_id", "successor_id"):
            if c.get(k):
                used.add(c[k])
    words = say(cmds)
    import re
    for tok in re.findall(r"\b[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+){1,5}\b", words):
        used.add(tok)
    bogus = sorted(u for u in used if u not in real)
    report("Every id it names is real", not bogus,
           "all ids exist in the schedule" if not bogus
           else f"invented: {', '.join(bogus[:8])}")


# ── 3. does it own up when a command fails? ──────────────────────────────────

def check_owns_failures(project, model, key):
    history = [{
        "instruction": "tie the precast milestone to its predecessor",
        "commands": [{"action": "add_relation", "predecessor_id": "NOPE.123",
                      "successor_id": "ALSO.NOPE"}],
        "results": [{"action": "add_relation", "success": False,
                     "message": "Predecessor — no activity 'NOPE.123' in this schedule"}],
    }]
    chat = [{"role": "user", "text": "tie the precast milestone to its predecessor"},
            {"role": "system_result", "text": "0 edits applied, 1 failed",
             "context": "Results:\n  FAILED add_relation: Predecessor — no "
                        "activity 'NOPE.123' in this schedule"}]
    cmds, _ = ask(project, "did that go through?", model, key, history, chat)
    words = say(cmds).lower()
    admits = any(w in words for w in ("fail", "did not", "didn't", "no ", "not applied",
                                      "unsuccessful", "error", "could not"))
    claims = any(w in words for w in ("yes, it went through", "successfully applied",
                                      "the tie is in place", "yes — applied"))
    report("It admits a failed command", admits and not claims, say(cmds)[:400])


# ── 4. can it use the conversation? ──────────────────────────────────────────

def check_back_reference(project, model, key):
    acts = [a for a in project.activities][:3]
    if len(acts) < 3:
        return
    chat = [
        {"role": "user", "text": "what should X connect to?"},
        {"role": "assistant", "text": "Options",
         "context": ("Tie options offered:\n"
                     f"  Option 1 (predecessor): add_relation {acts[0].activity_id} "
                     f"'{acts[0].name}' -> {acts[2].activity_id} '{acts[2].name}'\n"
                     f"  Option 2 (predecessor): add_relation {acts[1].activity_id} "
                     f"'{acts[1].name}' -> {acts[2].activity_id} '{acts[2].name}'\n"
                     "If the user picks one by number, issue that add_relation "
                     "command directly.")},
    ]
    cmds, _ = ask(project, "apply the second one", model, key, chat=chat)
    ties = [c for c in edits(cmds) if c.get("action") == "add_relation"]
    right = any(t.get("predecessor_id") == acts[1].activity_id for t in ties)
    report('"Apply the second one" resolves to option 2', right,
           f"emitted: {ties or say(cmds)[:300]}\n"
           f"expected predecessor {acts[1].activity_id}")


# ── 5. does it claim to have seen documents it has not? ──────────────────────

def check_no_phantom_documents(project, model, key):
    cmds, _ = ask(project, "Based on the electrical spec section I sent you "
                           "earlier, what does it require for cable pulling?",
                  model, key)
    words = say(cmds).lower()
    honest = any(w in words for w in
                 ("don't have", "do not have", "didn't receive", "did not receive",
                  "not been shared", "no record", "haven't received", "attach",
                  "upload", "wasn't provided", "was not provided", "i don't see"))
    report("It does not describe a document it never got", honest,
           say(cmds)[:400])


# ── image checks (only when you point at your own files) ─────────────────────

def check_drawing(project, path, model, key):
    from interpreter.vision import read_drawing
    from engine import project_brain as pb
    rd = read_drawing(Path(path).read_bytes(), Path(path).name, project,
                      model_key=model, api_key=key)
    graded = [(t, pb.ground(project, pb.parse_directive(t))) for t in rd["directives"]]
    binds = [t for t, d in graded if d.kind != pb.NOTE]
    detail = [f"sheet {rd.get('sheet_number')}: {rd.get('summary', '')[:160]}",
              f"rooms: {', '.join(rd.get('rooms') or []) or '—'}"]
    for t, d in graded:
        detail.append(f"  [{'RULE' if d.kind != pb.NOTE else 'note'}] {t}")
    report("A drawing yields at least one rule that binds", bool(binds),
           "\n".join(detail))


def check_status_shot(project, path, model, key):
    from interpreter.vision import read_schedule
    from engine import sheet_sync
    read = read_schedule(Path(path).read_bytes(), Path(path).name, project,
                         question="match the dates and actualization status",
                         model_key=model, api_key=key)
    res = sheet_sync.match_rows(project, read["rows"])
    detail = [sheet_sync.summarize(res)]
    for m in res["matched"][:10]:
        for c in m["changes"]:
            detail.append(f"  {m['activity_id']}: {c['label']} "
                          f"{c['from'] or '—'} -> {c['to']}  [{c['severity']}]")
    report("A schedule screenshot matches rows to real activities",
           res["rows_matched"] > 0, "\n".join(detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("schedule")
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--folder", default="Phase 1 (Build-Out)")
    ap.add_argument("--drawing")
    ap.add_argument("--status-shot")
    args = ap.parse_args()

    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set OPENAI_API_KEY or ANTHROPIC_API_KEY first.")

    path = Path(args.schedule)
    project = load_xer(str(path)) if path.suffix.lower() == ".xer" else load_xml(str(path))
    print(f"{project.name}: {len(project.activities)} activities, "
          f"{len(project.relations)} relations, model={args.model}")

    checks = [
        lambda: check_wiring(project, args.model, key, args.folder),
        lambda: check_no_invented_ids(project, args.model, key),
        lambda: check_owns_failures(project, args.model, key),
        lambda: check_back_reference(project, args.model, key),
        lambda: check_no_phantom_documents(project, args.model, key),
    ]
    if args.drawing:
        checks.append(lambda: check_drawing(project, args.drawing, args.model, key))
    if args.status_shot:
        checks.append(lambda: check_status_shot(project, args.status_shot,
                                                args.model, key))
    for fn in checks:
        try:
            fn()
        except Exception as e:
            report(getattr(fn, "__name__", "check"), False, f"crashed: {e}")

    passed = sum(1 for _, ok in _results if ok)
    print(f"\n{'=' * 60}\n{passed}/{len(_results)} passed")
    sys.exit(0 if passed == len(_results) else 1)


if __name__ == "__main__":
    main()
