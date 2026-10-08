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

GROUPS: List[Tuple[str, str, Optional[str]]] = [
    # Pre-construction, which comes first and is not site work.
    ("Design", r"DSG", r"^design |design kickoff|\bspecs\b"),
    ("Funding & Pre-Construction", r"FDG",
     r"funding|\bGMP\b|market pricing|\bBSU\b|\bOAA\b|buyout"),
    ("Submittals", r"SUB|REM", r"submittal|shop drawing|load calculation"),
    ("Procurement", r"PRO", r"^procurement|procurement[ :/]|fabrication:"),
    ("Permits", r"PMT", r"\bpermit\b|site plan review|service authority|\bDEQ\b"),
    ("VDC Coordination", r"VDC", r"\bVDC\b|coordination drawing"),

    # The contract dates themselves, before the rooms they happen in.
    ("Completion Milestones", r"MIL",
     r"substantial completion|temporary certificate|final certificate|"
     r"certificate of occupan|complete construction|notice to proceed|"
     r"building finals|close.?out"),
    ("Commissioning & Startup", r"CO|CX",
     r"level [1-5]\s*(cx|comm)|commissioning|ready to energize|energi[sz]|"
     r"start.?up|burn.?in|functional test|integrated systems|"
     r"pre.?functional|piping (testing|flush)"),
    # CUP is an area, and most of its work is commissioning its own
    # equipment. Commissioning keeps priority so that a "Ready to Energize"
    # stays with the other energization dates; everything else CUP lands here
    # rather than being taken by the lineups its ids run under.
    ("CUP", r"CUP", r"\bcup[- ]?\d*\b|central utility"),
    ("Equipment Lineups", r"L|UP", r"lineup|line up"),
    ("Inspections & Tags", r"",
     r"(red|orange|yellow|green) tag|\bQA/QC\b|county.*inspection|"
     r"in.?wall inspection|contractor verification"),

    # Structure, bottom up.
    ("Precast", r"PC", r"precast|area \d+ (erection|turnover)"),
    ("Foundations & Underground", r"STR|UDG|SHF",
     r"deep foundation|footer|footing|caisson|pile\b|grade beam|"
     r"excavat|mep underground"),
    ("Slabs & Pours", r"S|U|UG|SOD|SOG|L1|L2|L3|L4|L5",
     r"\bpour\b|pour slab|form & tie|form and tie|slab on|concrete sealer|"
     r"saw cut"),
    ("Structural Steel", r"", r"high steel|steel erection|joist|decking|"
     r"shear stud|fabrication: steel"),

    # The rooms.
    ("Generator Rooms", r"GEN", r"\bgen(erator)?\s*\d{2,3}\b|generator room|"
     r"day tank|to engine\b"),
    ("MV Rooms", r"MV", r"\bmv\s*\d{2,3}\b|medium voltage"),
    ("HV Rooms", r"HV", r"\bhv\s*\d{2,3}\b|high voltage"),
    ("Electrical Rooms", r"ER|ERR\d*|POE\d*", r"\ber{1,2}\s*\d{2,3}\b|"
     r"electrical room|\bPOE\s*\d"),
    ("Data Halls", r"DA|DH", r"data hall|\bdh\s*\d{2,3}\b"),
    ("Galleries", r"GL|GAL|GL\d+|GL\d+[EW]", r"galler|\bgl\s*\d{3}\b"),
    ("Admin Build-Out", r"ADM", r"^office |admin build"),
    ("Misc Rooms", r"ROOM", r"vestibule|corridor|restroom"),

    # Equipment and the envelope.
    ("Long Lead Equipment", r"LLE|XFMR", r"long lead|transformer|\bxfmr\b|"
     r"\bMVS\b|switchgear"),
    ("Skids", r"SK|SKID", r"\bskid"),
    ("Roof", r"ROOF|MEP", r"\broof|lightning protection|roof coping"),
    ("Site & Exterior Utilities", r"EXT|SUT",
     r"mobilize|site telecom|wet utilit|dry utilit|duct bank|building pad"),
]

PHASES: Dict[str, str] = {"PH1": "Phase 1", "PH2": "Phase 2", "PH3": "Phase 3",
                          "P1": "Phase 1", "P2": "Phase 2", "P3": "Phase 3"}

_OTHER = "Other"
_NO_PHASE = "All phases"


def _compiled():
    """
    Each group's id-token test and name test, pre-compiled.

    The token test is a whole-segment match rather than a substring one. "L" is
    an equipment lineup and "L1" is the first floor; "ER" is an electrical room
    and "REM" is not. Matching loosely put floor slabs in with the lineups.
    """
    return [(g,
             _re.compile(rf"^(?:{toks})$", _re.I) if toks else None,
             _re.compile(pat, _re.I) if pat else None)
            for g, toks, pat in GROUPS]


_MATCHERS = _compiled()


def categorize(activity_id: str, name: str = "") -> Tuple[str, str]:
    """
    The group and phase an activity belongs to.

    The id is checked a segment at a time rather than by position, since the
    coding differs between buildings; the name carries the rest. The first
    group that matches wins, so the order of the table is what decides the
    ambiguous cases -- procurement before the generator rooms, so that
    GEN.PRO "Procurement / Fabrication: Precast" is procurement; commissioning
    before the room it happens in.
    """
    segs = [s for s in (activity_id or "").split(".") if s]
    phase = next((PHASES[s.upper()] for s in segs if s.upper() in PHASES),
                 _NO_PHASE)
    text = name or ""
    for group, toks, pat in _MATCHERS:
        if toks is not None and any(toks.match(s) for s in segs):
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


def compare_pairwise(baseline: Snapshot, updates: Sequence[Snapshot],
                     include_other: bool = True) -> Dict[str, Any]:
    """
    Each update measured against the baseline on ITS OWN common set.

    `compare` holds every issue to one common set, which is right when the
    activity ids are stable and punishing when they are not. This GC renumbers
    at every re-issue: of MDC-3's 1,957 contract activities 1,142 survive into
    June and 974 into September, but only 968 into both -- and intersecting all
    four sources at once leaves most groups with one or two rows, which is not
    a basis for a number anybody should quote.

    So each column is intersected with the baseline on its own. Every column is
    still like-for-like against the contract; they simply rest on different
    subsets, and each carries the count it was measured on so the reader can
    see which ones are thin.
    """
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for upd in updates:
        one = compare(baseline, [upd], include_other=include_other)
        for row in one["rows"]:
            key = (row["group"], row["phase"])
            slot = merged.setdefault(key, {"group": row["group"],
                                           "phase": row["phase"]})
            slot[baseline.label] = row[baseline.label]
            cell = dict(row[upd.label])
            cell["activities"] = row["activities"]
            slot[upd.label] = cell

    order = {g: i for i, (g, _, _) in enumerate(GROUPS)}
    order[_OTHER] = len(order)
    rows = [merged[k] for k in sorted(merged,
                                      key=lambda k: (order.get(k[0], 99), k[1]))]
    scope = {"common": None}
    for snap in [baseline] + list(updates):
        scope[snap.label] = {"total": len(snap.rows)}
    for upd in updates:
        shared = set(baseline.rows) & set(upd.rows)
        scope[upd.label]["shared_with_baseline"] = len(shared)
        scope[upd.label]["not_in_baseline"] = len(set(upd.rows) - shared)
    return {"rows": rows, "scope": scope, "baseline": baseline.label,
            "labels": [baseline.label] + [u.label for u in updates]}


def as_issued(snapshots: Sequence[Snapshot],
              include_other: bool = True) -> Dict[str, Any]:
    """
    Each group's window in each issue, on whatever that issue contains.

    The companion to `compare`, and the honest other half of the picture: the
    like-for-like number is what can be argued as movement, and this is what
    the schedule now actually says, scope growth and all. Read on its own it
    overstates slip; read next to the other it shows where the growth is.
    """
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for snap in snapshots:
        for aid, row in snap.rows.items():
            g, ph = categorize(aid, row["name"])
            if g == _OTHER and not include_other:
                continue
            cell = groups.setdefault((g, ph), {}).setdefault(
                snap.label, {"start": None, "finish": None, "activities": 0})
            cell["activities"] += 1
            if row["start"] and (cell["start"] is None or row["start"] < cell["start"]):
                cell["start"] = row["start"]
            if row["finish"] and (cell["finish"] is None or row["finish"] > cell["finish"]):
                cell["finish"] = row["finish"]

    order = {g: i for i, (g, _, _) in enumerate(GROUPS)}
    order[_OTHER] = len(order)
    base = snapshots[0].label
    rows: List[Dict[str, Any]] = []
    for (g, ph), cells in sorted(groups.items(),
                                 key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        row: Dict[str, Any] = {"group": g, "phase": ph}
        for snap in snapshots:
            cell = dict(cells.get(snap.label)
                        or {"start": None, "finish": None, "activities": 0})
            if snap.label != base:
                cell["slip_days"] = _days(cell["finish"],
                                          (cells.get(base) or {}).get("finish"))
                cell["added"] = cell["activities"] - (
                    (cells.get(base) or {}).get("activities", 0))
            row[snap.label] = cell
        rows.append(row)
    return {"rows": rows, "labels": [s.label for s in snapshots], "baseline": base}


def worst(result: Dict[str, Any], label: str, limit: int = 10) -> List[Dict[str, Any]]:
    """The groups that moved furthest in one update, latest first."""
    got = [r for r in result["rows"] if (r.get(label) or {}).get("slip_days") is not None]
    got.sort(key=lambda r: r[label]["slip_days"], reverse=True)
    return got[:limit]


def describe(result: Dict[str, Any]) -> str:
    """A few lines for the terminal, and for a header row in the workbook."""
    common = result["scope"].get("common")
    out = [f"baseline: {result['baseline']}"]
    out.append(f"compared on {common} activities common to all "
               f"{len(result['labels'])} issues" if common is not None else
               "each issue compared on its own overlap with the baseline")
    for lab in result["labels"]:
        sc = result["scope"][lab]
        if "not_in_common" in sc:
            out.append(f"  {lab}: {sc['total']} activities, "
                       f"{sc['not_in_common']} not in the common set")
        elif "shared_with_baseline" in sc:
            out.append(f"  {lab}: {sc['total']} activities, "
                       f"{sc['shared_with_baseline']} shared with the baseline, "
                       f"{sc['not_in_baseline']} new")
        else:
            out.append(f"  {lab}: {sc['total']} activities (the baseline)")
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
