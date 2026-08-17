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
SEQUENCE = "sequence"    # a family of areas runs one after another
NOTE = "note"            # prose the agent reads, nothing enforced


@dataclass
class Directive:
    id: str
    text: str                       # exactly as typed, always shown back
    kind: str = NOTE
    subject: str = ""               # the thing that comes later (ORDER)
    after: str = ""                 # the thing that comes first (ORDER)
    family: str = ""                # "ER", "MV" … (SEQUENCE)
    same_area: bool = False         # ORDER only applies within one room/area
    enabled: bool = True
    created_at: str = ""
    matched_subject: int = 0        # activities the later side names
    matched_after: int = 0          # activities the earlier side names
    note_reason: str = ""           # why a rule-shaped sentence stayed a note

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

_SAME_AREA_RE = re.compile(
    r"(?i)\b(?:in|within|for)\s+the\s+same\s+(?:room|area|zone|space|lineup|unit)\b"
    r"|\bsame[- ]room\b|\bper\s+room\b|\bwithin\s+each\b")


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

    body = re.sub(r"(?i)^\s*(?:please\s+|note[:,]?\s+|remember[:,]?\s+)", "", raw)
    body = body.rstrip(".")
    same_area = bool(_SAME_AREA_RE.search(body))
    # the same-area qualifier is not part of either side of the ordering
    stripped = _SAME_AREA_RE.sub(" ", body)

    m = _AFTER_RE.match(stripped) or _BEFORE_RE.match(stripped)
    if m:
        subj, aft = _clean(m.group("subj")), _clean(m.group("after"))
        if subj and aft and subj.lower() != aft.lower():
            d.kind, d.subject, d.after, d.same_area = ORDER, subj, aft, same_area
            return d

    m = _SEQ_RE.search(body)
    if m:
        fam = _clean(m.group("fam"))
        if fam and fam.lower() not in _STOP and len(fam) <= 15:
            d.kind, d.family, d.same_area = SEQUENCE, fam, same_area
            return d

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
    if d.kind == ORDER:
        d.matched_after = sum(1 for a in acts if phrase_matches(d.after, a.name))
        d.matched_subject = sum(1 for a in acts if phrase_matches(d.subject, a.name))
        if not d.matched_after or not d.matched_subject:
            missing = d.after if not d.matched_after else d.subject
            d.kind, d.note_reason = NOTE, (
                f"nothing in this schedule is called '{missing}', so there is "
                f"nothing to enforce — kept as guidance")
    elif d.kind == SEQUENCE:
        d.matched_subject = sum(1 for a in acts
                                if family_index(d.family, a.name) is not None)
        if d.matched_subject < 2:
            d.kind, d.note_reason = NOTE, (
                f"only {d.matched_subject} activity names carry a '{d.family}' "
                f"number, so there is no sequence to enforce — kept as guidance")
    return d


def describe(d: Directive) -> str:
    """One line saying what was understood — shown back before anything uses it."""
    if d.kind == ORDER:
        where = " (within the same area)" if d.same_area else ""
        seen = (f" — {d.matched_after} ↔ {d.matched_subject} activities"
                if d.matched_after or d.matched_subject else "")
        return f"'{d.subject}' must come after '{d.after}'{where}{seen}"
    if d.kind == SEQUENCE:
        seen = f" — {d.matched_subject} activities" if d.matched_subject else ""
        return f"'{d.family}' areas run one after another, in number order{seen}"
    if d.note_reason:
        return f"Guidance only — {d.note_reason}"
    return "Guidance for the agent — nothing enforced"


# ── Matching a directive against activities ──────────────────────────────────

@lru_cache(maxsize=20000)
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


@lru_cache(maxsize=4096)
def _phrase_words(phrase: str) -> Tuple[str, ...]:
    return tuple(w for w in _norm(phrase).split()
                 if w not in _STOP and len(w) > 1)


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
    return all(w in hay for w in words)


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
        if word in _STOP or word in ("ph", "phase", "l", "lvl", "level"):
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


def directive_verdict(d: Directive, pred_name: str, succ_name: str) -> Optional[str]:
    """
    What this directive says about pred -> succ.

      "supports"   the directive asks for exactly this
      "violates"   the directive forbids it (it is the reverse)
      None         the directive has nothing to say about this pair
    """
    if not d.enabled:
        return None

    if d.kind == ORDER:
        if phrase_matches(d.after, pred_name) and phrase_matches(d.subject, succ_name):
            if d.same_area:
                pp, sp = _place(pred_name), _place(succ_name)
                if pp and sp and not (pp & sp):
                    return None            # different rooms — rule does not apply
            return "supports"
        if phrase_matches(d.after, succ_name) and phrase_matches(d.subject, pred_name):
            return "violates"
        return None

    if d.kind == SEQUENCE:
        i, j = family_index(d.family, pred_name), family_index(d.family, succ_name)
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

    return None


def verdicts(directives: List[Directive], pred_name: str,
             succ_name: str) -> Tuple[List[Directive], List[Directive]]:
    """(supporting, violating) directives for one candidate tie."""
    sup, vio = [], []
    for d in directives:
        v = directive_verdict(d, pred_name, succ_name)
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

    rules = [d for d in directives if d.enabled and d.kind in (ORDER, SEQUENCE)]
    if not rules:
        return {"checked": 0, "violations": [], "rules": 0}

    by_uid = {a.uid: a for a in project.activities}
    out, checked = [], 0

    for r in project.relations:
        p, s = by_uid.get(r.predecessor_uid), by_uid.get(r.successor_uid)
        if not p or not s:
            continue
        checked += 1
        for d in rules:
            if directive_verdict(d, p.name, s.name) == "violates":
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
            here = _place(late.name)
            same_place = [f for f in firsts if here and (_place(f.name) & here)]
            pool = same_place or ([] if d.same_area and here else firsts)
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

    # -- persistence ------------------------------------------------------
    def to_json(self) -> Dict[str, Any]:
        return {"key": self.key, "directives": [d.to_json() for d in self.directives]}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Brain":
        ds = []
        for raw in (data or {}).get("directives", []):
            fields = {k: raw.get(k) for k in Directive.__dataclass_fields__ if k in raw}
            fields.setdefault("id", uuid.uuid4().hex[:8])
            fields.setdefault("text", "")
            ds.append(Directive(**fields))
        return cls((data or {}).get("key", "unknown"), ds)

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
        return [d for d in self.directives if d.enabled and d.kind in (ORDER, SEQUENCE)]

    @property
    def notes(self) -> List[Directive]:
        return [d for d in self.directives if d.enabled and d.kind == NOTE]

    def context_block(self) -> str:
        """What the agent is told, kept short — this rides in every prompt."""
        if not self.directives:
            return ""
        lines = ["", "HOW THIS PROJECT IS BEING BUILT (stated by the user):"]
        for d in self.rules:
            lines.append(f"  RULE — {d.text}   [enforced: {describe(d)}]")
        for d in self.notes:
            lines.append(f"  NOTE — {d.text}")
        lines.append("  Rules are enforced in the tie ranking and checked against the "
                     "schedule. Where one contradicts the dates, say so — do not "
                     "quietly pick a side.")
        return "\n".join(lines)
