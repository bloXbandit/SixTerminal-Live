# -*- coding: utf-8 -*-
"""
actualize.py — turn "the gens are done, we're out to terminations in ER 208"
into a statused schedule.

THE JOB THIS DOES
  What arrives is never a list of activity IDs. It is a lookahead with a data
  date three weeks past the app's, or a sentence: several generators are
  complete, we are out to X in that room, precast is finished on that side of
  the building. Every one of those describes a FRONT — where the work has got
  to — and the schedule update it implies is much larger than what was said.

  Saying "Gen 315 is complete" is also saying its feeders are pulled, its gear
  is set, and the precast it sits on went in months ago. Nobody lists those.
  They are not a judgement call either, and this is the point: they are the
  activities the finished one depends on, transitively, and that is a
  computation. "Work out what else must be true" is upstream closure, over the
  whole network, including the parts in other folders and other phases.

  So the input is a front and the output is a status update, and the step in
  between is the closure — which is exactly the step a person does badly,
  because following two thousand relations back by eye is not something anyone
  does twice.

WHAT IT IS CAREFUL ABOUT
  The implied set is the surprising part, so it is reported SEPARATELY from
  what was actually said. "You told me about 23 activities; that means 310
  others are also complete" is a sentence the user must get to read before
  anything is written, because if the closure reaches somewhere it should not,
  that is the moment to catch it — not after 300 rows have actual dates.

  It never un-completes anything. Work already statused keeps the dates it has;
  evidence adds to the picture rather than replacing it.

DATES, WHICH ARE THE FIDDLY PART
  An activity being completed needs actual dates and the evidence rarely gives
  them. The schedule's own planned dates are the best available answer for
  work that was already in the past — that is what it thought would happen and
  nothing contradicts it. For work the schedule still had in the FUTURE, the
  evidence does contradict it: it cannot finish next month if it is done now.
  Those are pulled back to land on the evidence date, keeping their duration,
  so the shape of the run survives instead of collapsing every row onto one
  day.

  Statusing does not reschedule. Float and the critical path are refreshed so
  the derived columns agree with the new actuals; planned dates elsewhere are
  left exactly where they were, as everywhere else in this app.
"""

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .connect import _children, _path_of, _rx, _source_folders, _subtree_activities
from .schedule_model import Project, WBSNode


def _d(v) -> str:
    return str(v or "")[:10]


def _iso(v) -> Optional[str]:
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise ValueError(f"Not a valid date: {v!r}")


def _days(a) -> float:
    """An activity's length in days, from whatever it has."""
    hrs = a.planned_duration if a.planned_duration is not None else 0.0
    return max(0.0, float(hrs) / 8.0)


def _shift_back(finish: str, days: float) -> str:
    d = _dt.date.fromisoformat(finish)
    return (d - _dt.timedelta(days=max(0, int(round(days))))).isoformat()


def _is_done(a) -> bool:
    return (a.status or "").strip().lower() in ("completed", "complete")


# ── resolving the evidence into a front ──────────────────────────────────────

def _front(project: Project,
           folder_pattern: Optional[str],
           activity_ids: Optional[List[str]],
           through: Optional[str],
           under: Optional[WBSNode]) -> Tuple[List, List, List[str]]:
    """
    What the evidence actually names.

    Returns (complete_now, started_now, notes). `through` splits a folder: the
    activity the work has reached is IN PROGRESS and everything feeding it is
    complete — which is what "we are out to terminations" means.
    """
    notes: List[str] = []
    complete, started = [], []
    by_id = {a.activity_id: a for a in project.activities}

    for aid in (activity_ids or []):
        a = by_id.get(aid)
        if a is None:
            notes.append(f"no activity {aid}")
        else:
            complete.append(a)

    if folder_pattern:
        kids = _children(project)
        acts_in: Dict[Optional[str], List] = {}
        for a in project.activities:
            acts_in.setdefault(a.wbs_uid, []).append(a)
        folders = _source_folders(project, _rx(folder_pattern, "folder_pattern"), under)
        if not folders:
            notes.append(f"no folder matched {folder_pattern!r}")
        t_rx = _rx(through, "through") if through else None
        for w in folders:
            acts = _subtree_activities(project, w, kids, acts_in)
            if not acts:
                notes.append(f"{w.name}: no activities in it")
                continue
            if t_rx is None:
                complete.extend(acts)
                continue
            hit = [a for a in acts if t_rx.search(a.name or "")]
            if not hit:
                notes.append(f"{w.name}: nothing matching {through!r} in it — "
                             f"left alone")
                continue
            # The front IS that activity; what feeds it is behind the front.
            started.extend(hit)
    return complete, started, notes


# ── the plan ─────────────────────────────────────────────────────────────────

def plan(project: Project,
         folder_pattern: Optional[str] = None,
         activity_ids: Optional[List[str]] = None,
         through: Optional[str] = None,
         as_of: Optional[str] = None,
         include_predecessors: bool = True,
         under: Optional[WBSNode] = None,
         preserve: Optional[str] = None) -> Dict[str, Any]:
    """
    What a statement of progress means for the schedule. Writes nothing.

    folder_pattern       regex over folder names the evidence is about
    activity_ids         explicit activities said to be complete
    through              within each folder, the activity the work has reached.
                         That one goes In Progress, everything feeding it goes
                         Complete — "we are out to terminations in ER 208".
    as_of                the date the evidence describes — a lookahead's data
                         date. Defaults to the project's own.
    include_predecessors pull in everything the named work depends on,
                         wherever it lives. This is the "precast must be done
                         if we are roughing that room" case, computed rather
                         than reasoned. Default on; it is the whole point.
    preserve             an activity whose finish the user wants held. Its date
                         before and after is reported — nothing is compressed
                         to hit it, because that is a different decision.
    """
    as_of = _iso(as_of) or _iso(project.data_date) or _dt.date.today().isoformat()

    named_c, named_s, notes = _front(project, folder_pattern, activity_ids,
                                     through, under)
    named_uids = {a.uid for a in named_c} | {a.uid for a in named_s}

    implied: List = []
    if include_predecessors and named_uids:
        from .connect import _graph, reachable
        _fwd, back = _graph(project)
        closure = reachable(back, named_uids, include_starts=False)
        by_uid = {a.uid: a for a in project.activities}
        implied = [by_uid[u] for u in closure if u in by_uid and u not in named_uids]

    before = _d(_finish_of_id(project, preserve)) if preserve else None

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    capped = 0
    already = 0

    def _add(a, want: str, why: str):
        nonlocal capped, already
        if a.uid in seen:
            return
        seen.add(a.uid)
        if _is_done(a) and want == "Completed":
            already += 1                # already there; nothing to say about it
            return
        cur_s, cur_f = _d(a.actual_start), _d(a.actual_finish)
        if want == "In Progress":
            start = cur_s or min(_d(a.planned_start) or as_of, as_of)
            rows.append({"activity_id": a.activity_id, "name": a.name,
                         "folder": _folder_name(project, a), "to": want,
                         "why": why, "actual_start": start,
                         "actual_finish": None, "uid": a.uid,
                         "was": a.status or "Not Started"})
            return
        # Completed. Planned dates are the best answer for work already in the
        # past; work the schedule still had in the future is pulled back to the
        # evidence date, keeping its duration so the run keeps its shape.
        fin = cur_f or _d(a.planned_finish) or as_of
        if fin > as_of:
            # The schedule still had this in the future and the evidence says
            # it is done. The evidence wins, but the disagreement is worth
            # counting: a lot of these means the plan is well behind what is
            # actually built, which is the headline, not a detail.
            fin = as_of
            capped += 1
        start = cur_s or _d(a.planned_start) or fin
        if start > fin:
            start = _shift_back(fin, _days(a))
        rows.append({"activity_id": a.activity_id, "name": a.name,
                     "folder": _folder_name(project, a), "to": want,
                     "why": why, "actual_start": start, "actual_finish": fin,
                     "uid": a.uid, "was": a.status or "Not Started"})

    for a in named_s:
        _add(a, "In Progress", "named")
    for a in named_c:
        _add(a, "Completed", "named")
    for a in implied:
        _add(a, "Completed", "implied")

    # The closure is only ever as good as the logic. An activity with no
    # predecessors implies nothing, so on a job with a lot of open starts the
    # implied set is thin — and that is a fact about the schedule, not a
    # limit the user should have to discover by trusting a short list.
    from .connect import _graph
    _f, back = _graph(project)
    unlinked = sum(1 for u in named_uids if not back.get(u))

    return {
        "action": "actualize",
        "applied": False,
        "as_of": as_of,
        "data_date": _d(project.data_date) or None,
        "named": [r for r in rows if r["why"] == "named"],
        "implied": [r for r in rows if r["why"] == "implied"],
        "already_complete": already,
        "capped": capped,
        "named_unlinked": unlinked,
        "named_total": len(named_uids),
        "notes": notes,
        "preserve": preserve,
        "preserve_before": before,
        "rows": rows,
    }


def _finish_of_id(project: Project, activity_id: Optional[str]):
    if not activity_id:
        return None
    a = project.get_activity(activity_id=activity_id)
    return None if a is None else (a.actual_finish or a.planned_finish)


def _folder_name(project: Project, a) -> str:
    w = project.get_wbs(a.wbs_uid) if hasattr(project, "get_wbs") else None
    if w is not None:
        return w.name or ""
    for w in project.wbs_nodes:
        if w.uid == a.wbs_uid:
            return w.name or ""
    return ""


def actualize(project: Project, apply: bool = False, **kw) -> Dict[str, Any]:
    """
    Status the schedule from a statement of progress.

    Nothing already complete is undone; evidence adds to the picture. With
    apply=False (the default) this is `plan` — the closure is reported before
    it is written, because a closure reaching somewhere it should not is
    something to catch before 300 rows have actual dates on them.
    """
    res = plan(project, **kw)
    if not apply or not res["rows"]:
        return res

    by_uid = {a.uid: a for a in project.activities}
    written = 0
    for r in res["rows"]:
        a = by_uid.get(r["uid"])
        if a is None:
            continue
        if r["to"] == "In Progress":
            a.actual_start = r["actual_start"]
            a.actual_finish = None
            if not (0 < (a.percent_complete or 0) < 100):
                a.percent_complete = 50.0
            a.remaining_duration = (a.planned_duration or 0) * (
                1 - a.percent_complete / 100.0)
            a.status = "In Progress"
        else:
            a.actual_start = r["actual_start"]
            a.actual_finish = r["actual_finish"]
            a.percent_complete = 100.0
            a.remaining_duration = 0.0
            a.status = "Completed"
        written += 1

    project.build_lookups()
    # Statusing is not a reschedule. Float and the critical path have to agree
    # with the actuals that just landed; planned dates stay where they were.
    try:
        from .schedule_model import compute_dates
        compute_dates(project, apply_dates=False)
    except Exception:
        pass

    res["applied"] = True
    res["written"] = written
    if res["preserve"]:
        res["preserve_after"] = _d(_finish_of_id(project, res["preserve"]))
    return res


def describe(result: Dict[str, Any], limit: int = 12) -> str:
    """The plan as something to say yes or no to."""
    named, implied = result["named"], result["implied"]
    if not named and not implied:
        out = ["Nothing to actualise — the evidence names work that is already "
               "statused, or nothing matched."]
        out += [f"  {n}" for n in result["notes"]]
        return "\n".join(out)

    verb = "Statused" if result["applied"] else "Would status"
    out = [f"{verb} {len(named) + len(implied)} activities as of "
           f"{result['as_of']}"
           + (f" (the project's data date is {result['data_date']})"
              if result["data_date"] and result["data_date"] != result["as_of"]
              else "") + "."]
    out.append(f"  {len(named)} you named.")
    if implied:
        out.append(f"  {len(implied)} follow from them — work the named "
                   f"activities depend on, so it must already be done.")
    if result["already_complete"] > 0:
        out.append(f"  {result['already_complete']} were already complete and "
                   f"keep the dates they have.")
    if result.get("capped"):
        out.append(f"  {result['capped']} were still in the FUTURE in the "
                   f"schedule — the plan is behind what you are telling me, so "
                   f"they finish on {result['as_of']} rather than when it "
                   f"thought they would.")
    if result.get("named_unlinked") and result.get("named_total"):
        n, t = result["named_unlinked"], result["named_total"]
        # A third is enough to distort the answer. One open start in five
        # hundred is not worth a line; one in three means the short knock-on
        # list is about the logic rather than about the job.
        if n >= max(1, t // 3):
            out.append(f"  Heads up: {n} of the {t} you named have no "
                       f"predecessor at all, so they imply nothing. The "
                       f"knock-on list below is short because of the logic, "
                       f"not because there is little else to catch up.")

    def _block(title, rows):
        if not rows:
            return []
        lines = ["", title]
        for r in rows[:limit]:
            when = (f"{r['actual_start']} → {r['actual_finish']}"
                    if r["actual_finish"] else f"started {r['actual_start']}")
            lines.append(f"  {r['activity_id']:12} {r['to']:12} {when}")
            lines.append(f"      {r['name']}  ·  {r['folder']}")
        if len(rows) > limit:
            lines.append(f"  …and {len(rows) - limit} more")
        return lines

    out += _block("Named by the evidence:", named)
    out += _block("Implied by the logic — nothing above can be true without these:",
                  implied)

    if result["notes"]:
        out.append("")
        out.append("Not acted on:")
        out += [f"  {n}" for n in result["notes"]]

    if result.get("preserve"):
        b, a = result.get("preserve_before"), result.get("preserve_after")
        out.append("")
        if not result["applied"]:
            out.append(f"{result['preserve']} finishes {b or 'unknown'} today. "
                       f"Apply this and I will report where it lands.")
        elif b and a and a != b:
            out.append(f"{result['preserve']} moved {b} → {a}. Holding the "
                       f"original date means shortening or overlapping work "
                       f"between here and there — say the word and I will show "
                       f"you where the time can come from.")
        elif b:
            out.append(f"{result['preserve']} still finishes {b} — the date holds.")

    if not result["applied"]:
        out.append("")
        out.append("Nothing has been changed. Say go ahead to apply it.")
    return "\n".join(out)
