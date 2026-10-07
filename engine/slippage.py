"""
slippage.py — what moved between the contract schedule and each update.

A job is handed over on a contract schedule and then re-issued every month or
two, and the question anybody actually asks is not which of 3,600 activities
changed. It is whether the generator rooms are later than they were, by how
much, and whether the commissioning dates at the end of them moved with it.

So this rolls the activities up into the handful of groups a job is talked
about in -- precast, pours, generator rooms, MV, HV, commissioning, the
completion milestones -- and reports each group's window against the contract.

Two things it refuses to do, because both quietly turn a scope change into
slippage:

A group's window is measured on the activities the sources have IN COMMON.
An update that adds thirty generator activities has a later "last finish"
whether or not a single existing date moved, and a group's window taken from
whatever each source happened to contain reports that as slip. The activities
only one side has are counted and listed separately, as scope.

A date is only compared against the same kind of date. An update that has
started work reports actuals, and an actual start against a planned start is
progress, not movement.

The groups are matched on the activity name as well as the id, because the id
coding is not the same on all three buildings -- GEN is the second segment on
MDC-2 and MDC-3 and the third on MDC-1 -- and the printed Gantt a contract
arrives as gives a name and an id and nothing else to go on.
"""

from __future__ import annotations

import collections
import datetime as _dt
import re as _re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── the groups a job is discussed in ────────────────────────────────────────
# (group, id-segment tokens, name pattern). A row matches on either the id
# token or the name, and the FIRST group that matches wins -- so the order is
# the specific-before-general one, and commissioning is tested before the room
# it happens in.

GROUPS: List[Tuple[str, Tuple[str, ...], Optional[str]]] = [
    ("Completion Milestones", ("MIL",),
     r"substantial completion|temporary certificate|certificate of occupan|"
     r"complete construction|notice to proceed|building finals|close.?out"),
    ("Commissioning & Energization", ("CX",),
     r"level [1-5]\s*(cx|comm)|commissioning|ready to energize|energi[sz]|"
     r"start.?up|burn.?in|functional test|testing and inspection"),
    ("Precast", ("PC",), r"precast|erection|turnover"),
    ("Concrete & Pours", ("FDG", "SOD", "SOG", "UDG"),
     r"\bpour\b|slab|footing|foundation|mat\b|caisson|pile\b|grade beam"),
    ("Structural Steel", ("STR", "ST", "SHF"),
     r"high steel|steel|joist|decking|shear"),
    ("Generator Rooms", ("GEN",), r"\bgen(erator)?\s*\d|\bgen\b"),
    ("MV Rooms", ("MV",), r"\bmv\b|medium voltage"),
    ("HV Rooms", ("HV",), r"\bhv\b|high voltage"),
    ("Electrical Rooms", ("ER",), r"\ber\s*\d|electrical room"),
    ("Galleries", ("GL", "GAL"), r"galler|\bgl\s*\d"),
    ("CUP", ("CUP",), r"\bcup\b|central utility"),
    ("Long Lead Equipment", ("LLE",), r"long lead"),
    ("Roof", ("ROOF",), r"\broof"),
    ("Underground & Utilities", ("UG", "U"), r"underground|utilit|duct bank"),
    ("Substation & Transformers", ("SUB", "XFMR"),
     r"substation|transformer|\bxfmr\b"),
    ("Skids", ("SK", "SKID"), r"\bskid"),
]

PHASES: Dict[str, str] = {"PH1": "Phase 1", "PH2": "Phase 2", "PH3": "Phase 3",
                          "P1": "Phase 1", "P2": "Phase 2", "P3": "Phase 3"}

_OTHER = "Other"
_NO_PHASE = "All phases"


def _compiled():
    return [(g, toks, _re.compile(pat, _re.I) if pat else None)
            for g, toks, pat in GROUPS]


_MATCHERS = _compiled()


def categorize(activity_id: str, name: str = "") -> Tuple[str, str]:
    """
    The group and phase an activity belongs to.

    The id is checked a segment at a time rather than by position, since the
    coding differs between buildings; the name carries the rest.
    """
    segs = [s.upper() for s in (activity_id or "").split(".")]
    phase = next((PHASES[s] for s in segs if s in PHASES), _NO_PHASE)
    tokens = set(segs)
    text = name or ""
    for group, toks, pat in _MATCHERS:
        if tokens & set(toks):
            return group, phase
        if pat is not None and pat.search(text):
            return group, phase
    return _OTHER, phase


# ── a dated snapshot of a schedule, whatever it arrived as ──────────────────

def _iso(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10] if _re.match(r"\d{4}-\d{2}-\d{2}", text) else None


class Snapshot:
    """
    One issue of a schedule: activity id -> dates, however it was read.

    `actual` records whether each date was reported as actual, so a started
    activity is not compared against a planned date and called movement.
    """

    def __init__(self, label: str, data_date: Optional[str] = None):
        self.label = label
        self.data_date = data_date
        self.rows: Dict[str, Dict[str, Any]] = {}

    def add(self, activity_id, name, start, finish,
            start_actual: bool = False, finish_actual: bool = False) -> None:
        aid = (activity_id or "").strip()
        if not aid:
            return
        s, f = _iso(start), _iso(finish)
        if s is None and f is None:
            return
        # A print repeats an activity on a continuation page; keep the widest.
        prev = self.rows.get(aid)
        if prev:
            s = min(x for x in (s, prev["start"]) if x) if (s or prev["start"]) else None
            f = max(x for x in (f, prev["finish"]) if x) if (f or prev["finish"]) else None
        self.rows[aid] = {"name": name or (prev or {}).get("name") or "",
                          "start": s, "finish": f,
                          "start_actual": bool(start_actual) or (prev or {}).get("start_actual", False),
                          "finish_actual": bool(finish_actual) or (prev or {}).get("finish_actual", False)}

    def __len__(self) -> int:
        return len(self.rows)

    @classmethod
    def from_gantt(cls, result: Dict[str, Any], label: str,
                   data_date: Optional[str] = None) -> "Snapshot":
        """A printed Gantt, as engine.gantt_pdf.read returns it."""
        snap = cls(label, data_date)
        for r in result.get("rows", []):
            if r.get("kind") != "activity":
                continue
            snap.add(r.get("activity_id"), r.get("name"), r.get("start"),
                     r.get("finish"), r.get("start_actual"), r.get("finish_actual"))
        return snap

    @classmethod
    def from_project(cls, project, label: str,
                     data_date: Optional[str] = None) -> "Snapshot":
        """A schedule in the app's own model."""
        snap = cls(label, data_date or getattr(project, "data_date", None))
        for a in project.activities:
            start = getattr(a, "actual_start", None) or getattr(a, "planned_start", None)
            finish = getattr(a, "actual_finish", None) or getattr(a, "planned_finish", None)
            snap.add(a.activity_id, a.name, start, finish,
                     bool(getattr(a, "actual_start", None)),
                     bool(getattr(a, "actual_finish", None)))
        return snap

    @classmethod
    def from_rows(cls, rows: Sequence[Dict[str, Any]], label: str,
                  data_date: Optional[str] = None) -> "Snapshot":
        """Tabular rows with activity_id / name / start / finish keys."""
        snap = cls(label, data_date)
        for r in rows:
            snap.add(r.get("activity_id"), r.get("name"), r.get("start"),
                     r.get("finish"), r.get("start_actual"), r.get("finish_actual"))
        return snap


# ── the comparison ──────────────────────────────────────────────────────────

def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    return (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days


def compare(baseline: Snapshot, updates: Sequence[Snapshot],
            include_other: bool = True) -> Dict[str, Any]:
    """
    Each group's window in the baseline and in every update, and the slip.

    The window is measured only on the activities present in the baseline AND
    in every update, so what it reports is movement rather than scope. The
    rest is counted and listed under "scope".
    """
    everyone = [baseline] + list(updates)
    common = set(baseline.rows)
    for u in updates:
        common &= set(u.rows)

    grouped: Dict[Tuple[str, str], List[str]] = collections.defaultdict(list)
    for aid in common:
        g, ph = categorize(aid, baseline.rows[aid]["name"])
        if g == _OTHER and not include_other:
            continue
        grouped[(g, ph)].append(aid)

    order = {g: i for i, (g, _, _) in enumerate(GROUPS)}
    order[_OTHER] = len(order)

    rows: List[Dict[str, Any]] = []
    for (g, ph), aids in sorted(grouped.items(),
                                key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        row: Dict[str, Any] = {"group": g, "phase": ph, "activities": len(aids)}
        base_fin = None
        for snap in everyone:
            starts = [snap.rows[a]["start"] for a in aids if snap.rows[a]["start"]]
            fins = [snap.rows[a]["finish"] for a in aids if snap.rows[a]["finish"]]
            started = sum(1 for a in aids if snap.rows[a]["start_actual"])
            done = sum(1 for a in aids if snap.rows[a]["finish_actual"])
            s, f = (min(starts) if starts else None), (max(fins) if fins else None)
            row[snap.label] = {"start": s, "finish": f, "started": started,
                               "complete": done}
            if snap is baseline:
                base_fin = f
            else:
                row[snap.label]["slip_days"] = _days(f, base_fin)
        rows.append(row)

    scope = {"common": len(common)}
    for snap in everyone:
        only = set(snap.rows) - common
        scope[snap.label] = {"total": len(snap.rows), "not_in_common": len(only),
                             "sample": sorted(only)[:25]}
    return {"rows": rows, "scope": scope,
            "labels": [s.label for s in everyone],
            "baseline": baseline.label}


def worst(result: Dict[str, Any], label: str, limit: int = 10) -> List[Dict[str, Any]]:
    """The groups that moved furthest in one update, latest first."""
    got = [r for r in result["rows"] if (r.get(label) or {}).get("slip_days") is not None]
    got.sort(key=lambda r: r[label]["slip_days"], reverse=True)
    return got[:limit]


def describe(result: Dict[str, Any]) -> str:
    """A few lines for the terminal, and for a header row in the workbook."""
    out = [f"baseline: {result['baseline']}",
           f"compared on {result['scope']['common']} activities common to all "
           f"{len(result['labels'])} issues"]
    for lab in result["labels"]:
        sc = result["scope"][lab]
        out.append(f"  {lab}: {sc['total']} activities, "
                   f"{sc['not_in_common']} not in the common set")
    for lab in result["labels"][1:]:
        moved = [r for r in result["rows"]
                 if (r.get(lab) or {}).get("slip_days")]
        later = [r for r in moved if r[lab]["slip_days"] > 0]
        if moved:
            top = max(moved, key=lambda r: r[lab]["slip_days"])
            out.append(f"  {lab}: {len(later)} of {len(result['rows'])} groups "
                       f"later; worst {top['group']} ({top['phase']}) "
                       f"{top[lab]['slip_days']:+}d")
    return "\n".join(out)
