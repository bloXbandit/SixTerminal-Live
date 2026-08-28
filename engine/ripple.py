# -*- coding: utf-8 -*-
"""
ripple.py — reschedule ONE activity's path, and leave the rest of the job alone.

WHY THIS EXISTS
  There are only two speeds otherwise. Type a date and nothing moves — the
  logic downstream goes on saying something the schedule no longer supports.
  Or press Schedule and everything moves, including two thousand rows that
  had nothing to do with the change, on a job where 592 activities have no
  predecessor and would be dragged to the data date.

  Neither is what statusing an activity actually means. Actualising a start
  should push the work that DEPENDS on it and touch nothing else.

HOW IT WORKS, AND THE ONE SUBTLETY
  The CPM cannot be run on a fragment: an activity's dates depend on the whole
  network above it, so computing "just this path" would produce different
  numbers from the real schedule. So the pass runs GLOBALLY on a copy, exactly
  as the Schedule button does — and then only the dates of activities on the
  affected path are written back.

  That separation is the whole design. Computation is global because it has to
  be; the WRITE is scoped because that is what preserves the rest of the job.
  A row that moved in the trial but sits off the path is discarded, and it is
  discarded deliberately: it moved because a global reflow moves everything,
  not because of anything the user did.

WHAT COUNTS AS THE PATH
  The activity itself, plus everything reachable forward through relationships
  — its successors, their successors, and so on. That is precisely the set a
  change can legitimately affect. Predecessors are not included by default:
  moving an activity's start does not move what came before it, and pulling
  predecessors in would let an actualisation quietly rewrite history.

THE DATA DATE
  By default the pass runs from the project's own data date, so the projection
  it shows is the projection the schedule is actually making today. A future
  date can be supplied instead — "if we are standing here on 1 March, where
  does this path land" — and that override applies to the TRIAL ONLY. The
  project's data date is not moved: a ripple is meant to leave everything it
  did not touch exactly as it was, and the data date is a property of the
  whole job, not of one activity's path. Moving it for real is what the
  Schedule button does.
"""

from typing import Any, Dict, List, Optional, Set


def _d(v) -> str:
    return str(v or "")[:10]


def downstream(project, start_uid: str, include_self: bool = True) -> Set[str]:
    """Every activity reachable forward from here. Guarded against cycles."""
    adj: Dict[str, List[str]] = {}
    for r in project.relations:
        adj.setdefault(r.predecessor_uid, []).append(r.successor_uid)
    seen: Set[str] = {start_uid} if include_self else set()
    stack, guard = [start_uid], 0
    while stack and guard < 200000:
        guard += 1
        for v in adj.get(stack.pop(), ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def _snapshot(project):
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


def simulate(project, activity_id: str, changes: Optional[Dict[str, Any]] = None,
             include_predecessors: bool = False,
             data_date: Optional[str] = None) -> Dict[str, Any]:
    """
    What a change to one activity would do to the work that depends on it.

    `changes` takes the same fields the edit actions do — actual_start,
    actual_finish, planned_start, duration_days, status. Nothing is written:
    the trial runs on a copy and the report says what WOULD move.

    `data_date` projects from a different date than the project is statused
    from — "if today were 1 March, where does this path land". It applies to
    the trial only; the project's own data date is never moved by a ripple.
    """
    from engine.edit_engine import apply_commands
    from engine.schedule_model import compute_dates

    act = project.get_activity(activity_id=activity_id)
    if act is None:
        return {"error": f"No activity {activity_id}"}

    as_of = _d(project.data_date) or None
    if data_date:
        import datetime as _dt0
        try:
            as_of = _dt0.date.fromisoformat(str(data_date)[:10]).isoformat()
        except ValueError:
            return {"error": f"Not a valid date: {data_date!r}"}

    before = {a.uid: (_d(a.planned_start), _d(a.planned_finish))
              for a in project.activities}

    trial = _snapshot(project)
    # Trial only. The real project keeps the data date it had — see the module
    # docstring: a ripple must not change a property of the whole job.
    if data_date:
        trial.data_date = as_of
    changes = changes or {}
    cmds: List[Dict[str, Any]] = []
    if changes.get("actual_start"):
        cmds.append({"action": "set_actual_date", "activity_id": activity_id,
                     "field": "start", "date": changes["actual_start"]})
    if changes.get("actual_finish"):
        cmds.append({"action": "set_actual_date", "activity_id": activity_id,
                     "field": "finish", "date": changes["actual_finish"]})
    if changes.get("planned_start"):
        cmds.append({"action": "update_planned_date", "activity_id": activity_id,
                     "field": "start", "date": changes["planned_start"]})
        # A typed start on a LINKED, unpinned row does not survive the pass —
        # its predecessors drive it straight back, so the ripple would report
        # a date the user never asked for and quietly ignore the one they did.
        # A pin is what makes a typed date stick against logic, which is
        # exactly what the grid's own date cell does in this case. An actual
        # date needs none of this: the CPM anchors started work to it.
        has_pred = any(r.successor_uid == act.uid for r in project.relations)
        if has_pred and not (act.constraint_type or "").strip():
            cmds.append({"action": "set_constraint", "activity_id": activity_id,
                         "constraint_type": "Start On",
                         "constraint_date": changes["planned_start"],
                         "move_date": False})
    if changes.get("planned_finish"):
        cmds.append({"action": "update_planned_date", "activity_id": activity_id,
                     "field": "finish", "date": changes["planned_finish"]})
    if changes.get("duration_days") is not None:
        cmds.append({"action": "update_duration", "activity_id": activity_id,
                     "new_duration_days": changes["duration_days"]})
    if changes.get("status"):
        cmds.append({"action": "set_progress", "activity_id": activity_id,
                     "status": changes["status"]})

    failures = []
    if cmds:
        for cmd, (ok, msg) in zip(cmds, apply_commands(trial, cmds)):
            if not ok:
                failures.append(f"{cmd['action']}: {msg}")
    if failures:
        return {"error": "; ".join(failures)}

    # Global pass, because an activity's dates depend on the whole network
    # above it — a fragment would compute different numbers from the real
    # schedule. The SCOPE is applied to the write, not to the computation.
    compute_dates(trial, hold_unlinked_dates=True, apply_dates=True)

    path = downstream(trial, act.uid)
    if include_predecessors:
        back: Dict[str, List[str]] = {}
        for r in trial.relations:
            back.setdefault(r.successor_uid, []).append(r.predecessor_uid)
        seen, stack, guard = set(), [act.uid], 0
        while stack and guard < 200000:
            guard += 1
            for v in back.get(stack.pop(), ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        path |= seen

    on_path, off_path = [], 0
    folder_of = {w.uid: w.name for w in trial.wbs_nodes}
    for a in trial.activities:
        was = before.get(a.uid)
        now = (_d(a.planned_start), _d(a.planned_finish))
        if was is None or was == now:
            continue
        if a.uid not in path:
            off_path += 1
            continue
        shift = 0
        if was[0] and now[0]:
            import datetime as _dt
            try:
                shift = (_dt.date.fromisoformat(now[0])
                         - _dt.date.fromisoformat(was[0])).days
            except ValueError:
                shift = 0
        on_path.append({
            "activity_id": a.activity_id, "name": a.name,
            "folder": folder_of.get(a.wbs_uid, ""),
            "from": was[0], "to": now[0],
            "from_finish": was[1], "to_finish": now[1],
            "shift_days": shift,
        })

    on_path.sort(key=lambda r: (r["to"], -abs(r["shift_days"])))
    return {
        "activity_id": activity_id, "name": act.name,
        "changes": changes,
        "as_of": as_of,
        "as_of_override": bool(data_date),
        "project_data_date": _d(project.data_date) or None,
        "path_size": len(path),
        "moved_on_path": len(on_path),
        "would_move_off_path": off_path,
        "movers": on_path,
        "trial": trial, "path": path,
    }


def apply_ripple(project, activity_id: str,
                 changes: Optional[Dict[str, Any]] = None,
                 include_predecessors: bool = False,
                 data_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Make the change and let it flow down its own path — writing back ONLY the
    activities on that path, so everything else keeps the dates it had.

    A `data_date` projects the path from that date instead of the project's
    own. The dates it produces are written; the project's data date is not.
    """
    sim = simulate(project, activity_id, changes, include_predecessors, data_date)
    if sim.get("error"):
        return sim

    trial, path = sim["trial"], sim["path"]
    by_uid = {a.uid: a for a in trial.activities}
    written = 0
    for a in project.activities:
        if a.uid not in path:
            continue                     # off the path — left exactly as it was
        t = by_uid.get(a.uid)
        if t is None:
            continue
        if (a.planned_start, a.planned_finish, a.actual_start, a.actual_finish,
                a.status, a.percent_complete, a.remaining_duration,
                a.planned_duration) != (
                t.planned_start, t.planned_finish, t.actual_start,
                t.actual_finish, t.status, t.percent_complete,
                t.remaining_duration, t.planned_duration):
            written += 1
        a.planned_start, a.planned_finish = t.planned_start, t.planned_finish
        a.actual_start, a.actual_finish = t.actual_start, t.actual_finish
        a.status, a.percent_complete = t.status, t.percent_complete
        a.planned_duration = t.planned_duration
        a.remaining_duration = t.remaining_duration
        a.constraint_type, a.constraint_date = t.constraint_type, t.constraint_date

    project.build_lookups()
    # Refresh float and the critical path without rewriting Start/Finish —
    # the derived columns must agree with the dates that just changed.
    try:
        from engine.schedule_model import compute_dates
        compute_dates(project, apply_dates=False)
    except Exception:
        pass

    sim["written"] = written
    sim.pop("trial", None)
    sim.pop("path", None)
    return sim


def report(project, activity_id: str, changes: Optional[Dict[str, Any]] = None,
           include_predecessors: bool = False, max_rows: int = 20,
           data_date: Optional[str] = None) -> str:
    """The simulation as prose. Changes nothing."""
    r = simulate(project, activity_id, changes, include_predecessors, data_date)
    if r.get("error"):
        return r["error"]

    what = ", ".join(f"{k} → {v}" for k, v in (r["changes"] or {}).items())
    head = [f"RIPPLE from {r['activity_id']} — {r['name']}",
            f"  Change: {what or 'none, just reflowing its path'}"]
    if r["as_of_override"]:
        head.append(f"  Projected as of {r['as_of']} — the project's own data "
                    f"date ({r['project_data_date'] or 'not set'}) is NOT moved "
                    f"by this; remaining work simply cannot land before "
                    f"{r['as_of']} in the projection.")
    else:
        head.append(f"  As of the project data date {r['as_of'] or 'not set'}.")
    head.append(f"  Its path is {r['path_size']} activities "
                f"(itself plus everything downstream).")

    if not r["moved_on_path"]:
        head.append("\n  Nothing on the path would move — the dates already "
                    "agree with the logic.")
    else:
        head.append(f"\n  {r['moved_on_path']} would move:")
        for m in r["movers"][:max_rows]:
            sign = f"{m['shift_days']:+d}d" if m["shift_days"] else "finish only"
            head.append(f"    {m['activity_id']}  {m['from'] or '—'} → "
                        f"{m['to'] or '—'}  ({sign})"
                        f"\n        {m['name']}  ·  {m['folder']}")
        if r["moved_on_path"] > max_rows:
            head.append(f"    …and {r['moved_on_path'] - max_rows} more")

    if r["would_move_off_path"]:
        head.append(
            f"\n  {r['would_move_off_path']} activities elsewhere would have "
            f"moved under a full Schedule run. They are NOT on this activity's "
            f"path and will be left exactly as they are — that is the point of "
            f"doing it this way rather than pressing Schedule.")
    return "\n".join(head)
