# -*- coding: utf-8 -*-
"""
schedule_preview.py — what would pressing Schedule do, and why?

WHY THIS EXISTS
  Schedule (F9) is the one action that rewrites Start and Finish across the
  whole job. On a schedule that is still being wired it can move thousands of
  rows at once, and the honest way to find out what it did used to be: press
  it, look, and undo if you did not like it. That works, but it churns the
  project, spends an undo step, and leaves the agent reasoning about a change
  it has already made.

  Running the same pass on a COPY answers the question without any of that.
  Nothing in the live project is touched, so it is safe to run at any time,
  as often as you like, and the agent can use it to decide whether to
  schedule at all — or what to fix first.

WHY EACH ROW MOVES
  A count of "1,904 activities would move" is not actionable. The reason is,
  and there are only a few:

    no logic      the activity has no predecessor, so an explicit F9 drives
                  it to the data date. On a job with hundreds of open starts
                  this is the whole story, and it is the single biggest
                  reason a reflow looks catastrophic
    driven        a predecessor's finish moved it
    pinned        a constraint holds it somewhere the logic disagrees with
    data date     remaining work cannot sit in the past

  Grouping by reason turns "everything moved" into "1,650 of these have no
  predecessor — wire those first and the reflow becomes readable".
"""

from typing import Any, Dict, List, Optional

NO_LOGIC = "no logic"
DRIVEN = "driven by a predecessor"
PINNED = "held by a constraint"
DATA_DATE = "pulled to the data date"
OTHER = "other"


def _snapshot(project):
    """An independent copy. Every field is an immutable scalar or frozenset,
    so a shallow copy per object is already fully independent — the same
    reasoning (and the same cost saving over deepcopy) as the undo stack."""
    import copy as _copy

    from engine.schedule_model import Project
    snap = Project(
        uid=project.uid, name=project.name, id=project.id,
        data_date=project.data_date, planned_start=project.planned_start,
        must_finish_by=project.must_finish_by, status_code=project.status_code,
        calendars=[_copy.copy(c) for c in project.calendars],
        wbs_nodes=[_copy.copy(w) for w in project.wbs_nodes],
        activities=[_copy.copy(a) for a in project.activities],
        relations=[_copy.copy(r) for r in project.relations],
    )
    snap.build_lookups()
    return snap


def _reason(act, has_pred: bool, data_date: Optional[str]) -> str:
    if not has_pred:
        return NO_LOGIC
    if (act.constraint_type or "").strip() and act.constraint_date:
        return PINNED
    if data_date and str(act.planned_start or "")[:10] == str(data_date)[:10]:
        return DATA_DATE
    return DRIVEN


def analyse(project, data_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Run the Schedule pass on a copy and report the difference.

    The live project is never touched — verified by a test, because a preview
    that quietly reschedules is worse than no preview at all. That is also
    what makes `data_date` safe here: it previews a run from a date the
    project has not been statused to yet, without committing to it.
    """
    from engine.schedule_model import compute_dates

    before = {a.uid: (str(a.planned_start or "")[:10],
                      str(a.planned_finish or "")[:10])
              for a in project.activities}
    finish_before = max([v[1] for v in before.values() if v[1]] or [""]) or None

    trial = _snapshot(project)
    if data_date:
        import datetime as _dd
        try:
            trial.data_date = _dd.date.fromisoformat(
                str(data_date)[:10]).isoformat()
        except ValueError:
            return {"error": f"Not a valid date: {data_date!r}"}
    compute_dates(trial, hold_unlinked_dates=False, apply_dates=True)

    has_pred = {a.uid: False for a in trial.activities}
    for r in trial.relations:
        if r.successor_uid in has_pred:
            has_pred[r.successor_uid] = True

    folder_name = {w.uid: w.name for w in trial.wbs_nodes}
    moved: List[Dict[str, Any]] = []
    for a in trial.activities:
        was = before.get(a.uid)
        now = (str(a.planned_start or "")[:10], str(a.planned_finish or "")[:10])
        if was is None or was == now:
            continue
        shift = 0
        if was[0] and now[0]:
            try:
                import datetime as _d
                shift = (_d.date.fromisoformat(now[0])
                         - _d.date.fromisoformat(was[0])).days
            except ValueError:
                shift = 0
        moved.append({
            "activity_id": a.activity_id, "name": a.name,
            "folder": folder_name.get(a.wbs_uid, ""),
            "from": was[0], "to": now[0],
            "from_finish": was[1], "to_finish": now[1],
            "shift_days": shift,
            "reason": _reason(a, has_pred.get(a.uid, False), trial.data_date),
        })

    finish_after = max(
        [str(a.planned_finish)[:10] for a in trial.activities
         if a.planned_finish] or [""]) or None

    by_reason: Dict[str, int] = {}
    for m in moved:
        by_reason[m["reason"]] = by_reason.get(m["reason"], 0) + 1

    return {
        "total": len(project.activities),
        "moved": len(moved),
        "unchanged": len(project.activities) - len(moved),
        "finish_before": finish_before,
        "finish_after": finish_after,
        "by_reason": by_reason,
        "movers": sorted(moved, key=lambda m: -abs(m["shift_days"])),
        "data_date": str(trial.data_date)[:10] if trial.data_date else None,
        "project_data_date": (str(project.data_date)[:10]
                              if project.data_date else None),
        "data_date_override": bool(data_date),
    }


def report(project, max_rows: int = 15, data_date: Optional[str] = None) -> str:
    """The preview as prose, for the chat and for the agent to reason from."""
    d = analyse(project, data_date)
    if d.get("error"):
        return d["error"]
    if d.get("data_date_override"):
        head = (f"PREVIEW AS OF {d['data_date']} — a date the project has not "
                f"been statused to (its data date is still "
                f"{d['project_data_date'] or 'not set'}). Nothing here is "
                f"committed; remaining work is simply floored at "
                f"{d['data_date']} for the trial.\n")
    else:
        head = ""
    if not d["moved"]:
        return head + ("Pressing Schedule would move nothing — every date "
                       "already agrees with the logic driving it. Safe to run, "
                       "but it will not change anything.")

    out = [head + f"IF YOU PRESS SCHEDULE — {d['moved']} of {d['total']} "
           f"activities would move. Nothing has been changed by this check."]
    if d["finish_before"] != d["finish_after"]:
        out.append(f"  Project finish {d['finish_before'] or '—'} → "
                   f"{d['finish_after'] or '—'}")
    else:
        out.append(f"  Project finish unchanged ({d['finish_after'] or '—'})")

    out.append("\nWHY THEY MOVE:")
    for reason, n in sorted(d["by_reason"].items(), key=lambda kv: -kv[1]):
        out.append(f"  {n:>6}  {reason}")

    if d["by_reason"].get(NO_LOGIC):
        n = d["by_reason"][NO_LOGIC]
        out.append(
            f"\n  ⚠ {n} of those have NO PREDECESSOR. An explicit Schedule "
            f"drives unlinked work to the data date ({d['data_date'] or '—'}), "
            f"which is why the reflow looks drastic. Wiring those first makes "
            f"the result readable — and is worth doing before scheduling.")

    out.append("\nBIGGEST MOVES:")
    for m in d["movers"][:max_rows]:
        arrow = f"{m['from'] or '—'} → {m['to'] or '—'}"
        sign = f"{m['shift_days']:+d}d" if m["shift_days"] else "same start"
        out.append(f"  {m['activity_id']}  {arrow}  ({sign})  [{m['reason']}]"
                   f"\n      {m['name']}  ·  {m['folder']}")
    if d["moved"] > max_rows:
        out.append(f"  …and {d['moved'] - max_rows} more")
    return "\n".join(out)
