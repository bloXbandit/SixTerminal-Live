# -*- coding: utf-8 -*-
"""
logic_transplant.py — giving a dateless schedule its relationships back.

WHY
  A schedule read out of a printed Gantt has every activity, every date and no
  logic at all, because a bar chart shows bars and not what drives them. Such a
  schedule looks fine and is inert: nothing moves when something slips, there is
  no critical path, and pressing F9 collapses the whole thing to the data date.

  There are two places the missing logic can honestly come from.

  THE DATES THEMSELVES. If one activity in a room finishes on Tuesday and the
  next starts on Wednesday, the printed schedule is already asserting a
  finish-to-start tie. Reading it back out is not inference, it is transcription.
  It is also self-consistent by construction: add those ties and rescheduling
  moves nothing, which is the only acceptance test that matters here.

  A SISTER JOB'S PATTERNS. Three buildings on one site are scheduled the same
  way by the same people. Where the sister job has `FORM & TIE REBAR` driving
  `POUR SLAB`, and both those activities exist in a room here, that tie belongs
  here too. This is what recovers the Start-to-Start and Finish-to-Finish
  relations, which dates alone cannot reveal — two activities starting together
  look independent.

WHAT IT WILL NOT DO
  Reach 100%. The sister job this is taken from ties 1.10 relations per activity
  and leaves 494 of 2,427 activities without a predecessor. Beating that would
  mean inventing sequence nobody designed. Parity is the target; the open ends
  are reported so a planner can close the ones that matter.

  Make a cycle. Every tie is checked against the graph before it is added, and
  one that would close a loop is dropped and reported. P6 will import a cycle
  and then refuse to schedule.

  Touch logic that is already there. A schedule that has been worked on keeps
  every relation it has; this only adds.
"""

import collections
import datetime as _dt
import re as _re
from typing import Any, Dict, List, Optional, Set, Tuple

FS, SS, FF, SF = ("Finish to Start", "Start to Start",
                  "Finish to Finish", "Start to Finish")

# Relation.lag is stored in HOURS, like every other duration in the model, and
# the scheduler divides it back out by the calendar's hours per day. Writing
# working days into it made every lag eight times too short: a 254-day gap
# became 32, and a thousand ties each losing most of their gap is what moved
# 1,652 dates by up to eight months.
HOURS_PER_DAY = 8.0

# How many working days may sit between a finish and the next start for the gap
# to read as "this drove that". Zero is the same day, which P6 writes for an FS
# with no lag; two allows for a weekend landing oddly or a day of float the
# planner left in. Beyond that it is two pieces of work that happen to be near
# each other.
ADJACENT_DAYS = 2

# A pattern seen fewer times than this in the source is one planner's one-off,
# not the way the job is built.
MIN_PATTERN = 2


def _d(value) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _norm(text) -> str:
    return " ".join(str(text or "").split()).upper()


# The room or area an activity name carries, which has to come off before two
# rooms can be compared. "Install High Steel (Gen 315)" and "Install High Steel
# (Gen 316)" are the same step in two rooms; left whole, every pattern is seen
# exactly once and the "seen at least twice" rule throws all of them away. That
# is what made a first run learn 134 patterns and place none of them in MDC-3.
_PLACE_TAIL = _re.compile(
    r"\s*(?:\((?:GEN|MV|HV|ER|DH|GL|GAL|CUP|ROOM|RM|AREA|LEVEL|LVL|PH)\s*[\w\-/.]*\)"
    r"|\s*[-–]\s*(?:GEN|MV|HV|ER|DH|GL|GAL)\s*\d{1,4}[A-Z]?"
    r"|\s*[-–]\s*DATA\s*CENTER\s*[ABC]"
    r"|\s*[-–]\s*PHASE\s*\d)\s*$", _re.I)


def _step(text) -> str:
    """An activity name with the room it happens in taken off the end."""
    out = _norm(text)
    for _ in range(3):          # names carry up to a couple of these
        trimmed = _PLACE_TAIL.sub("", out).strip()
        if trimmed == out:
            break
        out = trimmed
    return out or _norm(text)


def _work_days(a: _dt.date, b: _dt.date,
               holidays: frozenset = frozenset()) -> int:
    """
    Working days from a to b, a counting as zero. Negative if b precedes a.

    The holidays are the successor calendar's, and they matter: a lag measured
    straight through a holiday is short by a day once the scheduler stops for
    it, which is how twelve Precast turnovers came out one to five days early
    against a print built on the GC's calendar.
    """
    if b == a:
        return 0
    step = 1 if b > a else -1
    n, day = 0, a
    while day != b:
        day += _dt.timedelta(days=step)
        if day.weekday() < 5 and day.isoformat() not in holidays:
            n += step
    return n


def _holidays_by_activity(project) -> Dict[str, frozenset]:
    """Each activity's own calendar's holidays, for measuring gaps on it."""
    cals = {c.uid: frozenset(getattr(c, "holidays", None) or ())
            for c in (getattr(project, "calendars", None) or [])}
    return {a.uid: cals.get(getattr(a, "calendar_uid", None), frozenset())
            for a in project.activities}


def _folders(project) -> Dict[str, str]:
    """Each activity's leaf folder uid, which is where sequence lives."""
    return {a.uid: getattr(a, "wbs_uid", None) for a in project.activities}


def patterns(source, min_seen: int = MIN_PATTERN) -> Dict[Tuple[str, str], Dict]:
    """
    How the sister job sequences work, as (predecessor name -> successor name).

    Only ties inside one folder are learned. A cross-folder tie in the source
    says something about ITS areas — this room feeds that corridor — and does
    not transfer to a building laid out differently. Those are the 287 long-range
    relations that have to be put back by a planner, not by a name match.
    """
    by_uid = {a.uid: a for a in source.activities}
    folder = _folders(source)
    seen: Dict[Tuple[str, str], collections.Counter] = collections.defaultdict(
        collections.Counter)
    for rel in source.relations:
        p, s = by_uid.get(rel.predecessor_uid), by_uid.get(rel.successor_uid)
        if not (p and s):
            continue
        if folder.get(p.uid) != folder.get(s.uid):
            continue
        key = (_step(p.name), _step(s.name))
        seen[key][(getattr(rel, "type", FS) or FS,
                   float(getattr(rel, "lag", 0) or 0))] += 1
    out = {}
    for key, kinds in seen.items():
        total = sum(kinds.values())
        if total < min_seen or key[0] == key[1]:
            continue
        (kind, lag), _ = kinds.most_common(1)[0]
        out[key] = {"type": kind, "lag": lag, "seen": total}
    return out


class _Graph:
    """Just enough graph to refuse a cycle."""

    def __init__(self, project):
        self.fwd: Dict[str, Set[str]] = collections.defaultdict(set)
        self.have: Set[Tuple[str, str]] = set()
        for rel in project.relations:
            self.fwd[rel.predecessor_uid].add(rel.successor_uid)
            self.have.add((rel.predecessor_uid, rel.successor_uid))

    def would_cycle(self, pred: str, succ: str) -> bool:
        """True if pred already depends on succ, directly or through others."""
        if pred == succ:
            return True
        stack, seen = [succ], {succ}
        while stack:
            node = stack.pop()
            if node == pred:
                return True
            for nxt in self.fwd.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def add(self, pred: str, succ: str) -> None:
        self.fwd[pred].add(succ)
        self.have.add((pred, succ))


def from_dates(project, adjacent: int = ADJACENT_DAYS) -> List[Dict]:
    """
    The ties the printed dates already assert.

    For each activity, the activity in its own folder whose finish sits closest
    before its start — within `adjacent` working days — is its predecessor. One
    predecessor each, which is what keeps the density near the source's rather
    than wiring every pair that happens to be adjacent.

    Activities sharing a start date get a Start-to-Start from whichever of them
    the planner evidently drove first, because a shared start is exactly what
    dates cannot distinguish from coincidence — so it is only taken where the
    two already share a predecessor-shaped relationship through the folder.
    """
    folder = _folders(project)
    hols = _holidays_by_activity(project)
    groups: Dict[Any, List] = collections.defaultdict(list)
    for a in project.activities:
        s, f = _d(getattr(a, "planned_start", None)), _d(getattr(a, "planned_finish", None))
        if s and f:
            groups[folder.get(a.uid)].append((s, f, a))

    found: List[Dict] = []
    for _, acts in groups.items():
        if len(acts) < 2:
            continue
        acts.sort(key=lambda z: (z[0], z[1], str(getattr(z[2], "activity_id", ""))))
        for i, (start, _f, act) in enumerate(acts):
            best, best_gap = None, None
            # Measured on the successor's calendar, which is the one the
            # forward pass will use when it places this activity.
            hol = hols.get(act.uid, frozenset())
            for (s2, f2, other) in acts:
                if other is act or f2 > start:
                    continue
                gap = _work_days(f2, start, hol)
                if 0 <= gap <= adjacent and (best_gap is None or gap < best_gap
                                             or (gap == best_gap and f2 > _d(best.planned_finish))):
                    best, best_gap = other, gap
            if best is not None:
                # Exactly the printed gap, for the same reason: a tie written
                # at zero lag pulls its successor forward by whatever slack the
                # print had, and a thousand of those compound down a chain.
                found.append({"pred": best.uid, "succ": act.uid, "type": FS,
                              "lag": float(best_gap - 1) * HOURS_PER_DAY,
                              "why": f"dates: finishes {best_gap} working "
                                     f"day(s) before this starts",
                              "source": "dates"})
    return found


def _implied_lag(pred, succ, kind: str,
                 holidays: frozenset = frozenset()) -> Optional[float]:
    """
    The lag this schedule's own dates give a tie of this kind, in working days.

    A pattern learned from the sister job says WHICH activity drives which. It
    does not say how far apart they sit here, and taken at zero lag it forces
    them adjacent — which is how adding 1,154 correct relationships moved 1,704
    dates, some by half a year. The relationship is real; the gap is this job's,
    so it is written as lag and the printed dates survive.

    None when the dates contradict the kind — a successor that starts before
    its predecessor finishes is not finish-to-start here, whatever it is on the
    other building, and a tie that has to be bent to fit is not evidence.
    """
    ps, pf = _d(getattr(pred, "planned_start", None)), _d(getattr(pred, "planned_finish", None))
    ss, sf = _d(getattr(succ, "planned_start", None)), _d(getattr(succ, "planned_finish", None))
    if kind == FS and pf and ss:
        # The pass computes ES = predecessor EF + lag + 1 working days, so the
        # lag that reproduces a printed gap of g is g - 1. Clamping it at zero
        # cost a day on every tie whose successor started the day its
        # predecessor finished, and those are the common case.
        gap = _work_days(pf, ss, holidays)
        return float(gap - 1) * HOURS_PER_DAY if gap >= 0 else None
    if kind == SS and ps and ss:
        gap = _work_days(ps, ss, holidays)
        return float(gap) * HOURS_PER_DAY if gap >= 0 else None
    if kind == FF and pf and sf:
        gap = _work_days(pf, sf, holidays)
        return float(gap) * HOURS_PER_DAY if gap >= 0 else None
    if kind == SF and ps and sf:
        gap = _work_days(ps, sf, holidays)
        return float(gap) * HOURS_PER_DAY if gap >= 0 else None
    return None


def from_patterns(project, learned: Dict[Tuple[str, str], Dict],
                  keep_dates: bool = True) -> List[Dict]:
    """
    The sister job's sequence, where both named activities sit in one folder.

    This is what recovers Start-to-Start and Finish-to-Finish: two activities
    beginning on the same day look unrelated in a date table, and the source
    says one drives the other.

    With `keep_dates`, each tie carries the lag this schedule's own dates imply,
    so the relationship is added without moving anything. Without it, the
    source's own lag is used and the dates will shift.
    """
    folder = _folders(project)
    hols = _holidays_by_activity(project)
    by_folder: Dict[Any, Dict[str, List]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for a in project.activities:
        by_folder[folder.get(a.uid)][_step(a.name)].append(a)

    found: List[Dict] = []
    contradicted = 0
    for _, names in by_folder.items():
        for (pname, sname), how in learned.items():
            if pname not in names or sname not in names:
                continue
            # One name can appear twice in a folder (east/west halves). Pair
            # them in date order rather than crossing them over.
            ps = sorted(names[pname], key=lambda a: str(getattr(a, "planned_start", "")))
            ss = sorted(names[sname], key=lambda a: str(getattr(a, "planned_start", "")))
            for p, s in zip(ps, ss):
                lag = how["lag"]
                if keep_dates:
                    lag = _implied_lag(p, s, how["type"],
                                       hols.get(s.uid, frozenset()))
                    if lag is None:
                        contradicted += 1
                        continue
                found.append({"pred": p.uid, "succ": s.uid, "type": how["type"],
                              "lag": lag,
                              "why": f"the sister job runs this pair "
                                     f"{how['seen']} time(s); lag from this "
                                     f"schedule's own dates"
                              if keep_dates else
                              f"the sister job runs this pair {how['seen']} time(s)",
                              "source": "pattern"})
    from_patterns.last_contradicted = contradicted
    return found


from_patterns.last_contradicted = 0


def plan(project, source, adjacent: int = ADJACENT_DAYS,
         min_seen: int = MIN_PATTERN,
         keep_dates: bool = True) -> Dict[str, Any]:
    """
    Every tie that would be added, in the order they are trusted.

    Dates first, because they are this schedule's own assertion and adding them
    changes no date. Patterns second, filling what dates cannot see. A tie
    already present, a duplicate, or one that would close a loop is dropped and
    counted.
    """
    learned = patterns(source, min_seen=min_seen)
    graph = _Graph(project)
    accepted: List[Dict] = []
    dropped = collections.Counter()
    for batch in (from_dates(project, adjacent),
                  from_patterns(project, learned, keep_dates=keep_dates)):
        for tie in batch:
            key = (tie["pred"], tie["succ"])
            if key in graph.have:
                dropped["already tied"] += 1
                continue
            if graph.would_cycle(tie["pred"], tie["succ"]):
                dropped["would make a loop"] += 1
                continue
            graph.add(*key)
            accepted.append(tie)

    by_uid = {a.uid: a for a in project.activities}
    has_pred = {t["succ"] for t in accepted} | {
        r.successor_uid for r in project.relations}
    open_ends = [a for a in project.activities if a.uid not in has_pred]
    dropped["dates contradict the pattern"] = from_patterns.last_contradicted
    return {"ties": accepted, "dropped": {k: v for k, v in dropped.items() if v},
            "learned": len(learned),
            "counts": {
                "activities": len(project.activities),
                "before": len(project.relations),
                "adding": len(accepted),
                "from_dates": sum(1 for t in accepted if t["source"] == "dates"),
                "from_patterns": sum(1 for t in accepted if t["source"] == "pattern"),
                "after": len(project.relations) + len(accepted),
                "per_activity": round((len(project.relations) + len(accepted))
                                      / max(1, len(project.activities)), 2),
                "open_ends": len(open_ends)},
            "open_sample": [by_uid[a.uid].activity_id for a in open_ends[:40]]}


def apply(project, source, **kw) -> Dict[str, Any]:
    """Add the planned ties. Existing logic is never touched."""
    from .schedule_model import Relation
    result = plan(project, source, **kw)
    start = len(project.relations)
    for i, tie in enumerate(result["ties"], start=1):
        project.relations.append(Relation(
            uid=f"LT-{start + i:06d}", predecessor_uid=tie["pred"],
            successor_uid=tie["succ"], type=tie["type"], lag=tie["lag"]))
    project.build_lookups()
    return result


def retime_lags(project, only=None, uids=None, retype: bool = False) -> Dict[str, Any]:
    """
    Re-measure existing lags against the dates, on each successor's calendar.

    Giving a schedule the calendar it was really built on does not move a
    stored date, but it changes what a lag spans: the same number of hours now
    steps over a holiday the scheduler stops for. Lags measured on a plain
    Mon-Fri week are then all slightly short, and the next forward pass walks
    the successors forward past the dates the print asserts.

    So this re-measures the gap the dates already show and writes it back. It
    changes the lag on a relationship and never the relationship -- nothing is
    added, removed, or re-pointed, and a tie whose dates contradict its kind is
    left exactly as it was rather than bent to fit.

    `only` limits it to relations whose successor's activity_id starts with one
    of the given prefixes; `uids` to an explicit set of successor uids. Both
    left out, every relation is re-measured.

    `retype` additionally lets a tie the dates flatly contradict keep its
    dependency in the kind the dates do support — a successor that starts
    while its predecessor is still running is Start-to-Start here, whatever it
    is on the sister job. It is off by default because changing the kind of a
    relationship is a bigger claim than adjusting its gap, and it should be an
    explicit decision about logic one is entitled to change.
    """
    hols = _holidays_by_activity(project)
    by_uid = {a.uid: a for a in project.activities}
    changed, retyped, kept, skipped = [], [], 0, 0
    for r in project.relations:
        succ, pred = by_uid.get(r.successor_uid), by_uid.get(r.predecessor_uid)
        if succ is None or pred is None:
            skipped += 1
            continue
        sid = getattr(succ, "activity_id", "") or ""
        if only and not any(sid.startswith(x) for x in only):
            continue
        if uids is not None and succ.uid not in uids:
            continue
        lag = _implied_lag(pred, succ, r.type, hols.get(succ.uid, frozenset()))
        if lag is None and retype:
            # The dependency stands; only the kind was wrong for this job.
            for kind in (SS, FF, FS):
                if kind == r.type:
                    continue
                alt = _implied_lag(pred, succ, kind, hols.get(succ.uid, frozenset()))
                if alt is not None:
                    retyped.append({"activity_id": sid,
                                    "pred": getattr(pred, "activity_id", ""),
                                    "was": r.type, "now": kind,
                                    "lag": alt / HOURS_PER_DAY})
                    r.type, r.lag, lag = kind, alt, alt
                    break
        if lag is None:          # the dates do not support this kind; leave it
            skipped += 1
            continue
        if lag != r.lag:
            changed.append({"activity_id": sid, "pred": getattr(pred, "activity_id", ""),
                            "type": r.type, "was": r.lag / HOURS_PER_DAY,
                            "now": lag / HOURS_PER_DAY})
            r.lag = lag
        else:
            kept += 1
    project.build_lookups()
    return {"changed": changed, "retyped": retyped,
            "counts": {"changed": len(changed), "retyped": len(retyped),
                       "kept": kept, "left_alone": skipped}}


def verify(project, weekdays=frozenset({0, 1, 2, 3, 4})) -> Dict[str, Any]:
    """
    Reschedule a copy and measure how far the dates move.

    This is the acceptance test. Logic derived from a schedule's own dates
    should be consistent with them, so a forward pass ought to move almost
    nothing. Whatever moves is a tie that disagrees with the printed schedule —
    which is a finding, not necessarily an error, but it has to be looked at
    rather than shipped.
    """
    import copy

    from .schedule_model import compute_dates
    before = {a.activity_id: (_d(getattr(a, "planned_start", None)),
                              _d(getattr(a, "planned_finish", None)))
              for a in project.activities}
    probe = copy.deepcopy(project)
    compute_dates(probe, apply_dates=True)
    moved = []
    for a in probe.activities:
        was = before.get(a.activity_id)
        now = (_d(getattr(a, "planned_start", None)),
               _d(getattr(a, "planned_finish", None)))
        if not (was and was[1] and now[1]):
            continue
        shift = (now[1] - was[1]).days
        if shift:
            moved.append({"activity_id": a.activity_id, "name": a.name,
                          "was": was[1], "now": now[1], "days": shift})
    moved.sort(key=lambda m: -abs(m["days"]))
    total = len([a for a in project.activities
                 if _d(getattr(a, "planned_finish", None))])
    return {"moved": moved,
            "counts": {"compared": total, "moved": len(moved),
                       "unchanged": total - len(moved),
                       "worst_days": moved[0]["days"] if moved else 0}}


def describe(result: Dict[str, Any], check: Optional[Dict] = None,
             limit: int = 8) -> str:
    c = result["counts"]
    lines = [f"{c['activities']} activities, {c['before']} relations before.",
             f"adding {c['adding']}: {c['from_dates']} the dates already imply, "
             f"{c['from_patterns']} from the sister job's patterns.",
             f"{c['after']} relations after — {c['per_activity']} per activity.",
             f"{c['open_ends']} activities still without a predecessor."]
    if result["dropped"]:
        lines.append("dropped: " + ", ".join(f"{v} {k}"
                                             for k, v in result["dropped"].items()))
    if check:
        k = check["counts"]
        lines.append(f"\nrescheduled: {k['unchanged']} of {k['compared']} dates "
                     f"unchanged, {k['moved']} moved"
                     + (f", worst {k['worst_days']:+} days" if k["moved"] else ""))
        for m in check["moved"][:limit]:
            lines.append(f"   {m['activity_id']:26} {m['was']} -> {m['now']} "
                         f"({m['days']:+}d)")
    return "\n".join(lines)
