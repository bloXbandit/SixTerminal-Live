# -*- coding: utf-8 -*-
"""
requirements.py — statements about the job that can be CHECKED, and fixed.

WHY THIS EXISTS
  A schedule is judged against things a project manager says in one line:

    "Phase 1 substantial completion is 15 March 27."
    "Every generator termination has to lead to commissioning for its phase."
    "The first activity in each MV room follows energization."

  Those are not opinions and they are not chat — each one is a testable
  property of the network. Until now they could only live in the brain as
  prose the agent might remember, so nothing ever verified them and nothing
  could say which activities broke them.

  Here they become checkable. A requirement reports exactly which activities
  violate it, and can propose the ties or constraints that would satisfy it.

THE DIVISION OF LABOUR
  Turning English into a requirement is the model's job — it is good at it,
  and a regex parser for "and/or engine burn in activities" would be worse
  than useless. Deciding whether the requirement HOLDS is this module's job:
  it walks the actual relationship graph, which the model cannot do reliably
  and should not be guessing at.

WHY REACHABILITY AND NOT A DIRECT LINK
  "Leads to commissioning" does not mean "is wired directly to it". Work
  reaches commissioning through a chain, and demanding a direct tie would
  both fail correct schedules and invite ties that skip the real sequence.
  So the test is: is there ANY forward path. That is the property that
  actually matters — if there is no path, a slip in that activity never
  reaches the milestone, which is the whole point of asking.
"""

from datetime import date as _date
from typing import Any, Dict, List, Optional

DEADLINE = "deadline"      # X must finish on or before DATE
REACHES = "reaches"        # every X must have a forward path to some Y
FOLLOWS = "follows"        # every X must be driven (directly or not) by some Y
NOT_AFTER = "not_after"    # the GATE flag: nothing in scope may finish after DATE


def _matches(act, pattern: str) -> bool:
    """Loose, case-insensitive containment on name or id — the way a person
    names work, not an exact string."""
    if not pattern:
        return False
    p = pattern.strip().lower()
    return p in (act.name or "").lower() or p in (act.activity_id or "").lower()


def _in_scope(project, act, scope: Optional[str]) -> bool:
    """Scope narrows by folder path or by id/name, so 'for each phase' works
    without naming every activity."""
    if not scope:
        return True
    s = scope.strip().lower()
    if s in (act.activity_id or "").lower() or s in (act.name or "").lower():
        return True
    by_uid = {w.uid: w for w in project.wbs_nodes}
    uid, guard = act.wbs_uid, 0
    while uid and guard < 100:
        guard += 1
        w = by_uid.get(uid)
        if not w:
            break
        if s in (w.name or "").lower() or s in (w.code or "").lower():
            return True
        uid = w.parent_uid
    return False


def _forward(project) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {}
    for r in project.relations:
        adj.setdefault(r.predecessor_uid, []).append(r.successor_uid)
    return adj


def _backward(project) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {}
    for r in project.relations:
        adj.setdefault(r.successor_uid, []).append(r.predecessor_uid)
    return adj


def _reaches_any(start: str, targets: set, adj: Dict[str, List[str]]) -> bool:
    """Any forward path from start into targets. Iterative, and guarded — a
    schedule with a cycle must not hang the check."""
    seen, stack, guard = {start}, [start], 0
    while stack and guard < 200000:
        guard += 1
        u = stack.pop()
        for v in adj.get(u, ()):
            if v in targets:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def check(project, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Does this requirement hold? Returns the violations by name, never a bare
    pass/fail — "12 activities break this, here they are" is actionable and
    "failed" is not.
    """
    kind = (spec.get("kind") or "").strip().lower()
    scope = spec.get("scope")
    folder_of = {w.uid: w.name for w in project.wbs_nodes}

    if kind == DEADLINE:
        date = str(spec.get("date") or "")[:10]
        if not date:
            return {"error": "deadline needs a date"}
        subject = [a for a in project.activities
                   if _matches(a, spec.get("what") or spec.get("from") or "")
                   and _in_scope(project, a, scope)]
        bad = [a for a in subject
               if str(a.planned_finish or a.early_finish or "")[:10] > date]
        return {
            "kind": kind, "passed": not bad and bool(subject),
            "matched": len(subject), "violations": len(bad),
            "detail": [{"activity_id": a.activity_id, "name": a.name,
                        "finish": str(a.planned_finish or a.early_finish or "")[:10],
                        "folder": folder_of.get(a.wbs_uid, "")}
                       for a in bad[:20]],
            "statement": f"{spec.get('what')} must finish on or before {date}",
        }

    if kind == NOT_AFTER:
        # The gate flag, and the question a deadline cannot answer. A deadline
        # on "Final Completion" only ever looks at that one row, so work that
        # drifted past a phase gate stays invisible while every named
        # milestone still reports green. This asks whether ANYTHING in scope
        # is scheduled past the date, and reports the worst overrun first,
        # measured in days — which is what finds the cause.
        date = str(spec.get("date") or "")[:10]
        if not date:
            return {"error": "not_after needs a date"}
        what = spec.get("what") or ""
        subject = [a for a in project.activities
                   if _in_scope(project, a, scope)
                   and (not what or _matches(a, what))]

        def _days_over(a) -> int:
            f = str(a.planned_finish or a.early_finish or "")[:10]
            if not f or f <= date:
                return 0
            try:
                return (_date.fromisoformat(f)
                        - _date.fromisoformat(date)).days
            except ValueError:
                return 0

        bad = sorted((a for a in subject if _days_over(a) > 0),
                     key=_days_over, reverse=True)
        return {
            "kind": kind, "passed": not bad,
            "matched": len(subject), "violations": len(bad),
            "worst_days_over": _days_over(bad[0]) if bad else 0,
            "detail": [{"activity_id": a.activity_id, "name": a.name,
                        "folder": folder_of.get(a.wbs_uid, ""),
                        "finish": str(a.planned_finish or a.early_finish or "")[:10],
                        "days_over": _days_over(a)}
                       for a in bad[:20]],
            "statement": ("nothing" + (f" in '{scope}'" if scope else "")
                          + (f" matching '{what}'" if what else "")
                          + f" may finish after {date}"),
        }

    if kind in (REACHES, FOLLOWS):
        a_pat = spec.get("from") or spec.get("what") or ""
        b_pat = spec.get("to") or spec.get("driver") or ""
        subject = [a for a in project.activities
                   if _matches(a, a_pat) and _in_scope(project, a, scope)]
        targets = {a.uid for a in project.activities
                   if _matches(a, b_pat) and _in_scope(project, a, scope)}
        if not subject:
            return {"kind": kind, "passed": False, "matched": 0,
                    "violations": 0, "detail": [],
                    "statement": f"nothing matches '{a_pat}'"
                                 + (f" within '{scope}'" if scope else ""),
                    "error": f"No activity matches '{a_pat}'"
                             + (f" in scope '{scope}'" if scope else "")}
        if not targets:
            return {"kind": kind, "passed": False, "matched": len(subject),
                    "violations": len(subject), "detail": [],
                    "statement": f"nothing matches '{b_pat}'",
                    "error": f"No activity matches '{b_pat}'"
                             + (f" in scope '{scope}'" if scope else "")}

        adj = _forward(project) if kind == REACHES else _backward(project)
        bad = [a for a in subject
               if a.uid not in targets and not _reaches_any(a.uid, targets, adj)]
        verb = "lead to" if kind == REACHES else "follow"
        return {
            "kind": kind, "passed": not bad,
            "matched": len(subject), "violations": len(bad),
            "detail": [{"activity_id": a.activity_id, "name": a.name,
                        "folder": folder_of.get(a.wbs_uid, ""),
                        "finish": str(a.planned_finish or "")[:10]}
                       for a in bad[:20]],
            "statement": (f"every '{a_pat}'"
                          + (f" in '{scope}'" if scope else "")
                          + f" must {verb} '{b_pat}'"),
        }

    return {"error": f"Unknown requirement kind '{kind}' — "
                     f"use deadline, not_after, reaches or follows"}


def enforce(project, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    The commands that would make this requirement hold.

    Never applied here — the caller decides. Nothing is deleted and no
    existing tie is repointed; a requirement is satisfied by ADDING the
    missing link or pin, never by removing what is already there.
    """
    res = check(project, spec)
    if res.get("error") or res.get("passed"):
        return {"check": res, "commands": []}

    kind = res["kind"]
    scope = spec.get("scope")
    cmds: List[Dict[str, Any]] = []

    if kind == NOT_AFTER:
        # Pinning every overrunning row would add hundreds of constraints and
        # bury the overrun under exactly the pins that hide whether the
        # network is right. The fix is shortening or re-sequencing the work.
        return {"check": res, "commands": [],
                "note": ("A gate overrun is not something to auto-fix — "
                         "pinning every late activity would hide the problem. "
                         "Shorten or re-sequence the work that drives it.")}

    if kind == DEADLINE:
        date = str(spec.get("date") or "")[:10]
        # A deadline, not a pull: this caps the late date so a slip shows as
        # negative float. Forcing the work onto the date would schedule the
        # problem away instead of reporting it.
        for d in res["detail"]:
            cmds.append({"action": "set_constraint",
                         "activity_id": d["activity_id"],
                         "constraint_type": "Finish On Or Before",
                         "constraint_date": date})
        return {"check": res, "commands": cmds}

    if kind in (REACHES, FOLLOWS):
        b_pat = spec.get("to") or spec.get("driver") or ""
        targets = [a for a in project.activities
                   if _matches(a, b_pat) and _in_scope(project, a, scope)]
        if not targets:
            return {"check": res, "commands": []}

        def _d(v):
            return str(v or "")[:10]

        by_id = {a.activity_id: a for a in project.activities}
        for row in res["detail"]:
            a = by_id.get(row["activity_id"])
            if a is None:
                continue
            if kind == REACHES:
                # the earliest target that starts AFTER this finishes, so the
                # tie runs forward in time rather than backward
                later = [t for t in targets
                         if _d(t.planned_start) >= _d(a.planned_finish)]
                pick = min(later, key=lambda t: _d(t.planned_start)) if later \
                    else max(targets, key=lambda t: _d(t.planned_start))
                if pick.uid != a.uid:
                    cmds.append({"action": "add_relation",
                                 "predecessor_id": a.activity_id,
                                 "successor_id": pick.activity_id,
                                 "type": "fs", "lag_days": 0})
            else:
                earlier = [t for t in targets
                           if _d(t.planned_finish) <= _d(a.planned_start)]
                pick = max(earlier, key=lambda t: _d(t.planned_finish)) if earlier \
                    else min(targets, key=lambda t: _d(t.planned_finish))
                if pick.uid != a.uid:
                    cmds.append({"action": "add_relation",
                                 "predecessor_id": pick.activity_id,
                                 "successor_id": a.activity_id,
                                 "type": "fs", "lag_days": 0})
        return {"check": res, "commands": cmds}

    return {"check": res, "commands": []}


def report(project, specs: List[Dict[str, Any]]) -> str:
    """Several requirements checked at once — the verification pass."""
    if not specs:
        return "No requirements given to check."
    out, failed = [], 0
    for spec in specs:
        r = check(project, spec)
        label = spec.get("label") or r.get("statement") or spec.get("kind")
        if r.get("error"):
            out.append(f"  ?  {label}\n       {r['error']}")
            failed += 1
            continue
        if r["passed"]:
            out.append(f"  OK  {label}  ({r['matched']} activities checked)")
            continue
        failed += 1
        if r["kind"] == NOT_AFTER:
            out.append(f"  ✗  {label}"
                       f"\n       {r['violations']} of {r['matched']} break "
                       f"this — worst is {r['worst_days_over']} days over:")
            for d in r["detail"][:6]:
                out.append(f"       · {d['activity_id']}  {d['name']}"
                           f"  [{d['folder']}] — {d['finish']}, "
                           f"{d['days_over']}d over")
            if r["violations"] > 6:
                out.append(f"       · …and {r['violations'] - 6} more")
            continue
        out.append(f"  ✗  {label}"
                   f"\n       {r['violations']} of {r['matched']} break this:")
        for d in r["detail"][:6]:
            extra = f" (finishes {d['finish']})" if d.get("finish") else ""
            out.append(f"       · {d['activity_id']}  {d['name']}"
                       f"  [{d['folder']}]{extra}")
        if r["violations"] > 6:
            out.append(f"       · …and {r['violations'] - 6} more")
    head = (f"REQUIREMENTS — {len(specs) - failed} of {len(specs)} hold. "
            f"Nothing has been changed.")
    return head + "\n" + "\n".join(out)
