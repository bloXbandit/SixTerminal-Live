"""
objectives.py — what this project is FOR, kept between sessions.

The tool was excellent at "what should I do right now, given this schedule",
and had no idea "where are we in the thing we are doing". Every session
started from the schedule as it is, never as a position in a campaign — so
1,610 open ends looked identical on day one and after a month of closing
them, and the only way to know you were making progress was to remember.

An objective fixes a target once: "close every open end through to
commissioning". Progress is then MEASURED off the schedule each time it is
asked for — never a stored counter, which would drift the moment anything
was edited outside the loop, undone, or restored from a backup. The baseline
is the one number that IS stored, because "how far through" is meaningless
without knowing where it started.

Deliberately narrow. An objective is a measurable thing this module can count
in the file, not a free-text wish — a target nobody can score is a note, and
notes already have somewhere to live.
"""

import datetime as _dt
import uuid
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional


# ── What can be counted ──────────────────────────────────────────────────────
# Each kind answers one question: how many rows still need work? Every one is
# derived from the schedule, so an edit made anywhere — the grid, the agent, a
# re-upload — moves the number without anything being told.

OPEN_ENDS = "open_ends"
OPEN_STARTS = "open_starts"
OPEN_FINISHES = "open_finishes"
UNLINKED = "unlinked"
MILESTONES_ANCHORED = "milestones_anchored"
HARD_CONSTRAINTS = "hard_constraints"

KINDS: Dict[str, Dict[str, str]] = {
    OPEN_ENDS: {
        "label": "Close every open end",
        "counts": "activities missing a predecessor or a successor",
    },
    OPEN_STARTS: {
        "label": "Give every activity a predecessor",
        "counts": "activities with nothing driving them",
    },
    OPEN_FINISHES: {
        "label": "Give every activity a successor",
        "counts": "activities that drive nothing",
    },
    UNLINKED: {
        "label": "Connect every stranded activity",
        "counts": "activities with no logic at all",
    },
    MILESTONES_ANCHORED: {
        "label": "Anchor every milestone to logic",
        "counts": "milestones with nothing driving them",
    },
    HARD_CONSTRAINTS: {
        "label": "Replace hard constraints with logic",
        "counts": "activities pinned by a hard date constraint",
    },
}

_HARD = {"Must Start On", "Must Finish On", "Start On", "Finish On"}
_MILESTONE = {"Start Milestone", "Finish Milestone"}


@dataclass
class Objective:
    id: str
    kind: str
    text: str = ""                  # what the user called it, if anything
    scope_uid: Optional[str] = None  # a WBS branch, or the whole schedule
    scope_name: str = ""
    baseline: int = 0               # how many were outstanding when it was set
    created_at: str = ""
    done_at: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _descendants(project, root_uid: str) -> set:
    out, grew = {root_uid}, True
    while grew:
        grew = False
        for w in getattr(project, "wbs_nodes", None) or []:
            if w.parent_uid in out and w.uid not in out:
                out.add(w.uid)
                grew = True
    return out


def _in_scope(project, scope_uid: Optional[str]) -> List[Any]:
    acts = getattr(project, "activities", None) or []
    if not scope_uid:
        return list(acts)
    branch = _descendants(project, scope_uid)
    return [a for a in acts if a.wbs_uid in branch]


def remaining(project, kind: str, scope_uid: Optional[str] = None) -> int:
    """
    How many rows still need work for this objective, right now.

    Completed work is never counted as outstanding: an activity that is
    already finished does not need a predecessor found for it, and counting
    it would mean an objective that can never reach zero.
    """
    acts = _in_scope(project, scope_uid)
    rels = getattr(project, "relations", None) or []
    has_succ = {r.predecessor_uid for r in rels}
    has_pred = {r.successor_uid for r in rels}

    live = [a for a in acts if a.status != "Completed"]
    if kind == OPEN_STARTS:
        return sum(1 for a in live
                   if a.uid not in has_pred and a.activity_type not in _MILESTONE)
    if kind == OPEN_FINISHES:
        return sum(1 for a in live
                   if a.uid not in has_succ and a.activity_type not in _MILESTONE)
    if kind == OPEN_ENDS:
        return sum(1 for a in live
                   if a.activity_type not in _MILESTONE
                   and (a.uid not in has_pred or a.uid not in has_succ))
    if kind == UNLINKED:
        return sum(1 for a in live
                   if a.uid not in has_pred and a.uid not in has_succ)
    if kind == MILESTONES_ANCHORED:
        return sum(1 for a in live
                   if a.activity_type in _MILESTONE and a.uid not in has_pred)
    if kind == HARD_CONSTRAINTS:
        return sum(1 for a in live if (a.constraint_type or "") in _HARD)
    raise ValueError(f"Unknown objective kind '{kind}'")


def make(project, kind: str, text: str = "", scope_uid: Optional[str] = None,
         scope_name: str = "") -> Objective:
    """Fix a target, and record where it started so progress can be read."""
    if kind not in KINDS:
        raise ValueError(f"Unknown objective '{kind}'. "
                         f"Use one of: {', '.join(sorted(KINDS))}")
    return Objective(
        id=uuid.uuid4().hex[:8],
        kind=kind,
        text=(text or "").strip() or KINDS[kind]["label"],
        scope_uid=scope_uid or None,
        scope_name=scope_name or "",
        baseline=remaining(project, kind, scope_uid),
        created_at=_dt.datetime.now().isoformat(timespec="seconds"),
    )


def progress(project, obj: Objective) -> Dict[str, Any]:
    """
    Where this objective stands, measured off the schedule as it is now.

    `done` can exceed the baseline — activities get added, and closing ends
    on rows that did not exist when the target was set is still progress. It
    can also go NEGATIVE, when a batch of unlinked work arrives after the
    baseline was taken. Both are reported honestly rather than clamped into
    a percentage that looks tidy and lies about what happened.
    """
    left = remaining(project, obj.kind, obj.scope_uid)
    base = obj.baseline
    done = base - left
    pct = 100 if left == 0 else (int(round(done / base * 100)) if base > 0 else 0)
    return {
        **obj.to_json(),
        "label": KINDS[obj.kind]["label"],
        "counts": KINDS[obj.kind]["counts"],
        "remaining": left,
        "done": done,
        "percent": max(0, min(100, pct)),
        "grew": done < 0,
        "complete": left == 0,
        "where": obj.scope_name or "whole schedule",
    }


def line(project, obj: Objective) -> str:
    """
    The one line the agent reads every turn. Costs almost nothing and is the
    difference between "what do you want to do?" and "we're 26% through".
    """
    p = progress(project, obj)
    if p["complete"]:
        return f"OBJECTIVE MET — {p['text']} ({p['where']}). Nothing outstanding."
    if p["grew"]:
        return (f"OBJECTIVE: {p['text']} ({p['where']}) — {p['remaining']} "
                f"{p['counts']} outstanding, {abs(p['done'])} MORE than when this "
                f"was set ({p['baseline']}): work has been added since.")
    return (f"OBJECTIVE: {p['text']} ({p['where']}) — {p['done']} of "
            f"{p['baseline']} done ({p['percent']}%), {p['remaining']} "
            f"{p['counts']} left.")


def from_json(raw: Any) -> Optional[Objective]:
    if not isinstance(raw, dict) or not raw.get("kind"):
        return None
    if raw["kind"] not in KINDS:
        return None
    fields = {k: raw.get(k) for k in Objective.__dataclass_fields__ if k in raw}
    fields.setdefault("id", uuid.uuid4().hex[:8])
    return Objective(**fields)


def suggest(project) -> List[Dict[str, Any]]:
    """
    The targets worth setting on THIS schedule, biggest first.

    Offering all six on a file where five are already at zero is noise, so a
    kind with nothing outstanding is not suggested.
    """
    out = []
    for kind, meta in KINDS.items():
        n = remaining(project, kind)
        if n:
            out.append({"kind": kind, "label": meta["label"],
                        "counts": meta["counts"], "outstanding": n})
    out.sort(key=lambda x: -x["outstanding"])
    return out
