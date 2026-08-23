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

from . import project_brain as _brain
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
    """
    Working days of GAP between the predecessor finishing and the successor
    starting — the lag a Finish-to-Start tie would need to reproduce the dates.

    The predecessor's finish day is worked, so a successor starting the next
    working day is back-to-back and reads as zero. Anything more is real slack.
    """
    p_fin = pred.actual_finish or pred.planned_finish or pred.early_finish
    s_start = succ.actual_start or succ.planned_start or succ.early_start
    raw = working_days_between(p_fin, s_start, _calendar_of(project, succ))
    return None if raw is None else raw - 1


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


# ── Candidate scoring ────────────────────────────────────────────────────────
# Ranking purely by "whose finish is nearest the milestone" is what produced
# ties like "Complete Construction <- Final Floor Finishes, implied lag 55d":
# nothing better was in scope, so the least-bad date won and got offered as a
# driver. A date 55 working days off is not a driver, it is a coincidence.
#
# A tie is judged on several independent signals instead, each of which a
# scheduler would actually check:
#
#   date fit     does the gap look like a handoff, or like unrelated work
#   subject      do the two activities talk about the same thing at all
#   area         same room / level / area / unit — or demonstrably different
#   WBS distance how far apart they sit in the breakdown
#   trade order  does the predecessor's trade run before the successor's
#   terminal     a turnover / complete / ready activity anchors a milestone
#   procurement  an install cannot precede its own delivery
#
# They are summed, so agreement between weak signals can carry a tie and one
# strong contradiction (wrong room, reversed trade) can sink it. Anything that
# does not clear _MIN_CONFIDENCE is reported as "no confident driver" rather
# than offered — saying nothing beats suggesting a wrong tie.

_MIN_CONFIDENCE = 0.30

# Beyond this the pair cannot be a handoff, and skipping it keeps the
# day-by-day working-day count off the hot path entirely.
_MAX_GAP_DAYS = 120

_STOP = frozenset("""
a an and or of the to for at in on by with from into onto per is be
phase ph level lvl area zone room rm unit no num complete completed completion
start starts starting finish finishes finishing begin end
""".split())

_AREA_RE = re.compile(
    r"""(?ix)
    \b(?:
        (?P<eq>MV|LV|UPS|CRAH|CDU|GEN|PDU|RMU|WCC|CWP|PCHWP|SCHWP|CUP|UP)\s*-?\s*(?P<eqn>\d{1,4})
      | (?:level|lvl|l)\s*(?P<lvl>\d{1,2})\b
      | (?:area|zone|grid\s*line)\s*(?P<area>[\w.-]{1,8})
      | (?:phase|ph)\s*(?P<ph>\d{1,2})\b
    )
    """)


def _tokens(text: str) -> frozenset:
    """Significant words in a name, for judging whether two rows share a subject."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOP)


# Any "<short word> <number>" in a name — the shape every job uses to name a
# place, whatever the words are. Read on its own this is far too eager ("400 A",
# "Rev 2", "3 MCM"), so it is only ever used against families the project has
# been shown to actually number: see learn_area_families().
_NUMBERED_RE = re.compile(r"(?i)\b([a-z]{1,6})\s*[-#]?\s*(\d{1,4})\b")

# Measurements, revisions and counts get numbered too, and none of them name a
# place. Two rows pulling different wire sizes are not in different rooms, so
# letting "MCM 500" become an area would penalise a perfectly good handoff.
# The last line is places _AREA_RE already reads in its own format — learning
# them again would tag the same room twice under two names.
_NOT_A_PLACE = frozenset("""
mcm awg kv kva kw mw va amp amps a v hz ton tons cfm gpm psi
in ft mm cm m sf sy cy lf ea qty no num rev revision sht dwg
type size ga gauge sch lb lbs kg hr hrs wk wks day days
ph phase l lvl level area zone
""".split())

# English words that are only a place when they are plainly meant as one.
# "OR 3" is an operating room on a hospital job; "2 or 3 crews" is a
# conjunction, and reading it as a room would invent places on every job that
# writes a range. Capitalisation is what separates them, so these are learned
# ONLY from an occurrence that was actually written as a code.
#
# Deliberately NOT the general stopword list: that one exists for judging
# shared subject and holds "room", "rm" and "unit" — the most ordinary
# place-words there are, which a job is perfectly entitled to number.
_FUNCTION_WORDS = frozenset("""
a an and or of the to for at in on by with from into onto per is be as it
""".split())


def learn_area_families(project) -> frozenset:
    """
    The words THIS job uses to name places, learned from its own naming.

    The built-in list below knows the prefixes this trade uses — MV, UPS,
    CRAH. That is useful on a data centre and useless on a hospital of OR and
    ICU rooms, a warehouse of DOCK and BAY, a hotel of numbered floors. Rather
    than growing the list per sector, the families are read out of the project:
    a word that appears in front of SEVERAL DIFFERENT numbers is how that job
    names its places.

    Repetition is what makes this safe. "Pull 3 #400" numbers nothing twice, so
    it never becomes a family; "OR 3", "OR 4", "OR 7" plainly does.
    """
    seen: Dict[str, set] = {}
    written_as_code: set = set()
    texts = []
    for a in (getattr(project, "activities", None) or []):
        texts.append(a.name or "")
    for w in (getattr(project, "wbs_nodes", None) or []):
        texts.append(w.name or "")
    for text in texts:
        for m in _NUMBERED_RE.finditer(text):
            raw = m.group(1)
            word = raw.lower()
            if word in _NOT_A_PLACE:
                continue
            if raw.isupper():
                written_as_code.add(word)
            seen.setdefault(word, set()).add(int(m.group(2)))
    return frozenset(
        w for w, nums in seen.items()
        if len(nums) >= 2 and (w not in _FUNCTION_WORDS or w in written_as_code))


def _area_tags(text: str, families: Optional[frozenset] = None) -> frozenset:
    """
    Room / level / area / phase identifiers in a name.

    "MV 105", "Level 3", "Area 7", "CUP-01", "PH2" all name a place. Two rows
    that name DIFFERENT places are almost never a handoff, however close their
    dates — that is how work in MV 105 gets tied to work in MV 109.

    `families` are the place-words this particular job uses, from
    learn_area_families(). Passing them is what makes a job whose rooms are
    called OR, DOCK or POD read as well as one whose rooms are called MV.
    """
    out = set()
    for m in _AREA_RE.finditer(text or ""):
        if m.group("eq"):
            out.add(f"{m.group('eq').lower()}{int(m.group('eqn'))}")
        elif m.group("lvl"):
            out.add(f"lvl{int(m.group('lvl'))}")
        elif m.group("area"):
            out.add(f"area{m.group('area').lower()}")
        elif m.group("ph"):
            out.add(f"ph{int(m.group('ph'))}")
    if families:
        for m in _NUMBERED_RE.finditer(text or ""):
            word = m.group(1).lower()
            if word in families:
                out.add(f"{word}{int(m.group(2))}")
    return frozenset(out)


# Rough order in which construction trades run. Only relative order matters,
# and only when both rows classify — an unknown trade simply scores neutral.
_TRADE_SEQUENCE: List[Tuple[int, Tuple[str, ...]]] = [
    (10, ("design", "engineer", "ifc", "shop drawing", "submittal", "permit",
          "review", "approval", "procure", "award", "buy-out", "buyout",
          "fabricat", "deliver", "lead time", "order")),
    (20, ("mobiliz", "clear", "grub", "demo", "erosion", "e&s", "survey", "layout")),
    (30, ("excavat", "blast", "grade", "grading", "undercut", "backfill",
          "underground", "utilit", "duct bank", "ductbank")),
    (40, ("footing", "pier", "caisson", "deep foundation", "grade beam",
          "foundation", "pile", "pad", "slab on grade")),
    (50, ("precast", "steel", "erect", "structure", "superstructure", "deck",
          "topping", "tilt")),
    (60, ("roof", "envelope", "curtain wall", "cladding", "glazing",
          "waterproof", "skin")),
    (70, ("rough-in", "rough in", "overhead", "conduit", "raceway", "hanger",
          "sleeve", "in-wall", "in wall")),
    (75, ("rigging", "rig ", "set ", "install", "mount", "place equipment",
          "equipment set")),
    (80, ("pull", "terminat", "wire", "cable", "splice", "bus", "feeder",
          "pipe", "duct", "insulat")),
    (85, ("framing", "drywall", "close in", "close-in", "tape", "mud",
          "prime", "paint", "ceiling", "floor finish", "flooring", "trim")),
    (90, ("qa/qc", "qaqc", "inspection", "checklist", "pre-functional",
          "prefunctional", "point to point", "megger", "test")),
    (95, ("energiz", "start-up", "start up", "startup", "commission", "cx",
          "functional", "integrated", "ist", "tab", "balanc")),
    # Project closeout only. "Turnover" deliberately does NOT belong here: an
    # area turnover is the end of THAT area's work, not the end of the job, and
    # ranking it as closeout made "Precast Area 7 Turnover" read as running
    # backwards against precast erection.
    (99, ("punch", "substantial completion", "tco", "occupancy",
          "closeout", "close-out", "final completion", "training", "o&m")),
]


def _trade_rank(name: str) -> Optional[int]:
    """Where this activity's work sits in the trade sequence, if recognisable."""
    low = (name or "").lower()
    best = None
    for rank, pats in _TRADE_SEQUENCE:
        for p in pats:
            if p in low:
                # last match wins: "Install and Test" is gated by the test
                best = rank if best is None else max(best, rank)
                break
    return best


_TERMINAL_RE = re.compile(
    r"(?i)\b(turnover|turn\s*over|complete|completion|ready|accepted|approved|"
    r"received|issued|released|finish|energiz\w*|substantial)\b")


def _is_terminal(name: str) -> bool:
    """Does this activity read as the END of a body of work?"""
    return bool(_TERMINAL_RE.search(name or ""))


def _wbs_chain(project: Project, act: Activity) -> List[str]:
    by_uid = {w.uid: w for w in project.wbs_nodes}
    chain, cur, guard = [], act.wbs_uid, 0
    while cur and guard < 60:
        chain.append(cur)
        node = by_uid.get(cur)
        cur = node.parent_uid if node else None
        guard += 1
    return chain


def _wbs_closeness(chain_a: List[str], chain_b: List[str]) -> int:
    """
    12 same folder, 8 one hop apart, 5 two hops, 0 distant.
    Distance is how far up either chain you must walk to meet.
    """
    if not chain_a or not chain_b:
        return 0
    if chain_a[0] == chain_b[0]:
        return 12
    pos_b = {uid: i for i, uid in enumerate(chain_b)}
    for i, uid in enumerate(chain_a):
        if uid in pos_b:
            hops = i + pos_b[uid]
            return 8 if hops <= 2 else (5 if hops <= 4 else 0)
    return 0


class _Ctx:
    """Per-call caches so scoring stays cheap over a big pool."""

    def __init__(self, project: Project, directives: Optional[List[Any]] = None,
                 feedback: Optional[Dict[str, Any]] = None):
        self.project = project
        # How proposals of each shape have been received on this job. Optional
        # everywhere — a project nobody has clicked through ranks exactly as
        # it did before.
        self.feedback: Dict[str, Any] = feedback or {}
        self._tok: Dict[str, frozenset] = {}
        self._area: Dict[str, frozenset] = {}
        self._chain: Dict[str, List[str]] = {}
        self._trade: Dict[str, Optional[int]] = {}
        self._where: Dict[str, str] = {}
        has_pred, has_succ = _open_ended(project)
        self.has_succ = has_succ
        # What the user has said about how THIS job is built. Empty for every
        # project that has not been told anything, which is the default — the
        # ranking below is unchanged when there is nothing to apply.
        self.directives: List[Any] = [d for d in (directives or [])
                                      if getattr(d, "enabled", True)]
        # How THIS job names its places, read from the job itself — so a
        # hospital of OR/ICU rooms or a warehouse of DOCK/BAY reads as well as
        # a data centre of MV/UPS, with nothing configured.
        self.families = learn_area_families(project)

    def tok(self, a: Activity) -> frozenset:
        if a.uid not in self._tok:
            self._tok[a.uid] = _tokens(a.name)
        return self._tok[a.uid]

    def area(self, a: Activity) -> frozenset:
        if a.uid not in self._area:
            self._area[a.uid] = (_area_tags(a.name, self.families)
                                 or _area_tags(wbs_path(self.project, a),
                                               self.families))
        return self._area[a.uid]

    def chain(self, a: Activity) -> List[str]:
        if a.uid not in self._chain:
            self._chain[a.uid] = _wbs_chain(self.project, a)
        return self._chain[a.uid]

    def trade(self, a: Activity) -> Optional[int]:
        if a.uid not in self._trade:
            self._trade[a.uid] = _trade_rank(a.name)
        return self._trade[a.uid]

    def where(self, a: Activity) -> str:
        """Folder path minus the root, for judging directives — the room is
        usually in the folder, not the activity name."""
        if a.uid not in self._where:
            self._where[a.uid] = _brain.where_of(self.project, a)
        return self._where[a.uid]


def score_tie(ctx: _Ctx, pred: Activity, succ: Activity,
              lag: Optional[int], scope_latest: bool = False) -> Tuple[float, List[str]]:
    """
    How much this pair looks like a real handoff, 0.0 - 1.0, plus the
    human-readable reasons behind the number.
    """
    score, why = 0.0, []

    # 1. Date fit is a GATE, not a contribution.
    #
    # Added to the other signals it could be outvoted, which is how a 78-day
    # gap still scored 0.53 — enough agreement elsewhere to look confident
    # about work three months apart. It multiplies instead: a tie needs BOTH a
    # plausible relationship and a plausible gap, and neither alone is enough.
    if lag is None:
        date_factor = 0.15
    elif lag < 0:
        date_factor = 0.05
        why.append(f"successor starts {-lag}d before it finishes")
    elif lag <= 2:
        date_factor = 1.00; why.append(f"dates already behave as this tie ({lag}d gap)")
    elif lag <= 5:
        date_factor = 0.92; why.append(f"tight {lag}d gap")
    elif lag <= 10:
        date_factor = 0.80; why.append(f"{lag}d gap")
    elif lag <= 22:
        date_factor = 0.55; why.append(f"{lag}d gap — loose")
    elif lag <= 44:
        date_factor = 0.30; why.append(f"{lag}d gap — too far to be the driver")
    else:
        date_factor = 0.10; why.append(f"{lag}d apart — unrelated work")

    # 2. subject overlap
    ta, tb = ctx.tok(pred), ctx.tok(succ)
    if ta and tb:
        shared = ta & tb
        j = len(shared) / len(ta | tb)
        if shared:
            score += 25 * min(1.0, j * 2.2)
            why.append("same subject: " + ", ".join(sorted(shared)[:3]))
        # A milestone's name IS its subject, and it is short: "Finish Precast"
        # is one word after stopwords. When every word of one name appears in
        # the other, they are about the same work however the jaccard reads
        # against a long descriptive name like "Precast Area 7 Turnover".
        if shared and (tb <= ta or ta <= tb):
            score += 12; why.append("one name is entirely about the other's subject")

    # 3. area — a specific place is strong evidence; a mismatch sinks the tie.
    #    A shared PHASE is much weaker: half the schedule is in PH1.
    aa, ab = ctx.area(pred), ctx.area(succ)
    if aa and ab:
        common = aa & ab
        specific = {t for t in common if not t.startswith("ph")}
        if specific:
            score += 20; why.append("same area: " + ", ".join(sorted(specific)[:2]))
        elif common:
            score += 6;  why.append("same phase")
        else:
            spec_a = {t for t in aa if not t.startswith("ph")}
            spec_b = {t for t in ab if not t.startswith("ph")}
            if spec_a and spec_b:
                score -= 18
                why.append(f"different areas ({','.join(sorted(spec_a)[:2])} "
                           f"vs {','.join(sorted(spec_b)[:2])})")

    # 4. WBS proximity
    near = _wbs_closeness(ctx.chain(pred), ctx.chain(succ))
    if near:
        score += near
        why.append("same folder" if near == 12 else "nearby in the WBS")

    # 5. trade order
    ra, rb = ctx.trade(pred), ctx.trade(succ)
    if ra is not None and rb is not None:
        if ra < rb:
            score += 8;  why.append("trade order runs the right way")
        elif ra == rb:
            score += 4
        else:
            score -= 12; why.append("trade order runs backwards")

    # 6. a milestone wants the END of something
    if succ.activity_type in ("Start Milestone", "Finish Milestone") and _is_terminal(pred.name):
        score += 8; why.append("predecessor is a completion/turnover")

    # 7. procurement coupling — an install cannot precede its own delivery
    if _is_install(succ.name):
        eq = set(_equipment_of(succ.name)) & set(_equipment_of(pred.name))
        if eq and re.search(r"(?i)deliver|fabricat|award|procure", pred.name or ""):
            score += 15
            why.append(f"delivery of {sorted(eq)[0]} gates this install")

    # 8. anchoring a dangling row is a bonus, never a reason on its own
    if pred.uid not in ctx.has_succ:
        score += 2

    # 9. Being the LAST work in the milestone's own phase is real evidence, and
    #    it is often the only evidence available: "Terminations" drives "Level 3
    #    Commissioning Start" without sharing a single word with it. Requiring
    #    shared vocabulary would reject exactly the ties a scheduler makes by
    #    looking at where the work runs out.
    if scope_latest:
        score += 15
        why.append("last work to finish in this milestone's phase")

    # Support tops out around 60 in practice — subject + area + same folder is
    # already a strong case, and further agreement should not be needed.
    support = max(0.0, min(1.0, score / 60.0))
    # Nothing says these two are about the same work: no shared word, no shared
    # room, and not the last thing to finish in the phase either. Sitting in the
    # same folder and running in a sensible trade order is true of thousands of
    # pairs and is not evidence of a handoff.
    if (not scope_latest and not (ta & tb)
            and not {t for t in (aa & ab) if not t.startswith("ph")}):
        support *= 0.5

    # 10. What the user has SAID about this job outranks anything inferred.
    #
    # Every signal above is a guess from names and dates. A directive is not:
    # somebody who has walked the job stated the rule. So a stated rule lifts
    # both terms — support to near-certain, and the date gate off the floor,
    # because a rule that disagrees with the dates means the DATES are the
    # thing in question, and that contradiction is reported rather than used
    # to quietly bury the tie. A rule the tie runs backwards against returns
    # zero: it is never proposed, at any date fit.
    if ctx.directives:
        sup, vio = _brain.verdicts(ctx.directives, pred.name, succ.name,
                                   ctx.where(pred), ctx.where(succ))
        if vio:
            return 0.0, why + [f"contradicts what you said: {vio[0].text}"]
        if sup:
            support = max(support, 0.90)
            date_factor = max(date_factor, 0.55)
            why.insert(0, f"you said: {sup[0].text}")

    # 11. How ties of this SHAPE have actually been received on this job.
    #
    # Applying a proposal and dismissing one are both judgements, and both used
    # to be discarded — the same wrong tie could be offered indefinitely. This
    # nudges by what was accepted before, deliberately AFTER the stated rules
    # and bounded small: it breaks ties between candidates the evidence already
    # likes, and must never overrule a rule or manufacture a handoff out of a
    # pair with nothing else going for it.
    if ctx.feedback:
        nudge = _brain.feedback_score(ctx.feedback, pred.name, succ.name)
        if nudge:
            support = max(0.0, min(1.0, support + nudge))
            why.append("you usually accept this kind of tie" if nudge > 0
                       else "you usually reject this kind of tie")

    # A perfect date with nothing else behind it lands at 0.15 — well under the
    # bar. Two unrelated activities that happen to abut are a coincidence, not
    # a handoff, and the floor has to sit low enough that a stray point or two
    # of incidental agreement cannot carry one over the line.
    return date_factor * (0.15 + 0.85 * support), why


def milestone_drivers(project: Project, milestone: Activity,
                      limit: int = 3, ctx: Optional["_Ctx"] = None,
                      directives: Optional[List[Any]] = None,
                      feedback: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    What should drive this milestone's date.

    Candidates are the work inside the milestone's own phase that finishes at
    or before it. They are ranked by score_tie — date fit, shared subject,
    matching area, WBS distance, trade order, whether the candidate reads as
    the end of something — not by date proximity alone, which is what used to
    offer a 55-day gap as a driver simply because nothing closer existed.

    A milestone with no candidate clearing _MIN_CONFIDENCE returns nothing.
    "I cannot see what drives this" is a useful answer; a wrong tie is not.
    """
    ms_date = _parse(milestone.planned_start or milestone.planned_finish)
    if ms_date is None:
        return []
    scope_uid, scope_label = _phase_scope(project, milestone)
    pool = (activities_in(project, scope_uid) if scope_uid else list(project.activities))
    ctx = ctx or _Ctx(project, directives, feedback)

    # Everything in scope that finishes by the milestone and is close enough to
    # be a handoff. The calendar-day gate comes before the working-day count,
    # which walks a day at a time — work four months upstream is not the driver
    # and this keeps the scan off it entirely.
    eligible = []
    for a in pool:
        if a.uid == milestone.uid or a.activity_type in ("Start Milestone", "Finish Milestone"):
            continue
        fin = _parse(a.planned_finish or a.early_finish)
        if fin is None or fin > ms_date or (ms_date - fin).days > _MAX_GAP_DAYS:
            continue
        eligible.append((a, fin))

    # Which of them is the last to finish — the "where does the work run out"
    # question a scheduler asks first.
    latest_fin = max((f for _, f in eligible), default=None)

    scored = []
    for a, fin in eligible:
        lag = implied_lag(project, a, milestone)
        if lag is None:
            continue
        is_latest = latest_fin is not None and (latest_fin - fin).days <= 2
        conf, why = score_tie(ctx, a, milestone, lag, scope_latest=is_latest)
        scored.append((conf, lag, a, why))

    # Before trusting the date, check the work this milestone is ABOUT.
    #
    # "Finish Precast" is dated 2025-11-03 in the reference schedule while
    # every precast activity finishes between 2026-01 and 2026-04. Searching
    # backwards from a date like that can only turn up unrelated work that
    # happens to abut it — which is exactly what it did, offering MEP
    # Underground Excavations as the driver of a precast milestone. The
    # milestone's date is the thing that is wrong, and saying so is the useful
    # answer.
    subject_best, subject_conf = None, 0.0
    for a in pool:
        if a.uid == milestone.uid or a.activity_type in ("Start Milestone", "Finish Milestone"):
            continue
        c, _ = score_tie(ctx, a, milestone, 0)      # date held neutral
        if c > subject_conf:
            subject_best, subject_conf = a, c
    if subject_best is not None and subject_conf >= 0.55:
        sb_fin = _parse(subject_best.planned_finish or subject_best.early_finish)
        if sb_fin and sb_fin > ms_date:
            gap = working_days_between(ms_date, sb_fin, _calendar_of(project, milestone))
            rec = _rec(project, subject_best, milestone, "Finish to Start",
                       f"The work this milestone is about finishes {gap}d AFTER the "
                       f"milestone's own date — the date is unsupportable, not the tie")
            rec["verdict"] = CONFLICT
            rec["confidence"] = round(subject_conf, 2)
            rec["signals"] = [f"{subject_best.name} finishes "
                              f"{str(sb_fin)} vs milestone {str(ms_date)}",
                              "no earlier work on this subject exists to drive it"]
            rec["date_check"] = (
                f"Nothing that finishes by {ms_date} is about this milestone's work. "
                f"Move the milestone to {sb_fin}, or point it at different work.")
            return [rec]

    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], abs(t[1])))

    # A clear winner is a clear winner — no need to offer runners-up and make
    # the user choose between an obvious tie and two worse ones.
    top = scored[0][0]
    if top >= 0.75:
        scored = scored[:1]

    out = []
    for conf, lag, a, why in scored[:limit]:
        if conf < _MIN_CONFIDENCE:
            break
        rationale = "; ".join(why[:3]) if why else f"nearest work in {scope_label}"
        rec = _rec(project, a, milestone, "Finish to Start", rationale)
        rec["confidence"] = round(conf, 2)
        rec["signals"] = why
        out.append(rec)
    return out


_TIE_QUESTION_RE = re.compile(
    r"""(?ix)
    \b(?:best|good|right|correct|recommend\w*|suggest\w*|what|which|where|how)\b
    .{0,60}?
    \b(?:connect\w*|tie|ties|link\w*|logic|predecessor|successor|
        pred|succ|relationship|sequence|anchor)\b
    """)

_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{2,80})[\"'”’]")
_IDLIKE_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+){1,5}|A\d{3,6})\b")


def tie_question(text: str) -> bool:
    """
    Does this read as "what should X connect to?" rather than an instruction?

    Only a question of that shape should produce clickable tie options — the
    user asked for them explicitly. "Link A to B" is an instruction and must
    stay an instruction.
    """
    t = (text or "").strip()
    if not t or not _TIE_QUESTION_RE.search(t):
        return False
    # an explicit instruction, even if it mentions ties, is not a question
    if re.match(r"(?i)^\s*(add|make|create|tie|link|connect|set|apply|delete|remove)\b", t):
        return False
    return True


def find_activity_in(project: Project, text: str) -> List[Activity]:
    """
    The activity a tie question is about: a quoted name, an id-looking token,
    or the longest exact name from the schedule that appears in the text.

    Everything returned is a real row — nothing is constructed from the text.
    """
    by_id = {a.activity_id.lower(): a for a in project.activities}
    for m in _IDLIKE_RE.finditer(text or ""):
        hit = by_id.get(m.group(1).lower())
        if hit:
            return [hit]
    # The regex knows the shapes ids usually take (MDC1.PH1.CO.CL.4340, A00042).
    # A schedule is free to number its rows any way it likes, so also take any
    # bare word that IS an id verbatim. Requiring an exact match against a real
    # row means this can widen what is recognised but never invent anything.
    for word in re.split(r"[\s,;:()\[\]?!]+", text or ""):
        hit = by_id.get(word.strip(".").lower())
        if hit:
            return [hit]
    for m in _QUOTED_RE.finditer(text or ""):
        needle = m.group(1).strip().lower()
        exact = [a for a in project.activities if a.name.strip().lower() == needle]
        if exact:
            return exact
        part = [a for a in project.activities if needle in a.name.lower()]
        if part:
            return part[:8]
    low = (text or "").lower()
    hits = [a for a in project.activities
            if len(a.name) >= 6 and a.name.strip().lower() in low]
    hits.sort(key=lambda a: -len(a.name))
    if hits:
        top = hits[0].name.strip().lower()
        return [a for a in project.activities if a.name.strip().lower() == top][:8]
    return []


def tie_options(project: Project, act: Activity, limit: int = 4,
                directives: Optional[List[Any]] = None,
                feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Ranked predecessor and successor candidates for ONE activity, each with the
    confidence and the reasons behind it, ready to be offered as apply buttons.

    Same scoring as everywhere else — this is the single-activity view of it,
    for when the user points at a row and asks what it should connect to.
    """
    ctx = _Ctx(project, directives, feedback)
    start = _parse(act.actual_start or act.planned_start or act.early_start)
    finish = _parse(act.actual_finish or act.planned_finish or act.early_finish)
    linked_pred = {r.predecessor_uid for r in project.relations if r.successor_uid == act.uid}
    linked_succ = {r.successor_uid for r in project.relations if r.predecessor_uid == act.uid}

    preds, succs = [], []
    for other in project.activities:
        if other.uid == act.uid:
            continue
        o_fin = _parse(other.planned_finish or other.early_finish)
        o_start = _parse(other.planned_start or other.early_start)

        # candidate predecessor: finishes before this one starts
        if (start and o_fin and o_fin <= start
                and (start - o_fin).days <= _MAX_GAP_DAYS
                and other.uid not in linked_pred):
            lag = implied_lag(project, other, act)
            if lag is not None:
                c, why = score_tie(ctx, other, act, lag)
                if c >= _MIN_CONFIDENCE:
                    preds.append((c, lag, other, why))

        # candidate successor: starts after this one finishes
        if (finish and o_start and o_start >= finish
                and (o_start - finish).days <= _MAX_GAP_DAYS
                and other.uid not in linked_succ):
            lag = implied_lag(project, act, other)
            if lag is not None:
                c, why = score_tie(ctx, act, other, lag)
                if c >= _MIN_CONFIDENCE:
                    succs.append((c, lag, other, why))

    preds.sort(key=lambda t: (-t[0], abs(t[1])))
    succs.sort(key=lambda t: (-t[0], abs(t[1])))

    def pack(items, as_pred: bool):
        out = []
        for c, lag, other, why in items[:limit]:
            rec = (_rec(project, other, act, "Finish to Start", "; ".join(why[:3]))
                   if as_pred else
                   _rec(project, act, other, "Finish to Start", "; ".join(why[:3])))
            rec["confidence"] = round(c, 2)
            rec["signals"] = why
            out.append(rec)
        return out

    return {
        "activity_id": act.activity_id,
        "name": act.name,
        "wbs_path": wbs_path(project, act),
        "start": str(act.planned_start or "")[:10],
        "finish": str(act.planned_finish or "")[:10],
        "has_predecessor": bool(linked_pred),
        "has_successor": bool(linked_succ),
        "predecessors": pack(preds, True),
        "successors": pack(succs, False),
    }


def wire_folder(project: Project, root_uid: str, min_confidence: float = 0.45,
                limit: int = 400,
                directives: Optional[List[Any]] = None,
                feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Every tie worth making inside one folder, ranked — the bulk answer to open
    ends. The reference schedule has 1,610 activities with no predecessor and
    1,608 with no successor; one at a time is not a plan.

    Only rows that are ALREADY open are candidates for the end being closed, so
    this never second-guesses logic somebody put in deliberately. Each proposal
    is the single best predecessor for that row — not a list to choose from —
    because the point is a reviewable batch, not another decision per activity.
    A row whose best candidate does not clear `min_confidence` is left alone and
    counted, so the number that could not be answered is visible rather than
    quietly absent.

    The bar is deliberately higher than the single-activity view: this applies
    in bulk, so a wrong tie here is a wrong tie many times over.
    """
    scope = _descendants(project, root_uid)
    acts = [a for a in project.activities if a.wbs_uid in scope]
    if not acts:
        return {"proposals": [], "unresolved": 0, "activity_count": 0,
                "open_starts": 0, "open_finishes": 0}

    has_pred, has_succ = _open_ended(project)
    ctx = _Ctx(project, directives, feedback)
    by_uid = {a.uid: a for a in acts}
    open_start = [a for a in acts if a.uid not in has_pred
                  and a.activity_type not in ("Start Milestone", "Finish Milestone")
                  and a.status != "Completed"]

    proposals, unresolved = [], 0
    for succ in open_start:
        s_date = _parse(succ.actual_start or succ.planned_start or succ.early_start)
        if s_date is None:
            unresolved += 1
            continue
        best = None
        for pred in acts:
            if pred.uid == succ.uid or _has_link(project, pred.uid, succ.uid):
                continue
            if pred.activity_type in ("Start Milestone", "Finish Milestone"):
                continue
            f = _parse(pred.planned_finish or pred.early_finish)
            if f is None or f > s_date or (s_date - f).days > _MAX_GAP_DAYS:
                continue
            lag = implied_lag(project, pred, succ)
            if lag is None:
                continue
            c, why = score_tie(ctx, pred, succ, lag)
            if best is None or c > best[0]:
                best = (c, lag, pred, why)
        if best is None or best[0] < min_confidence:
            unresolved += 1
            continue
        c, lag, pred, why = best
        rec = _rec(project, pred, succ, "Finish to Start", "; ".join(why[:3]))
        rec["confidence"] = round(c, 2)
        rec["signals"] = why
        proposals.append(rec)

    proposals.sort(key=lambda r: -r["confidence"])
    return {
        "proposals": proposals[:limit],
        "unresolved": unresolved,
        "activity_count": len(acts),
        "open_starts": len(open_start),
        "open_finishes": sum(1 for a in acts if a.uid not in has_succ
                             and a.status != "Completed"),
        "min_confidence": min_confidence,
    }


def milestone_report(project: Project, limit_per_milestone: int = 3,
                     directives: Optional[List[Any]] = None,
                feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    ctx = _Ctx(project, directives, feedback)   # one set of caches for every milestone
    items = []
    for m in milestones:
        drivers = milestone_drivers(project, m, limit=limit_per_milestone, ctx=ctx)
        items.append({
            "activity_id": m.activity_id,
            "name": m.name,
            "date": str(m.planned_start or m.planned_finish or "")[:10],
            "type": m.activity_type,
            "wbs_path": wbs_path(project, m),
            "has_predecessor": m.uid in has_pred,
            "has_successor": m.uid in has_succ,
            "constraint": m.constraint_type or None,
            "drivers": drivers,
            # said out loud so the agent reports "nothing confident here"
            # instead of reaching for whatever was nearest
            "no_confident_driver": not drivers and m.uid not in has_pred,
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


def area_report(project: Project, needle: str, sample: int = 8) -> Dict[str, Any]:
    """Everything the agent needs to reason about one area, in one call."""
    digest = area_digest(project, needle, sample=sample)
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
