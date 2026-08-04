"""
logic_advisor.py — Recommend the logic a schedule is missing, judged against
the dates it already has.

The problem this solves: a schedule can carry a complete set of dates and
almost no logic. The dates then hold only because a hard constraint nails each
one down, so nothing drives the contractual milestones, float is meaningless,
and the whole schedule reads as critical. Replacing those constraints with real
relationships is the fix — but only if the relationships reproduce the dates
that are already there.

The measure that makes that possible is the IMPLIED LAG: the working days
between a candidate predecessor's finish and a candidate successor's start.
It turns "is this a sensible tie?" into something checkable against the
schedule as dated:

  implied lag ~= 0   the date already behaves as if the tie existed. Add it,
                     and the Start On constraint holding that date can go —
                     the date stops moving because logic now produces it.
  implied lag > 0    a real tie with genuine slack. Add it at lag 0 and let
                     the gap show as float; do not invent a lag to force the
                     date, which just re-creates the constraint in disguise.
  implied lag < 0    the successor starts before the predecessor finishes, so
                     the tie cannot be FS as dated. Either the date is
                     unsupportable, the relationship is really SS, or a
                     predecessor is missing.

Nothing here mutates the project. Every function returns recommendations for a
human to review, because a wrong tie in a schedule is more expensive than a
missing one.
"""

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

from .schedule_model import Activity, Project, WBSNode

# ── Verdicts ─────────────────────────────────────────────────────────────────
CONFIRMS = "confirms"     # tie reproduces the existing date
SLACK    = "slack"        # tie is valid, date has room
CONFLICT = "conflict"     # tie is impossible as currently dated

# A tie whose implied lag is within this many working days of zero is treated
# as explaining the date outright — a day or two of drift is rounding, not a
# real gap.
_CONFIRM_WINDOW = 2
# Beyond this the tie is nominal: technically ordered, but so far apart that
# calling it the driver would be misleading.
_WEAK_GAP = 44


def _parse(d) -> Optional[_dt.date]:
    if not d:
        return None
    try:
        return _dt.date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def _calendar_of(project: Project, act: Activity):
    cals = getattr(project, "calendars", None) or []
    for c in cals:
        if c.uid == getattr(act, "calendar_uid", None):
            return c
    return cals[0] if cals else None


def working_days_between(d1, d2, cal=None) -> Optional[int]:
    """
    Working days from d1 to d2 on the activity's calendar. Negative when d2
    precedes d1 — that sign is the whole point, so it is never discarded.
    """
    a, b = _parse(d1), _parse(d2)
    if a is None or b is None:
        return None
    wd = (getattr(cal, "work_days", None) if cal else None) or frozenset({0, 1, 2, 3, 4})
    hol = (getattr(cal, "holidays", None) if cal else None) or frozenset()
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if b >= a else (b, a)
    n = 0
    while lo < hi:
        lo += _dt.timedelta(days=1)
        if lo.weekday() in wd and lo.isoformat() not in hol:
            n += 1
    return sign * n


def implied_lag(project: Project, pred: Activity, succ: Activity) -> Optional[int]:
    """Working days between the predecessor's finish and the successor's start."""
    p_fin = pred.actual_finish or pred.planned_finish or pred.early_finish
    s_start = succ.actual_start or succ.planned_start or succ.early_start
    return working_days_between(p_fin, s_start, _calendar_of(project, succ))


def classify(lag: Optional[int]) -> Tuple[str, str]:
    """Turn an implied lag into a verdict plus the reason to show the user."""
    if lag is None:
        return SLACK, "One of the two has no date, so the tie can't be checked against the dates."
    if lag < 0:
        return CONFLICT, (
            f"Successor starts {-lag} working days BEFORE the predecessor finishes — "
            f"impossible as a Finish-to-Start. Either the date is unsupportable, "
            f"this is really a Start-to-Start overlap, or a different predecessor drives it.")
    if lag <= _CONFIRM_WINDOW:
        return CONFIRMS, (
            f"The dates already behave as if this tie existed ({lag}d gap). Adding it "
            f"reproduces the date, so the Start On constraint holding it can be removed.")
    if lag <= _WEAK_GAP:
        return SLACK, (
            f"Valid tie with {lag} working days of slack. Add at lag 0 and let the gap "
            f"show as float rather than inventing a lag to force the date.")
    return SLACK, (
        f"Ordered but {lag} working days apart — too far to call this the driver. "
        f"Something in between is probably the real predecessor.")


# ── WBS helpers ──────────────────────────────────────────────────────────────

def wbs_path(project: Project, act: Activity) -> str:
    by_uid = {w.uid: w for w in project.wbs_nodes}
    parts: List[str] = []
    cur = by_uid.get(act.wbs_uid)
    seen = set()
    while cur and cur.uid not in seen:
        seen.add(cur.uid)
        parts.insert(0, cur.name)
        cur = by_uid.get(cur.parent_uid)
    return " / ".join(parts)


def wbs_node_path(project: Project, node: WBSNode) -> str:
    """Full folder path of a WBS node itself (wbs_path takes an activity)."""
    by_uid = {w.uid: w for w in project.wbs_nodes}
    parts: List[str] = []
    cur, seen = node, set()
    while cur and cur.uid not in seen:
        seen.add(cur.uid)
        parts.insert(0, cur.name)
        cur = by_uid.get(cur.parent_uid)
    return " / ".join(parts)


def _descendants(project: Project, root_uid: str) -> set:
    out = {root_uid}
    grew = True
    while grew:
        grew = False
        for w in project.wbs_nodes:
            if w.parent_uid in out and w.uid not in out:
                out.add(w.uid)
                grew = True
    return out


def find_wbs(project: Project, needle: str) -> Optional[WBSNode]:
    """
    Folder lookup that understands a qualified name.

    Folder names repeat across phases — "MV Rooms" exists under Phase 1, 2 and
    3 — so a plain substring match returns whichever comes first, which is
    rarely the one meant. Matching every word of the request against the full
    folder PATH lets "Phase 1 MV Rooms" resolve to the right branch, while a
    bare "MV Rooms" still works when it is unambiguous.
    """
    if not needle:
        return None
    low = needle.strip().lower()
    for w in project.wbs_nodes:
        if (w.name or "").lower() == low or (w.code or "").lower() == low:
            return w

    # A phase qualifier is a hard filter, not a hint. Bare digits are dropped
    # from the word match because the project code itself ("25-1539-INT-1")
    # contains them, which otherwise makes every phase look like a match.
    want_phase = phase_number(needle)
    words = [t for t in re.split(r"[^a-z0-9]+", low) if t and not t.isdigit()]
    words = [t for t in words if t not in ("phase", "ph")]
    if not words and want_phase is None:
        return None

    best, best_score = None, 0
    for w in project.wbs_nodes:
        path = wbs_node_path(project, w).lower()
        if want_phase is not None:
            pn = phase_number(path)
            if pn != want_phase:
                continue
            if not words:                      # "Phase 2" on its own
                if phase_number(w.name or "") == want_phase:
                    return w
                continue
        hits = sum(1 for t in words if t in path)
        if hits == 0:
            continue
        # every word matched beats a partial match; among equals prefer the
        # folder whose own name carries the most of the request
        own = sum(1 for t in words if t in (w.name or "").lower())
        score = (hits * 100) + (own * 10) - min(len(path) // 20, 5)
        if score > best_score:
            best, best_score = w, score
    return best


def activities_in(project: Project, root_uid: str) -> List[Activity]:
    branch = _descendants(project, root_uid)
    return [a for a in project.activities if a.wbs_uid in branch]


# ── Phase resolution ─────────────────────────────────────────────────────────
# Milestones live in their own folder, so the work that drives them sits in a
# different branch entirely. "(PH2)" in a milestone name is the only link back
# to "Phase 2 (Build-Out)", so that mapping has to be made explicitly.

_PHASE_RE = re.compile(r"\(?\bPH\s*([0-9]+)\b\)?|\bPhase\s*([0-9]+)\b", re.I)


def phase_number(text: str) -> Optional[int]:
    m = _PHASE_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _phase_scope(project: Project, milestone: Activity) -> Tuple[Optional[str], str]:
    """
    The WBS branch whose work should drive this milestone, as (uid, label).
    Falls back to the whole project when the milestone names no phase.
    """
    n = phase_number(milestone.name) or phase_number(wbs_path(project, milestone))
    if n is not None:
        # Milestones are usually filed under their own "Phase N" sub-folder, so
        # a plain name match finds the milestone folder rather than the work.
        # Score candidates by how much real (non-milestone) work they hold and
        # take the richest — that is the branch whose progress drives the date.
        ms_types = ("Start Milestone", "Finish Milestone")
        best, best_work = None, -1
        for w in project.wbs_nodes:
            nm = (w.name or "").lower()
            if f"phase {n}" not in nm and f"ph{n}" not in nm.replace(" ", ""):
                continue
            if "milestone" in wbs_node_path(project, w).lower():
                continue
            work = sum(1 for a in activities_in(project, w.uid)
                       if a.activity_type not in ms_types)
            if work > best_work:
                best, best_work = w, work
        if best is not None and best_work > 0:
            return best.uid, best.name
    # Building-wide milestones are driven by the shell, not a build-out phase
    for w in project.wbs_nodes:
        if "core & shell" in (w.name or "").lower():
            return w.uid, w.name
    return None, "whole project"


# ── Commissioning ladder ─────────────────────────────────────────────────────
# Level 1-5 commissioning is a fixed ladder, and within a phase each level's
# start precedes its own finish. Levels overlap in practice (L4 often starts on
# early systems while L3 finishes on later ones), so the ladder is proposed and
# then checked against the dates rather than assumed.

_CX_RE = re.compile(r"level\s*([1-5])\s*commissioning\s*(start|finish)", re.I)


def _cx_rank(name: str) -> Optional[Tuple[int, int]]:
    m = _CX_RE.search(name or "")
    if not m:
        return None
    return int(m.group(1)), (0 if m.group(2).lower() == "start" else 1)


def _has_link(project: Project, a_uid: str, b_uid: str) -> bool:
    return any(r.predecessor_uid == a_uid and r.successor_uid == b_uid
               for r in project.relations)


def _rec(project: Project, pred: Activity, succ: Activity, rel_type: str,
         rationale: str) -> Dict[str, Any]:
    lag = implied_lag(project, pred, succ)
    verdict, explanation = classify(lag)
    # A hard pin on the successor is what this tie replaces. Only a tie that
    # reproduces the date can retire one — otherwise removing it would let the
    # date move.
    ct = (succ.constraint_type or "").strip().lower()
    drops = bool(ct in ("start on", "must start on", "finish on", "must finish on")
                 and verdict == CONFIRMS)
    # A milestone carries a contractual date. Once logic drives it, the date
    # should become a DEADLINE rather than a pin: the milestone shows its real
    # early date, and any slip past the contract date appears as negative float
    # instead of being hidden by a constraint that forces the date.
    is_ms = succ.activity_type in ("Start Milestone", "Finish Milestone")
    deadline = None
    if is_ms and verdict != CONFLICT:
        ms_date = str(succ.planned_finish or succ.planned_start or "")[:10]
        if ms_date:
            deadline = {
                "activity_id": succ.activity_id,
                "constraint_type": ("Finish On Or Before"
                                    if succ.activity_type == "Finish Milestone"
                                    else "Start On Or Before"),
                "constraint_date": ms_date,
                "why": ("Keeps the contractual date as a deadline once logic drives "
                        "the milestone. The milestone will show its computed early "
                        "date; if the work slips past this date it becomes negative "
                        "float rather than being silently held."),
            }
    return {
        "predecessor_id": pred.activity_id,
        "predecessor_name": pred.name,
        "predecessor_finish": str(pred.planned_finish or "")[:10],
        "successor_id": succ.activity_id,
        "successor_name": succ.name,
        "successor_start": str(succ.planned_start or "")[:10],
        "type": rel_type,
        "lag_days": 0,
        "implied_lag_days": lag,
        "verdict": verdict,
        "rationale": rationale,
        "date_check": explanation,
        "removes_constraint": drops,
        "deadline": deadline,
        "constraint_on_successor": succ.constraint_type or None,
        "wbs_path": wbs_path(project, pred),
    }


def commissioning_ladder(project: Project) -> List[Dict[str, Any]]:
    """
    Tie each phase's commissioning milestones into their proper order.

    These are contractual dates with, typically, no logic at all behind them —
    so the ladder is where milestone anchoring pays off first.
    """
    out: List[Dict[str, Any]] = []
    by_phase: Dict[Optional[int], List[Activity]] = {}
    for a in project.activities:
        if _cx_rank(a.name) is None:
            continue
        by_phase.setdefault(phase_number(a.name) or phase_number(wbs_path(project, a)),
                            []).append(a)

    for phase, acts in sorted(by_phase.items(), key=lambda kv: (kv[0] is None, kv[0])):
        acts.sort(key=lambda a: (_cx_rank(a.name), str(a.planned_start or "")))
        for pred, succ in zip(acts, acts[1:]):
            if _has_link(project, pred.uid, succ.uid):
                continue
            lvl_p, kind_p = _cx_rank(pred.name)
            lvl_s, kind_s = _cx_rank(succ.name)
            if lvl_p == lvl_s:
                why = (f"Level {lvl_p} commissioning cannot finish before it starts"
                       if kind_s else "")
            else:
                why = (f"Level {lvl_s} commissioning follows Level {lvl_p} — "
                       f"systems must pass the lower level before the next begins")
            out.append(_rec(project, pred, succ, "Finish to Start",
                            why or f"Commissioning ladder for Phase {phase}"))
    return out


# ── Milestone drivers ────────────────────────────────────────────────────────

def _open_ended(project: Project) -> Tuple[set, set]:
    has_succ = {r.predecessor_uid for r in project.relations}
    has_pred = {r.successor_uid for r in project.relations}
    return has_pred, has_succ


def milestone_drivers(project: Project, milestone: Activity,
                      limit: int = 3) -> List[Dict[str, Any]]:
    """
    What should drive this milestone's date.

    Candidates are the work inside the milestone's own phase that finishes at
    or before the milestone, ranked by how close the implied lag sits to zero —
    i.e. by how completely the candidate already explains the date. Work that
    is itself dangling is preferred as a tie-break, since anchoring it serves
    the milestone and closes an open end at the same time.
    """
    ms_date = _parse(milestone.planned_start or milestone.planned_finish)
    if ms_date is None:
        return []
    scope_uid, scope_label = _phase_scope(project, milestone)
    pool = (activities_in(project, scope_uid) if scope_uid else list(project.activities))
    _, has_succ = _open_ended(project)

    scored = []
    for a in pool:
        if a.uid == milestone.uid or a.activity_type in ("Start Milestone", "Finish Milestone"):
            continue
        fin = _parse(a.planned_finish or a.early_finish)
        if fin is None or fin > ms_date:
            continue
        lag = implied_lag(project, a, milestone)
        if lag is None:
            continue
        scored.append((abs(lag), 0 if a.uid not in has_succ else 1,
                       -(a.planned_duration or 0), a, lag))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    out = []
    for _abs, _open, _dur, a, lag in scored[:limit]:
        rationale = (f"Latest work in {scope_label} that lands on this milestone's date"
                     if _abs <= _CONFIRM_WINDOW else
                     f"Nearest finishing work in {scope_label}")
        if _open == 0:
            rationale += "; it currently has no successor, so this closes an open end too"
        out.append(_rec(project, a, milestone, "Finish to Start", rationale))
    return out


def milestone_report(project: Project, limit_per_milestone: int = 3) -> Dict[str, Any]:
    """
    Every milestone, its logic state, and what could drive it.

    Milestones are the first place to attack a date-only schedule: they carry
    the contractual dates, there are few of them, and anchoring them proves the
    whole approach before it is turned loose on thousands of activities.
    """
    has_pred, has_succ = _open_ended(project)
    milestones = [a for a in project.activities
                  if a.activity_type in ("Start Milestone", "Finish Milestone")]
    milestones.sort(key=lambda a: str(a.planned_start or a.planned_finish or ""))

    items = []
    for m in milestones:
        items.append({
            "activity_id": m.activity_id,
            "name": m.name,
            "date": str(m.planned_start or m.planned_finish or "")[:10],
            "type": m.activity_type,
            "wbs_path": wbs_path(project, m),
            "has_predecessor": m.uid in has_pred,
            "has_successor": m.uid in has_succ,
            "constraint": m.constraint_type or None,
            "drivers": milestone_drivers(project, m, limit=limit_per_milestone),
        })

    ladder = commissioning_ladder(project)
    unanchored = [i for i in items if not i["has_predecessor"]]
    return {
        "milestone_count": len(items),
        "unanchored_count": len(unanchored),
        "milestones": items,
        "commissioning_ladder": ladder,
        "summary": {
            "confirms": sum(1 for i in items for d in i["drivers"] if d["verdict"] == CONFIRMS)
                        + sum(1 for d in ladder if d["verdict"] == CONFIRMS),
            "slack":    sum(1 for i in items for d in i["drivers"] if d["verdict"] == SLACK)
                        + sum(1 for d in ladder if d["verdict"] == SLACK),
            "conflict": sum(1 for i in items for d in i["drivers"] if d["verdict"] == CONFLICT)
                        + sum(1 for d in ladder if d["verdict"] == CONFLICT),
        },
    }


def to_commands(recs: List[Dict[str, Any]], include_conflicts: bool = False,
                drop_constraints: bool = True,
                keep_milestone_deadlines: bool = True) -> List[Dict[str, Any]]:
    """
    Turn accepted recommendations into edit commands.

    A tie that reproduces the date is paired with clearing the constraint that
    was holding it — that is the point of the exercise, and doing it in the
    same batch keeps the date from being held twice.
    """
    cmds: List[Dict[str, Any]] = []
    for r in recs:
        if r.get("verdict") == CONFLICT and not include_conflicts:
            continue
        cmds.append({
            "action": "add_relation",
            "predecessor_id": r["predecessor_id"],
            "successor_id": r["successor_id"],
            "type": {"Finish to Start": "fs", "Start to Start": "ss",
                     "Finish to Finish": "ff", "Start to Finish": "sf"}.get(r.get("type"), "fs"),
            "lag_days": r.get("lag_days", 0),
        })
        if drop_constraints and r.get("removes_constraint"):
            cmds.append({"action": "clear_constraint", "activity_id": r["successor_id"]})
        d = r.get("deadline")
        if keep_milestone_deadlines and d:
            cmds.append({"action": "set_constraint",
                         "activity_id": d["activity_id"],
                         "constraint_type": d["constraint_type"],
                         "constraint_date": d["constraint_date"]})
    return cmds


# ── Area navigation ──────────────────────────────────────────────────────────

def area_digest(project: Project, needle: str, sample: int = 8) -> Dict[str, Any]:
    """
    A compact picture of one branch, for answering "what's in Phase 2 MV Rooms?"
    without loading the whole schedule.

    The full context for a large project runs to tens of thousands of tokens,
    which forces shallow reasoning over everything instead of real reasoning
    over the part that was asked about. This returns only the named branch.
    """
    node = find_wbs(project, needle)
    if node is None:
        near = [w.name for w in project.wbs_nodes
                if needle.lower().split()[0] in (w.name or "").lower()][:8]
        return {"error": f"No folder matching '{needle}'", "did_you_mean": near}

    acts = activities_in(project, node.uid)
    has_pred, has_succ = _open_ended(project)
    kids = [w for w in project.wbs_nodes if w.parent_uid == node.uid]
    kids.sort(key=lambda w: (w.sequence_num, w.name))

    starts = sorted(str(a.planned_start)[:10] for a in acts if a.planned_start)
    fins = sorted(str(a.planned_finish)[:10] for a in acts if a.planned_finish)
    unlinked = [a for a in acts if a.uid not in has_pred and a.uid not in has_succ]

    return {
        "name": node.name,
        "code": node.code,
        "path": wbs_node_path(project, node),
        "activity_count": len(acts),
        "sub_folders": [
            {"name": k.name, "code": k.code,
             "activity_count": len(activities_in(project, k.uid))} for k in kids],
        "date_range": {"earliest_start": starts[0] if starts else None,
                       "latest_finish": fins[-1] if fins else None},
        "logic": {
            "fully_unlinked": len(unlinked),
            "missing_predecessor": sum(1 for a in acts if a.uid not in has_pred),
            "missing_successor": sum(1 for a in acts if a.uid not in has_succ),
        },
        "constrained": sum(1 for a in acts if a.constraint_type),
        "activities": [
            {"activity_id": a.activity_id, "name": a.name,
             "start": str(a.planned_start or "")[:10],
             "finish": str(a.planned_finish or "")[:10],
             "duration_days": round((a.planned_duration or 0) / 8.0, 1),
             "status": a.status,
             "linked": a.uid in has_pred or a.uid in has_succ,
             "constraint": a.constraint_type or None}
            for a in sorted(acts, key=lambda x: str(x.planned_start or ""))[:sample]
        ],
        "activities_shown": min(sample, len(acts)),
    }


# ── Within-area trade sequencing ─────────────────────────────────────────────

def sequence_recommendations(project: Project, needle: str,
                             max_recs: int = 60) -> List[Dict[str, Any]]:
    """
    Chain the unlinked work inside each room/area of a branch, in date order.

    Within one room the dates already encode the trade sequence the planner
    intended — rough-in before equipment set before terminations. Turning that
    ordering into relationships is what makes the room behave as a sequence
    instead of a pile of separately-pinned dates. Each link is still checked
    against the dates, so an overlap is reported rather than forced into FS.
    """
    node = find_wbs(project, needle)
    if node is None:
        return []
    has_pred, has_succ = _open_ended(project)

    # group by the LOWEST folder each activity sits in — that is the room
    rooms: Dict[str, List[Activity]] = {}
    for a in activities_in(project, node.uid):
        rooms.setdefault(a.wbs_uid, []).append(a)

    out: List[Dict[str, Any]] = []
    for wbs_uid, acts in rooms.items():
        dated = [a for a in acts if a.planned_start and a.status != "Completed"]
        if len(dated) < 2:
            continue
        dated.sort(key=lambda a: (str(a.planned_start)[:10],
                                  str(a.planned_finish or "")[:10], a.activity_id))
        for pred, succ in zip(dated, dated[1:]):
            if _has_link(project, pred.uid, succ.uid):
                continue
            # only propose where logic is actually missing
            if succ.uid in has_pred and pred.uid in has_succ:
                continue
            out.append(_rec(project, pred, succ, "Finish to Start",
                            f"Next in the dated trade sequence within "
                            f"{wbs_node_path(project, project.get_wbs(wbs_uid)).split(' / ')[-1]}"))
            if len(out) >= max_recs:
                return out
    return out


# ── Procurement / long-lead coupling ─────────────────────────────────────────
# Equipment cannot be installed before it arrives. Each entry maps the words a
# procurement line uses to the words the installation activities use.

EQUIPMENT_TERMS: List[Tuple[str, Tuple[str, ...]]] = [
    ("generator",      ("generator", "gen ")),
    ("switchgear",     ("switchgear", "swbd", "swgr", "msg", "mvs")),
    ("transformer",    ("transformer", "xfmr")),
    ("chiller",        ("chiller",)),
    ("cooling tower",  ("cooling tower",)),
    ("dry cooler",     ("dry cooler",)),
    ("ups",            ("ups",)),
    ("pdu",            ("pdu",)),
    ("rmu",            ("rmu",)),
    ("gis",            ("gis",)),
    ("crah",           ("crah",)),
    ("fcw",            ("fcw", "fcu")),
    ("busway",         ("busway", "bus duct")),
    ("skid",           ("skid",)),
    ("panel",          ("panelboard", "panel board")),
]

_INSTALL_VERBS = ("set ", "install", "rig", "hang", "place", "erect", "mount",
                  "terminate", "energize", "final connection")

# Foundations, bases and rough-in legitimately precede delivery — flagging them
# as "installed before it arrived" would be noise, not a finding.
_PRE_DELIVERY_OK = ("base", "pad", "foundation", "rough-in", "rough in", "layout",
                    "hanger", "support", "steel", "housekeeping", "curb", "isolator")


def _equipment_of(name: str) -> List[str]:
    low = (name or "").lower()
    return [key for key, words in EQUIPMENT_TERMS if any(w in low for w in words)]


def _is_install(name: str) -> bool:
    low = (name or "").lower()
    return any(v in low for v in _INSTALL_VERBS)


def procurement_report(project: Project, needle: Optional[str] = None,
                       max_items: int = 40) -> Dict[str, Any]:
    """
    Match long-lead procurement to the work it feeds, and flag anything dated
    to be installed before it can arrive.

    Delivery-before-install is not a tie to force — it is a finding. When an
    installation is dated ahead of its equipment, either the procurement dates
    are wrong or the installation cannot happen as planned, and a scheduler
    needs to see that rather than have a relationship quietly paper over it.
    """
    scope = find_wbs(project, needle) if needle else None
    pool = activities_in(project, scope.uid) if scope else list(project.activities)

    # procurement lines live under a procurement/LLE folder
    def _is_procurement(a: Activity) -> bool:
        p = wbs_path(project, a).lower()
        return ("lle" in p or "procurement" in p or "long lead" in p
                or "submittal" in p)

    supplies = [a for a in project.activities
                if _is_procurement(a) and _equipment_of(a.name)]
    installs = [a for a in pool
                if not _is_procurement(a) and _is_install(a.name)
                and _equipment_of(a.name)]

    items: List[Dict[str, Any]] = []
    conflicts = 0
    for s in supplies:
        kinds = set(_equipment_of(s.name))
        fed = [i for i in installs if kinds & set(_equipment_of(i.name))]
        if not fed:
            continue
        fed.sort(key=lambda a: str(a.planned_start or ""))
        first = fed[0]
        lag = implied_lag(project, s, first)
        verdict, why = classify(lag)
        early = bool(lag is not None and lag < 0
                     and not any(w in (first.name or "").lower()
                                 for w in _PRE_DELIVERY_OK))
        if early:
            conflicts += 1
        items.append({
            "supply_id": s.activity_id,
            "supply_name": s.name,
            "supply_finish": str(s.planned_finish or "")[:10],
            "equipment": sorted(kinds),
            "installs_fed": len(fed),
            "first_install_id": first.activity_id,
            "first_install_name": first.name,
            "first_install_start": str(first.planned_start or "")[:10],
            "implied_lag_days": lag,
            "verdict": verdict,
            "installed_before_delivery": early,
            "note": (
                f"{first.name} is dated {-lag} working days before "
                f"{s.name} is due to arrive — either the procurement dates are "
                f"wrong or this work cannot proceed as scheduled."
                if early else why),
        })
        if len(items) >= max_items:
            break

    items.sort(key=lambda i: (not i["installed_before_delivery"],
                              i["implied_lag_days"] if i["implied_lag_days"] is not None else 0))
    return {
        "scope": scope.name if scope else "whole project",
        "supply_lines": len(supplies),
        "matched": len(items),
        "installed_before_delivery": conflicts,
        "items": items,
    }


def area_report(project: Project, needle: str) -> Dict[str, Any]:
    """Everything the agent needs to reason about one area, in one call."""
    digest = area_digest(project, needle)
    if "error" in digest:
        return digest
    seq = sequence_recommendations(project, needle)
    proc = procurement_report(project, needle)
    return {
        "area": digest,
        "sequence_recommendations": seq,
        "procurement": proc,
        "summary": {
            "sequence_ties_proposed": len(seq),
            "confirms": sum(1 for r in seq if r["verdict"] == CONFIRMS),
            "conflicts": sum(1 for r in seq if r["verdict"] == CONFLICT),
            "installed_before_delivery": proc.get("installed_before_delivery", 0),
        },
    }
