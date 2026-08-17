"""
loading.py — how much crew the schedule is asking for, week by week.

A schedule can be perfectly valid as a network and still be impossible to
staff. The dates say the work fits; only spreading the crew across the working
days shows the weeks where it does not. On the reference schedule the busiest
week runs 34x as many concurrent activities as the median one, which is the
shape of a plan that has been dated rather than resourced.

Each activity's crew is spread evenly over its own working days — an activity
running Mon-Fri with 4 electricians contributes 4 to each of those five days,
not 20 to the day it starts. Weeks are ISO weeks, so they line up with how a
lookahead is talked about on site.

Nothing here writes to the project.
"""

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from .schedule_model import Project, Activity


# Titles that mean "how many people", in the order they are preferred.
CREW_PATTERNS = ("electric", "crew size", "crew", "manpower", "headcount",
                 "workers", "labor count", "manning")


def crew_field(project: Project) -> Optional[str]:
    """The UDF that holds a headcount, or None if the schedule carries no such column."""
    titles = [u.title for u in (getattr(project, "udf_types", None) or []) if u.title]
    for a in project.activities:
        for t in (getattr(a, "udfs", None) or {}):
            if t not in titles:
                titles.append(t)
    for pat in CREW_PATTERNS:
        for t in titles:
            if pat in (t or "").lower():
                return t
    return None


def _num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        return float(m.group(0)) if m else None


def _cal_of(project: Project, act: Activity):
    for c in (project.calendars or []):
        if c.uid == getattr(act, "calendar_uid", None):
            return c
    return (project.calendars or [None])[0]


def _work_days(project: Project, act: Activity, start: _dt.date,
               finish: _dt.date, cap: int = 800) -> List[_dt.date]:
    """The working days an activity actually occupies, on its own calendar."""
    cal = _cal_of(project, act)
    wd = (getattr(cal, "work_days", None) if cal else None) or frozenset({0, 1, 2, 3, 4})
    hol = (getattr(cal, "holidays", None) if cal else None) or frozenset()
    out, d, guard = [], start, 0
    while d <= finish and guard < cap:
        if d.weekday() in wd and d.isoformat() not in hol:
            out.append(d)
        d += _dt.timedelta(days=1)
        guard += 1
    return out


def _parse(v) -> Optional[_dt.date]:
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _descendants(project: Project, root_uid: str) -> set:
    kids = {}
    for w in project.wbs_nodes:
        kids.setdefault(w.parent_uid, []).append(w.uid)
    out, stack = {root_uid}, [root_uid]
    while stack:
        for k in kids.get(stack.pop(), []):
            if k not in out:
                out.add(k)
                stack.append(k)
    return out


def crew_load(project: Project, udf_title: Optional[str] = None,
              scope_uid: Optional[str] = None,
              include_completed: bool = False,
              include_past: bool = False) -> Dict[str, Any]:
    """
    Crew demand per ISO week.

    Returns per week:
      crew_days   total person-days asked for  (crew x working days that week)
      peak_crew   the worst single day in that week — the number that has to be
                  on site at once, which is what actually breaks
      activities  how many activities are running
      unstaffed   activities running with no crew number on them, so the week's
                  figures are known to be an undercount rather than silently so

    With no crew column, or none filled in, crew is counted as 1 per activity
    and `counted_as_activities` says so — the shape of the curve is still worth
    seeing.
    """
    title = udf_title or crew_field(project)
    scope = _descendants(project, scope_uid) if scope_uid else None

    per_day: Dict[_dt.date, float] = {}
    per_day_acts: Dict[_dt.date, int] = {}
    per_day_unstaffed: Dict[_dt.date, int] = {}
    with_crew = 0

    for a in project.activities:
        if scope is not None and a.wbs_uid not in scope:
            continue
        if not include_completed and a.status == "Completed":
            continue
        if a.activity_type in ("Start Milestone", "Finish Milestone"):
            continue
        s = _parse(a.actual_start or a.planned_start or a.early_start)
        f = _parse(a.planned_finish or a.early_finish or a.actual_finish)
        if not s or not f or f < s:
            continue
        days = _work_days(project, a, s, f)
        if not days:
            continue
        crew = _num((getattr(a, "udfs", None) or {}).get(title)) if title else None
        if crew is not None and crew > 0:
            with_crew += 1
        per = crew if (crew is not None and crew > 0) else 1.0
        for d in days:
            per_day[d] = per_day.get(d, 0.0) + per
            per_day_acts[d] = per_day_acts.get(d, 0) + 1
            if crew is None or crew <= 0:
                per_day_unstaffed[d] = per_day_unstaffed.get(d, 0) + 1

    weeks: Dict[tuple, Dict[str, Any]] = {}
    for d, v in per_day.items():
        y, w, _ = d.isocalendar()
        b = weeks.setdefault((y, w), {"crew_days": 0.0, "peak_crew": 0.0,
                                      "activities": 0, "unstaffed": 0,
                                      "week_start": d})
        b["crew_days"] += v
        b["peak_crew"] = max(b["peak_crew"], v)
        b["activities"] = max(b["activities"], per_day_acts.get(d, 0))
        b["unstaffed"] = max(b["unstaffed"], per_day_unstaffed.get(d, 0))
        b["week_start"] = min(b["week_start"], d)

    # You cannot staff the past. Weeks that finished before the data date are
    # dropped by default — a schedule carrying stale dates on not-started work
    # otherwise opens on years of one-activity weeks with the real peak far
    # below the fold.
    floor = _parse(project.data_date)
    out = []
    for (y, w), b in sorted(weeks.items()):
        if floor and not include_past and b["week_start"] + _dt.timedelta(days=6) < floor:
            continue
        out.append({
            "week": f"{y}-W{w:02d}",
            "week_start": b["week_start"].isoformat(),
            "crew_days": round(b["crew_days"], 1),
            "peak_crew": round(b["peak_crew"], 1),
            "activities": b["activities"],
            "unstaffed": b["unstaffed"],
        })

    peaks = [r["peak_crew"] for r in out]
    peaks_sorted = sorted(peaks)
    median = peaks_sorted[len(peaks_sorted) // 2] if peaks_sorted else 0
    busiest = max(out, key=lambda r: r["peak_crew"]) if out else None
    return {
        "crew_field": title,
        "counted_as_activities": with_crew == 0,
        "activities_with_crew": with_crew,
        "weeks": out,
        "peak_crew": max(peaks) if peaks else 0,
        "median_peak_crew": median,
        "spike_ratio": round(max(peaks) / median, 1) if median else None,
        "busiest_week": busiest,
    }


def lookahead(project: Project, weeks: int = 3, start: Optional[str] = None,
              scope_uid: Optional[str] = None,
              include_completed: bool = False) -> Dict[str, Any]:
    """
    The next N weeks of work, grouped by WBS — the thing a foreman is handed.

    Anything that OVERLAPS the window is in, not only what starts in it: work
    already running is the first thing a crew needs to see. Rows carry what
    matters on site — dates, duration, crew, status, and whether the activity
    is critical — and nothing else.
    """
    from .logic_advisor import wbs_path

    first = _parse(start) or _parse(project.data_date) or _dt.date.today()
    # start the window on the Monday of that week, the way a lookahead reads
    first = first - _dt.timedelta(days=first.weekday())
    last = first + _dt.timedelta(days=weeks * 7 - 1)
    title = crew_field(project)
    scope = _descendants(project, scope_uid) if scope_uid else None

    groups: Dict[str, List[Dict[str, Any]]] = {}
    total = 0
    for a in project.activities:
        if scope is not None and a.wbs_uid not in scope:
            continue
        if not include_completed and a.status == "Completed":
            continue
        s = _parse(a.actual_start or a.planned_start or a.early_start)
        f = _parse(a.planned_finish or a.early_finish or a.actual_finish) or s
        if not s or not f or f < first or s > last:
            continue
        cal = _cal_of(project, a)
        hpd = (getattr(cal, "hours_per_day", None) if cal else None) or 8.0
        path = wbs_path(project, a)
        groups.setdefault(path, []).append({
            "activity_id": a.activity_id,
            "name": a.name,
            "start": s.isoformat(),
            "finish": f.isoformat(),
            "duration_days": round((a.planned_duration or 0) / hpd, 1),
            "status": a.status,
            "crew": _num((getattr(a, "udfs", None) or {}).get(title)) if title else None,
            "critical": bool(a.is_critical),
            "starts_in_window": s >= first,
        })
        total += 1

    for rows in groups.values():
        rows.sort(key=lambda r: (r["start"], r["activity_id"]))
    return {
        "from": first.isoformat(),
        "to": last.isoformat(),
        "weeks": weeks,
        "crew_field": title,
        "activity_count": total,
        "groups": [{"wbs_path": k, "activities": v} for k, v in sorted(groups.items())],
    }
