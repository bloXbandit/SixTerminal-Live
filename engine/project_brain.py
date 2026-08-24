"""
project_brain.py — what YOU know about this job, that no generic rule can.

The tie ranker knows dates, shared subject, area, WBS distance and trade
order. What it cannot know is how THIS project is being built: that ER rooms
run one after another, that QA/QC always follows terminations in the same
room, that a particular feeder gates a particular lineup. That knowledge is
the difference between a tie it can guess at and one it can be sure of.

So it is stated once, in plain language, and kept with the project.

Two kinds of thing come out of a sentence:

  a RULE   something checkable — "X after Y", "ER rooms run sequential".
           It becomes a scoring signal, so it actually changes which ties are
           proposed, and it can be checked BOTH ways: the schedule can be
           searched for places that break it.

  a NOTE   everything else. Guidance the agent reads, nothing enforced.

The distinction matters. A rule that only exists as prose in a prompt is a
hope; you cannot toggle it, and you certainly cannot ask "where does my
schedule break this?". Anything that parses becomes a rule; anything that
does not is kept verbatim as a note rather than being mangled into one.

Rules never rewrite the schedule on their own. Where a rule contradicts the
dates, that contradiction is reported — the dates are usually the thing most
worth trusting — and applying anyway is a deliberate override.
"""

import datetime as _dt
import re
import uuid
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


# ── Identity ─────────────────────────────────────────────────────────────────

def project_key(project) -> str:
    """
    The stable name for a job, so its knowledge survives the next export.

    Deliberately NOT the filename: uploading test6_edited_edited.xml twice
    produces two different sessions, and a brain tied to that would be orphaned
    within a day. P6's own project id ("25-1539-INT-1") stays put through every
    re-export, rename and round trip.
    """
    for cand in (getattr(project, "id", None), getattr(project, "name", None),
                 getattr(project, "uid", None)):
        s = str(cand or "").strip()
        if s:
            return s
    return "unknown"


# ── Directives ───────────────────────────────────────────────────────────────

ORDER = "order"          # X must come after Y
SEQUENCE = "sequence"    # a family of areas runs one after another, by number
ROOM_ORDER = "room_order"  # a family of areas runs in a STATED order
NOTE = "note"            # prose the agent reads, nothing enforced


@dataclass
class Directive:
    id: str
    text: str                       # exactly as typed, always shown back
    kind: str = NOTE
    subject: str = ""               # the thing that comes later (ORDER)
    after: str = ""                 # the thing that comes first (ORDER)
    family: str = ""                # "ER", "MV" … (SEQUENCE, ROOM_ORDER)
    order: List[int] = field(default_factory=list)   # stated run order (ROOM_ORDER)
    same_area: bool = False         # ORDER only applies within one room/area
    same_phase: bool = False        # ORDER only applies within one phase
    enabled: bool = True
    created_at: str = ""
    matched_subject: int = 0        # activities the later side names
    matched_after: int = 0          # activities the earlier side names
    note_reason: str = ""           # why a rule-shaped sentence stayed a note
    # The shape the SENTENCE has, which never changes. `kind` is what the rule
    # currently IS, which does: a rule matching nothing in today's schedule is
    # demoted to guidance, and must be able to come back if the work appears.
    # Without keeping the two apart, grounding is a one-way door.
    parsed_kind: str = ""
    # How the rule has fared when it actually bit. A rule stopped an edit and
    # the user went ahead anyway is evidence about the RULE, not just that
    # edit — and it used to be discarded, so a rule overridden thirty times
    # looked exactly like one never questioned.
    overridden: int = 0
    upheld: int = 0
    last_conflict_at: str = ""
    # "I know, keep it anyway." Acknowledging is not the same as upholding:
    # it silences the prompt at the count it stood at, so the rule is raised
    # again only if it goes on losing AFTER the user said to keep it. Counting
    # an acknowledgement as an uphold instead would need three clicks to clear
    # a three-override flag.
    ack_overrides: int = -1

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


_STOP = frozenset("""
the a an of in on at for to and always must should needs need has have is are
be will shall run runs running go goes come comes only ever every each
""".split())

# "X after Y", "X follows Y", "X only after Y"
_AFTER_RE = re.compile(
    r"(?i)^\s*(?P<subj>.{2,60}?)\s+(?:should\s+|must\s+|always\s+|can\s+only\s+)*"
    r"(?:come|comes|start|starts|go|goes|happen|happens|be)?\s*"
    r"(?:after|follow|follows|following)\s+(?P<after>.{2,60}?)\s*$")

# "Y before X", "Y precedes X"
_BEFORE_RE = re.compile(
    r"(?i)^\s*(?P<after>.{2,60}?)\s+(?:should\s+|must\s+|always\s+)*"
    r"(?:come|comes|be|go|goes)?\s*(?:before|precede|precedes|preceding)\s+"
    r"(?P<subj>.{2,60}?)\s*$")

# "ER rooms run sequential", "MV rooms are sequential", "sequential by ER room"
_SEQ_RE = re.compile(
    r"(?i)(?:^|\b)(?P<fam>[A-Za-z][\w&/-]{0,14})\s*(?:rooms?|areas?|lineups?|units?)?"
    r"[^.]{0,24}?\bsequential(?:ly)?\b")

# "MV rooms run 107, 105, 106", "ER room order is 3 -> 1 -> 2",
# "GEN areas go 2 then 1 then 3".
#
# A stated order is the thing "sequential" cannot express. Rooms are rarely
# built in number order — crane access, energisation order, or which end the
# GC hands over first decides it — and until this existed there was nowhere to
# put that. An ordering WORD is required (run / go / order / sequence): a bare
# "MV rooms 105, 106" is a list of rooms, not a claim about their order, and
# enforcing it as one would be exactly the half-understood guess this module
# refuses to make elsewhere.
_ROOM_ORDER_RE = re.compile(
    r"(?i)\b(?P<fam>[A-Za-z][\w&/-]{0,14})\s+(?:rooms?|areas?|lineups?|units?)\b"
    r"[^\d\n]{0,24}?\b(?:run|runs|go|goes|order|ordered|sequence|sequenced)\b"
    r"[^\d\n]{0,24}?"
    r"(?P<list>\d{1,4}(?:\s*(?:,|;|->|-+>|→|\bthen\b)\s*\d{1,4})+)")

_SAME_AREA_RE = re.compile(
    r"(?i)\b(?:in|within|for)\s+the\s+same\s+(?:room|area|zone|space|lineup|unit)\b"
    r"|\bsame[- ]room\b|\bper\s+room\b|\bwithin\s+each\b")

# "in the same phase", "per phase", "phase by phase" — a different scope from a
# room: phases are huge, but on a phased job every commissioning rule is meant
# per phase ("L4 follows L3 in the same phase"), not across the whole schedule.
_SAME_PHASE_RE = re.compile(
    r"(?i)\b(?:in|within|for)\s+the\s+same\s+phase\b|\bsame[- ]phase\b"
    r"|\bper\s+phase\b|\bwithin\s+each\s+phase\b|\bphase\s+by\s+phase\b")


def _clean(s: str) -> str:
    """Strip the filler off a phrase so it matches activity names sensibly."""
    t = re.sub(r"(?i)\b(?:the|all|any|every|each|its|their|a|an)\b", " ", s or "")
    t = re.sub(r"[\"'“”‘’.,;:]+", " ", t)
    t = re.sub(r"(?i)\bactivit(?:y|ies)\b|\bwork\b|\btasks?\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_directive(text: str) -> Directive:
    """
    Turn one sentence into a rule if it is one, otherwise keep it as a note.

    Only unambiguous shapes become rules. Guessing at a half-understood
    sentence and then enforcing it across thousands of activities is worse
    than leaving it as guidance.
    """
    raw = (text or "").strip()
    d = Directive(id=uuid.uuid4().hex[:8], text=raw, kind=NOTE,
                  created_at=_dt.datetime.now().isoformat(timespec="seconds"))
    if not raw:
        return d

    def _shape(kind):
        """The shape is recorded twice on purpose: `kind` is what the rule IS
        right now and grounding may demote it, `parsed_kind` is what the
        SENTENCE says and never changes — so a demotion can be undone when the
        work it names finally appears in the schedule."""
        d.kind = d.parsed_kind = kind
        return d

    body = re.sub(r"(?i)^\s*(?:please\s+|note[:,]?\s+|remember[:,]?\s+)", "", raw)
    body = body.rstrip(".")
    same_phase = bool(_SAME_PHASE_RE.search(body))
    stripped = _SAME_PHASE_RE.sub(" ", body)
    same_area = bool(_SAME_AREA_RE.search(stripped))
    # the scope qualifiers are not part of either side of the ordering
    stripped = _SAME_AREA_RE.sub(" ", stripped).strip()

    m = _AFTER_RE.match(stripped) or _BEFORE_RE.match(stripped)
    if m:
        subj, aft = _clean(m.group("subj")), _clean(m.group("after"))
        if subj and aft and subj.lower() != aft.lower():
            d.subject, d.after = subj, aft
            d.same_area, d.same_phase = same_area, same_phase
            return _shape(ORDER)

    # A STATED order is tried before "sequential", because it is the more
    # specific claim: "MV rooms run 107, 105, 106" says everything "MV rooms
    # run sequential" does and then overrides the number order.
    m = _ROOM_ORDER_RE.search(body)
    if m:
        fam = _clean(m.group("fam"))
        rooms, seen_rooms = [], set()
        for n in re.findall(r"\d{1,4}", m.group("list")):
            v = int(n)
            if v not in seen_rooms:
                seen_rooms.add(v)
                rooms.append(v)
        if fam and fam.lower() not in _STOP and len(fam) <= 15 and len(rooms) >= 2:
            d.family, d.order, d.same_area = fam, rooms, same_area
            return _shape(ROOM_ORDER)

    m = _SEQ_RE.search(body)
    if m:
        fam = _clean(m.group("fam"))
        if fam and fam.lower() not in _STOP and len(fam) <= 15:
            d.family, d.same_area = fam, same_area
            return _shape(SEQUENCE)

    return d


def ground(project, d: Directive) -> Directive:
    """
    Make a rule prove it names work that exists, before it is called a rule.

    The parser reads shape, not meaning, so "the owner wants the CUP energised
    before the data halls" comes out looking like an ordering rule. It is
    harmless — neither side matches any activity, so it never fires — but
    showing it in the panel as an enforced rule is a lie about what the tool
    is doing. A rule that cannot touch a single activity is guidance, and says
    so, with the reason.
    """
    acts = getattr(project, "activities", None) or []
    # Grounding runs again whenever the schedule changes, so it has to start
    # from what the SENTENCE says, not from the verdict it reached last time.
    # Otherwise a rule demoted once could never recover when the work it names
    # finally appears in the file — and re-grounding would be a ratchet that
    # only ever turned rules into notes.
    if not d.parsed_kind:
        d.parsed_kind = d.kind
    d.kind, d.note_reason = d.parsed_kind, ""
    d.matched_subject = d.matched_after = 0
    if d.kind == ORDER:
        d.matched_after = sum(1 for a in acts if phrase_matches(d.after, a.name))
        d.matched_subject = sum(1 for a in acts if phrase_matches(d.subject, a.name))
        if not d.matched_after or not d.matched_subject:
            missing = d.after if not d.matched_after else d.subject
            d.kind, d.note_reason = NOTE, (
                f"nothing in this schedule is called '{missing}', so there is "
                f"nothing to enforce — kept as guidance")
    elif d.kind == SEQUENCE:
        d.matched_subject = sum(
            1 for a in acts
            if family_index(d.family, a.name) is not None
            or family_index(d.family, where_of(project, a)) is not None)
        if d.matched_subject < 2:
            d.kind, d.note_reason = NOTE, (
                f"only {d.matched_subject} activities sit in a numbered "
                f"'{d.family}' room, so there is no sequence to enforce — "
                f"kept as guidance")
    elif d.kind == ROOM_ORDER:
        # An order over rooms this job does not have is not an order. Both the
        # activity count AND how many of the named rooms actually exist matter:
        # a list where only one room is real states no sequence at all.
        wanted = set(d.order)
        present = set()
        for a in acts:
            n = family_index(d.family, a.name)
            if n is None:
                n = family_index(d.family, where_of(project, a))
            if n is not None and n in wanted:
                present.add(n)
                d.matched_subject += 1
        missing = [n for n in d.order if n not in present]
        if len(present) < 2:
            found = f"{d.family} {sorted(present)[0]}" if present else "none of them"
            d.kind, d.note_reason = NOTE, (
                f"this schedule has {found} out of the {len(d.order)} "
                f"'{d.family}' rooms named, so there is no order to enforce — "
                f"kept as guidance")
        elif missing:
            # Enforceable on the rooms that DO exist — but say which ones do
            # not, because a typo'd room number is silently doing nothing.
            d.note_reason = (f"no {d.family} "
                             f"{', '.join(str(n) for n in missing)} in this schedule "
                             f"— the rest of the order is enforced")
    return d


def describe(d: Directive) -> str:
    """One line saying what was understood — shown back before anything uses it."""
    if d.kind == ORDER:
        where = ((" (within the same area)" if d.same_area else "")
                 + (" (within the same phase)" if d.same_phase else ""))
        seen = (f" — {d.matched_after} ↔ {d.matched_subject} activities"
                if d.matched_after or d.matched_subject else "")
        return f"'{d.subject}' must come after '{d.after}'{where}{seen}"
    if d.kind == SEQUENCE:
        seen = f" — {d.matched_subject} activities" if d.matched_subject else ""
        return f"'{d.family}' areas run one after another, in number order{seen}"
    if d.kind == ROOM_ORDER:
        run = " → ".join(f"{d.family} {n}" for n in d.order)
        seen = f" — {d.matched_subject} activities" if d.matched_subject else ""
        gap = f"; {d.note_reason}" if d.note_reason else ""
        return f"areas run {run}{seen}{gap}"
    if d.note_reason:
        return f"Guidance only — {d.note_reason}"
    return "Guidance for the agent — nothing enforced"


# ── Matching a directive against activities ──────────────────────────────────

@lru_cache(maxsize=20000)
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


@lru_cache(maxsize=4096)
def _phrase_words(phrase: str) -> Tuple[str, ...]:
    # Digits survive the length filter: "Level 3 Commissioning" and "Level 4
    # Commissioning" differ ONLY in the digit, and dropping it would make the
    # two phrases identical — a rule about L4 silently matching L3 milestones.
    return tuple(w for w in _norm(phrase).split()
                 if w not in _STOP and (len(w) > 1 or w.isdigit()))


def phrase_matches(phrase: str, name: str) -> bool:
    """
    Does this activity look like the thing the directive names?

    Every significant word of the phrase has to appear. "QA/QC inspections"
    matches "CWP-CUP-01 - QA/QC Inspections and Checklists"; it does not match
    "QA Manager Walkthrough".
    """
    words = _phrase_words(phrase)
    if not words:
        return False
    hay = _norm(name)
    toks = set(hay.split())
    # A digit must stand alone — "4" as a substring would find "PH4" or "400A"
    # and quietly widen the rule. A word may match as a prefix, so
    # "termination" still finds "Terminations".
    return all((w in toks) if w.isdigit() else (w in hay) for w in words)


_FAMILY_NUM_RE_CACHE: Dict[str, Any] = {}


def family_index(family: str, name: str) -> Optional[int]:
    """The N in 'ER 3' / 'ER-3' / 'ER Room 3', for a family like 'ER'."""
    fam = _norm(family)
    if not fam:
        return None
    rx = _FAMILY_NUM_RE_CACHE.get(fam)
    if rx is None:
        rx = re.compile(rf"(?i)\b{re.escape(fam)}\s*[-#]?\s*"
                        rf"(?:rooms?|areas?|lineups?|units?|rm)?\s*[-#]?\s*(\d{{1,4}})\b")
        _FAMILY_NUM_RE_CACHE[fam] = rx
    m = rx.search(name or "")
    return int(m.group(1)) if m else None


# The ranker's own area reader knows the equipment prefixes this trade uses —
# MV, UPS, CRAH, CUP. A directive routinely names a family it has never heard
# of, because the naming is this job's ("ER 105", "TR 4", "WBO MV 105"), so any
# short prefix followed by a number counts as naming a place here. Over-reading
# is the safe direction: a locus only ever NARROWS where a same-area rule
# applies, so a spurious one makes the rule apply less, never more.
_ROOM_RE = re.compile(
    r"(?i)\b([a-z]{1,5})\s*[-#]?\s*(?:rooms?|areas?|rm)?\s*[-#]?\s*(\d{1,4})\b")


@lru_cache(maxsize=20000)
def _locus(name: str) -> frozenset:
    """Every place this name identifies — phases included, tagged 'ph'."""
    from .logic_advisor import _area_tags
    out = set(_area_tags(name or ""))
    for m in _ROOM_RE.finditer(name or ""):
        word = m.group(1).lower()
        # "P1"/"PH2" are phases, not rooms — _AREA_RE already reads those.
        if word in _STOP or word in ("p", "ph", "phase", "l", "lvl", "level"):
            continue
        out.add(f"{word}{int(m.group(2))}")
    return frozenset(out)


@lru_cache(maxsize=20000)
def _work_of(family: str, name: str) -> str:
    """The name with the family's own room number taken out — the WORK it is."""
    fam = _norm(family)
    if not fam:
        return _norm(name)
    out = re.sub(rf"(?i)\b{re.escape(fam)}\s*[-#]?\s*"
                 rf"(?:rooms?|areas?|lineups?|units?|rm)?\s*[-#]?\s*\d{{1,4}}\b",
                 " ", name or "")
    return _norm(out)


def _place(name: str) -> frozenset:
    """The locus with phases dropped — half the schedule is in PH1."""
    return frozenset(t for t in _locus(name) if not t.startswith("ph"))


def _phases(name: str) -> frozenset:
    return frozenset(t for t in _locus(name) if t.startswith("ph"))


# ── What proposals actually get accepted ─────────────────────────────────────
# Confirming a rule is the only way knowledge used to enter the brain, and it
# costs the user a sentence. But every Apply and every dismiss is already a
# judgement about a proposed tie — thrown away until now. Propose the same
# wrong tie forty times and decline it forty times and nothing changed.
#
# What is learned is the SHAPE of the tie, not the pair: "Pull Wire ->
# Terminations" accepted twelve times should lift the thirteenth in a room
# nobody has touched yet. So the room number comes out and the remaining
# words are the signature.

_SIG_DROP = frozenset("""
a an and or of the to for at in on by with from into onto per is be
""".split())


def tie_signature(pred_name: str, succ_name: str) -> str:
    """
    The kind of handoff this is, with the place taken out.

    "Pull Wire MV 105 -> Terminations MV 105" and the same pair in MV 106 are
    one signature — which is the point: what was learned in one room has to
    apply in the next, or it never generalises past the row it came from.

    The place is removed WHOLE, family word and number together. Dropping only
    the digits leaves "mv" behind, and then a tie recorded from a card that
    showed "Pull Wire MV 105" would not match the same tie scored against an
    activity simply named "Pull Wire" — which is the common case, because
    plenty of jobs carry the room in the folder rather than the name.
    """
    def core(name: str) -> str:
        bare = _ROOM_RE.sub(" ", name or "")
        words = [w for w in _norm(bare).split()
                 if w not in _SIG_DROP and not w.isdigit() and len(w) > 1]
        return "-".join(sorted(set(words))[:6])
    return f"{core(pred_name)}>{core(succ_name)}"


# One observation is an accident. Below this a signature says nothing, which
# keeps a single misclick from re-ranking the schedule.
# Overriding a rule twice is a pair of exceptions; a third time is a pattern
# worth raising. Set low on purpose — the cost of asking is one line in a
# panel, and the cost of NOT asking is a rule quietly fighting the user for
# months.
_REVIEW_OVERRIDES = 3

_MIN_OBSERVATIONS = 2
# What agreement is worth, capped. Feedback nudges an order the schedule and
# the stated rules already argue for; it must never outweigh them.
_FEEDBACK_WEIGHT = 0.06
_FEEDBACK_CAP = 0.25


def feedback_score(counts: Dict[str, Any], pred_name: str, succ_name: str) -> float:
    """
    How this kind of tie has been received, as a bounded nudge.

    Returns 0.0 until a signature has been judged twice — and the same
    signature seen in reverse counts AGAINST, because accepting A -> B is
    also evidence that B -> A is wrong.
    """
    if not counts:
        return 0.0
    fwd = counts.get(tie_signature(pred_name, succ_name)) or {}
    rev = counts.get(tie_signature(succ_name, pred_name)) or {}
    seen = (fwd.get("accepted", 0) + fwd.get("declined", 0)
            + rev.get("accepted", 0) + rev.get("declined", 0))
    if seen < _MIN_OBSERVATIONS:
        return 0.0
    net = (fwd.get("accepted", 0) - fwd.get("declined", 0)
           - rev.get("accepted", 0))
    return max(-_FEEDBACK_CAP, min(_FEEDBACK_CAP, net * _FEEDBACK_WEIGHT))


def where_of(project, act) -> str:
    """
    The activity's folder path, minus the root node.

    On this kind of job the room is IN the path, not the name: "Pull Wire"
    says nothing, "Phase 1 (Build-Out) / MV Rooms / MV 105" says everything.
    The root comes off because it is the project's own code ("25-1539-INT-1"),
    which reads as a room number and would hand every activity on the job the
    same false shared place.
    """
    from .logic_advisor import wbs_path
    path = wbs_path(project, act)
    return path.split(" / ", 1)[1] if " / " in path else ""


def directive_verdict(d: Directive, pred_name: str, succ_name: str,
                      pred_where: str = "", succ_where: str = "") -> Optional[str]:
    """
    What this directive says about pred -> succ.

      "supports"   the directive asks for exactly this
      "violates"   the directive forbids it (it is the reverse)
      None         the directive has nothing to say about this pair

    pred_where / succ_where are the WBS paths. The WORK is matched on the name
    alone, but the PLACE — room, phase — is read from name and path together,
    because "Terminations" under "MV 105" carries its room in the folder.
    """
    if not d.enabled:
        return None
    p_here = f"{pred_name} {pred_where}"
    s_here = f"{succ_name} {succ_where}"

    if d.kind == ORDER:
        if phrase_matches(d.after, pred_name) and phrase_matches(d.subject, succ_name):
            if d.same_phase:
                pf, sf = _phases(p_here), _phases(s_here)
                if not (pf and sf and (pf & sf)):
                    return None            # different phases, or phase unknown
            if d.same_area:
                pp, sp = _place(p_here), _place(s_here)
                if pp and sp and not (pp & sp):
                    return None            # different rooms — rule does not apply
            return "supports"
        if phrase_matches(d.after, succ_name) and phrase_matches(d.subject, pred_name):
            if d.same_phase:
                pf, sf = _phases(p_here), _phases(s_here)
                if pf and sf and not (pf & sf):
                    return None            # reversed, but in different phases
            if d.same_area:
                pp, sp = _place(p_here), _place(s_here)
                if pp and sp and not (pp & sp):
                    return None
            return "violates"
        return None

    if d.kind == SEQUENCE:
        i = family_index(d.family, pred_name)
        j = family_index(d.family, succ_name)
        if i is None:
            i = family_index(d.family, pred_where)
        if j is None:
            j = family_index(d.family, succ_where)
        if i is None or j is None:
            return None
        # "ER rooms run sequential" means room N's pull is followed by room
        # N+1's pull — not that ANY work in 111 precedes ANY work in 112.
        # Read the loose way it endorses every pair across two rooms, which on
        # 30 rooms is thousands of ties nobody asked for. So the two rows have
        # to be the same work in different rooms before it says anything.
        if _work_of(d.family, pred_name) != _work_of(d.family, succ_name):
            return None
        if j == i + 1:
            return "supports"
        if j < i:
            return "violates"
        return None

    if d.kind == ROOM_ORDER:
        i = family_index(d.family, pred_name)
        j = family_index(d.family, succ_name)
        if i is None:
            i = family_index(d.family, pred_where)
        if j is None:
            j = family_index(d.family, succ_where)
        if i is None or j is None:
            return None
        # Same reasoning as SEQUENCE: this orders the SAME work across rooms,
        # not every pair of activities that happen to sit in two of them.
        if _work_of(d.family, pred_name) != _work_of(d.family, succ_name):
            return None
        try:
            pi, pj = d.order.index(i), d.order.index(j)
        except ValueError:
            return None          # a room the order says nothing about
        if pj == pi + 1:
            return "supports"
        if pj < pi:
            return "violates"
        return None

    return None


def verdicts(directives: List[Directive], pred_name: str, succ_name: str,
             pred_where: str = "", succ_where: str = ""
             ) -> Tuple[List[Directive], List[Directive]]:
    """(supporting, violating) directives for one candidate tie."""
    sup, vio = [], []
    for d in directives:
        v = directive_verdict(d, pred_name, succ_name, pred_where, succ_where)
        if v == "supports":
            sup.append(d)
        elif v == "violates":
            vio.append(d)
    return sup, vio


# ── Checking the schedule against what you said ──────────────────────────────

def check(project, directives: List[Directive], limit: int = 200) -> Dict[str, Any]:
    """
    Where does the schedule break these rules?

    Two ways it can: a relationship that runs the wrong way, or dates that put
    the work in the wrong order even with no tie between them. Both are worth
    seeing — the first is a logic error, the second is the thing you would have
    caught on a walk.
    """
    from .logic_advisor import _parse

    rules = [d for d in directives
             if d.enabled and d.kind in (ORDER, SEQUENCE, ROOM_ORDER)]
    if not rules:
        return {"checked": 0, "violations": [], "rules": 0}

    by_uid = {a.uid: a for a in project.activities}
    out, checked = [], 0
    _wcache: Dict[str, str] = {}

    def wo(a):
        if a.uid not in _wcache:
            _wcache[a.uid] = where_of(project, a)
        return _wcache[a.uid]

    for r in project.relations:
        p, s = by_uid.get(r.predecessor_uid), by_uid.get(r.successor_uid)
        if not p or not s:
            continue
        checked += 1
        for d in rules:
            if directive_verdict(d, p.name, s.name, wo(p), wo(s)) == "violates":
                out.append({
                    "kind": "relationship",
                    "directive": d.text, "directive_id": d.id,
                    "predecessor_id": p.activity_id, "predecessor_name": p.name,
                    "successor_id": s.activity_id, "successor_name": s.name,
                    "why": f"This tie runs the opposite way to: {describe(d)}",
                })
                if len(out) >= limit:
                    return {"checked": checked, "violations": out, "rules": len(rules),
                            "truncated": True}

    # dates in the wrong order, tie or no tie
    for d in rules:
        if d.kind != ORDER:
            continue
        firsts = [a for a in project.activities if phrase_matches(d.after, a.name)]
        laters = [a for a in project.activities if phrase_matches(d.subject, a.name)]
        if not firsts or not laters:
            continue
        for late in laters:
            ls = _parse(late.actual_start or late.planned_start)
            if ls is None:
                continue
            # Work that repeats per room is the normal case, and comparing
            # every QA/QC against every Terminations anywhere on the job would
            # flag the whole schedule: ER 105's inspection legitimately runs
            # before ER 106 is even wired. So when the rule names work that
            # exists in identifiable places, each row is judged against its OWN
            # place. Only when nothing shares a place — one energisation, one
            # substation — does the rule apply across the whole schedule.
            here = _place(f"{late.name} {wo(late)}")
            same_place = [f for f in firsts
                          if here and (_place(f"{f.name} {wo(f)}") & here)]
            pool = same_place or ([] if d.same_area and here else firsts)
            if d.same_phase:
                lf = _phases(f"{late.name} {wo(late)}")
                pool = [f for f in pool
                        if lf and (_phases(f"{f.name} {wo(f)}") & lf)]
            for first in pool:
                if first.uid == late.uid:
                    continue
                ff = _parse(first.planned_finish or first.actual_finish)
                if ff is None or ff <= ls:
                    continue
                out.append({
                    "kind": "dates",
                    "directive": d.text, "directive_id": d.id,
                    "predecessor_id": first.activity_id, "predecessor_name": first.name,
                    "successor_id": late.activity_id, "successor_name": late.name,
                    "why": (f"'{late.name}' starts {ls} but '{first.name}' does not "
                            f"finish until {ff} — the dates contradict: {describe(d)}"),
                })
                if len(out) >= limit:
                    return {"checked": checked, "violations": out, "rules": len(rules),
                            "truncated": True}
                break        # one example per late activity is enough

    return {"checked": checked, "violations": out, "rules": len(rules)}


# ── Store ────────────────────────────────────────────────────────────────────

class Brain:
    """Everything stated about one job. Isolated — nothing leaks between projects."""

    def __init__(self, key: str, directives: Optional[List[Directive]] = None):
        self.key = key
        self.directives: List[Directive] = directives or []
        # What this project is FOR — one standing target, measured off the
        # schedule whenever it is asked for. See engine/objectives.py.
        self.objective: Optional[Any] = None
        # How proposals of each SHAPE have been received on this job.
        # signature -> {"accepted": n, "declined": n}. Never sent to the
        # model: it is a scoring input, so it costs nothing per turn.
        self.feedback: Dict[str, Dict[str, int]] = {}
        # The flow a scope-of-work document describes, distilled. Weaker than
        # anything the user typed, stronger than a guess from names — see
        # engine/scope_graph.py.
        self.scope: Optional[Any] = None
        # Every document given for this job, kept so the agent can go back to
        # one. Only a CATALOGUE rides in the prompt — see engine/doc_library.py.
        self.library: Optional[Any] = None

    def is_empty(self) -> bool:
        """Nothing worth saving — checked before writing a file for a project
        that was never taught anything."""
        return (not self.directives and self.objective is None
                and not self.feedback and self.scope is None
                and (self.library is None or not self.library.docs))

    def record(self, pred_name: str, succ_name: str, accepted: bool) -> str:
        """Remember how a proposed tie of this shape was received."""
        sig = tie_signature(pred_name, succ_name)
        row = self.feedback.setdefault(sig, {"accepted": 0, "declined": 0})
        row["accepted" if accepted else "declined"] += 1
        return sig

    def record_conflict(self, did: str, overridden: bool) -> Optional[Directive]:
        """
        Remember how a rule fared the one time it actually bit.

        A rule that stopped an edit and was overridden anyway is evidence
        about the RULE, not just about that edit. Backing off when it fires is
        evidence the other way. Both were discarded before, which is why a
        rule overridden thirty times looked exactly like one never questioned.
        """
        for d in self.directives:
            if d.id == did:
                if overridden:
                    d.overridden += 1
                else:
                    d.upheld += 1
                d.last_conflict_at = _dt.datetime.now().isoformat(timespec="seconds")
                return d
        return None

    def reground(self, project) -> List[Directive]:
        """
        Re-test every rule against the schedule as it is NOW.

        Match counts were frozen at the moment a rule was taught, so a rule
        that bound twelve activities went on claiming twelve after they were
        renamed, moved or deleted — and a rule demoted to guidance because its
        work did not exist yet could never come back once it did. Both are
        fixed by grounding again; nothing else needs to know it happened.

        Returns the directives whose status actually changed.
        """
        changed = []
        for d in self.directives:
            before = (d.kind, d.matched_after, d.matched_subject)
            ground(project, d)
            if (d.kind, d.matched_after, d.matched_subject) != before:
                changed.append(d)
        return changed

    def needs_review(self) -> List[Dict[str, Any]]:
        """
        Rules worth a second look, with the reason and what to do about it.

        Two ways a rule goes bad, and they want different answers. One keeps
        getting overridden — it is probably too broad, or has an exception
        only the user knows. The other has quietly stopped matching anything —
        the naming changed, or the work was deleted, and it is now enforcing
        nothing while still looking like a rule.

        Nothing is decided here. A rule the user meant is still a rule however
        often it loses an argument with the schedule; the point is to stop it
        being invisible.
        """
        out = []
        for d in self.directives:
            if not d.enabled:
                continue
            since_ack = d.overridden - max(d.ack_overrides, 0)
            if (since_ack >= _REVIEW_OVERRIDES and d.overridden > d.upheld):
                out.append({
                    "id": d.id, "text": d.text, "reason": "overridden",
                    "overridden": d.overridden, "upheld": d.upheld,
                    "why": (f"You have overridden this {d.overridden} times"
                            + (f" and kept it {d.upheld}" if d.upheld else "")
                            + ". It may be too broad, or have an exception worth "
                              "stating."),
                    "suggest": "Narrow it, restate it, or drop it.",
                })
            elif (d.parsed_kind and d.parsed_kind != NOTE and d.kind == NOTE
                    and d.ack_overrides < 0):
                out.append({
                    "id": d.id, "text": d.text, "reason": "orphaned",
                    "overridden": d.overridden, "upheld": d.upheld,
                    "why": f"Enforcing nothing — {d.note_reason or 'it matches no activity'}.",
                    "suggest": "Reword it to match how the work is named here, or drop it.",
                })
        return out

    def acknowledge(self, did: str) -> Optional[Directive]:
        """
        "I know — keep it." Silences the prompt at the count it stands at, so
        the rule is raised again only if it goes on losing afterwards.
        """
        for d in self.directives:
            if d.id == did:
                d.ack_overrides = d.overridden
                return d
        return None

    # -- persistence ------------------------------------------------------
    def to_json(self) -> Dict[str, Any]:
        return {"key": self.key,
                "directives": [d.to_json() for d in self.directives],
                "objective": self.objective.to_json() if self.objective else None,
                "feedback": self.feedback,
                "scope": self.scope.to_json() if self.scope else None,
                "library": self.library.to_json() if self.library else None}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Brain":
        from . import objectives as _obj
        ds = []
        for raw in (data or {}).get("directives", []):
            fields = {k: raw.get(k) for k in Directive.__dataclass_fields__ if k in raw}
            fields.setdefault("id", uuid.uuid4().hex[:8])
            fields.setdefault("text", "")
            ds.append(Directive(**fields))
        b = cls((data or {}).get("key", "unknown"), ds)
        b.objective = _obj.from_json((data or {}).get("objective"))
        from . import scope_graph as _sg
        b.scope = _sg.ScopeGraph.from_json((data or {}).get("scope"))
        from . import doc_library as _dl
        b.library = _dl.Library.from_json((data or {}).get("library"))
        raw_fb = (data or {}).get("feedback")
        if isinstance(raw_fb, dict):
            for sig, row in raw_fb.items():
                if isinstance(row, dict):
                    b.feedback[str(sig)] = {
                        "accepted": int(row.get("accepted") or 0),
                        "declined": int(row.get("declined") or 0)}
        return b

    # -- editing ----------------------------------------------------------
    def add(self, text: str, project=None) -> Directive:
        d = parse_directive(text)
        if project is not None:
            ground(project, d)
        self.directives.append(d)
        return d

    def remove(self, did: str) -> bool:
        before = len(self.directives)
        self.directives = [d for d in self.directives if d.id != did]
        return len(self.directives) != before

    def toggle(self, did: str, on: Optional[bool] = None) -> Optional[Directive]:
        for d in self.directives:
            if d.id == did:
                d.enabled = (not d.enabled) if on is None else bool(on)
                return d
        return None

    # -- reading ----------------------------------------------------------
    @property
    def rules(self) -> List[Directive]:
        return [d for d in self.directives
                if d.enabled and d.kind in (ORDER, SEQUENCE, ROOM_ORDER)]

    @property
    def notes(self) -> List[Directive]:
        """General knowledge about the job — facts, not enforceable rules."""
        return [d for d in self.directives
                if d.enabled and d.kind == NOTE and not d.note_reason]

    @property
    def open_questions(self) -> List[Directive]:
        """
        Things stated as rules that could not be bound to any activity.

        These are the most useful thing in the brain and used to be the least
        visible: "QA/QC follows terminations" when nothing here is called
        QA/QC means either the naming differs or the work is missing, and
        both are worth raising. Buried in a flat NOTE list they read as
        ordinary guidance and nobody ever went back to them.
        """
        return [d for d in self.directives
                if d.enabled and d.kind == NOTE and d.note_reason]

    def docs(self):
        """The document library, created on first use."""
        from . import doc_library as _dl
        if self.library is None:
            self.library = _dl.Library()
        return self.library

    def set_objective(self, obj) -> None:
        self.objective = obj

    def objective_line(self, project=None) -> str:
        from . import objectives as _obj
        if self.objective is None or project is None:
            return ""
        try:
            return _obj.line(project, self.objective)
        except Exception:
            return ""      # a target whose kind no longer exists must not break a turn

    # How many of each section the prompt will carry. This block rides in
    # every request, so it is bounded rather than growing with the brain —
    # the full list is always one click away in the panel, and a tail line
    # says how much was left out so nothing looks complete when it is not.
    _CAP = 30

    def context_block(self, project=None) -> str:
        """
        What the agent is told about the job, in three separate piles.

        One flat list meant the agent could not tell an enforced rule from a
        stray remark from a question nobody answered — so it treated them
        alike, quoting guidance as if it were binding. They are kept apart
        here for the same reason they are kept apart everywhere else: they
        carry different authority.
        """
        objective = self.objective_line(project)
        rules, notes, questions = self.rules, self.notes, self.open_questions
        scope = self.scope.context_block() if self.scope else ""
        catalogue = self.library.catalogue_block() if self.library else ""
        if not (objective or rules or notes or questions or scope or catalogue):
            return ""

        def section(title, items, fmt):
            if not items:
                return []
            out = [title]
            out.extend(fmt(d) for d in items[:self._CAP])
            if len(items) > self._CAP:
                out.append(f"  …and {len(items) - self._CAP} more (ask to see them all)")
            return out

        lines = [""]
        if objective:
            lines += ["WHAT THIS PROJECT IS FOR:", "  " + objective,
                      "  Report progress against this when it is relevant. When the "
                      "user asks what to do next, answer from it."]
        lines += section(
            "RULES — HOW THIS JOB IS BUILT (stated by the user, ENFORCED):",
            rules, lambda d: f"  {d.text}   [{describe(d)}]")
        lines += section(
            "WHAT YOU KNOW ABOUT THIS JOB (context, nothing enforced):",
            notes, lambda d: f"  {d.text}")
        lines += section(
            "OPEN — stated but not matched to any activity. Raise these when relevant; "
            "do NOT treat them as in force:",
            questions, lambda d: f"  {d.text}   [{d.note_reason}]")
        # A rule the user keeps overriding is still a rule — but the agent
        # should stop presenting it as settled, and say so when it comes up.
        contested = [d for d in rules
                     if d.overridden >= _REVIEW_OVERRIDES and d.overridden > d.upheld]
        lines += section(
            "CONTESTED — enforced, but the user keeps overriding these. Apply them, "
            "and when one blocks something say it has been overridden before and "
            "ask whether it still holds:",
            contested,
            lambda d: f"  {d.text}   [overridden {d.overridden}×, kept {d.upheld}×]")
        if scope:
            lines.append(scope)
        if catalogue:
            lines.append(catalogue)
        if rules:
            lines.append("Rules are enforced in the tie ranking and checked against "
                         "the schedule. Where one contradicts the dates, say so — do "
                         "not quietly pick a side.")
        return "\n".join(lines)
