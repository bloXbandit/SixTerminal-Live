"""
schedule_model.py — Internal data model for Six Terminal Live.

All parsers (XER, P6 XML) normalize their output into these dataclasses.
The edit engine operates on this model.
The XML writer serializes this model back to valid P6 XML.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Calendar:
    uid: str
    name: str
    hours_per_day: float = 8.0
    hours_per_week: float = 40.0
    hours_per_month: float = 176.0
    hours_per_year: float = 2080.0
    type: str = "Global"  # Global | Project | Resource


@dataclass
class WBSNode:
    uid: str
    name: str
    code: str
    parent_uid: Optional[str] = None
    sequence_num: int = 0


@dataclass
class Activity:
    uid: str
    activity_id: str          # User-visible code e.g. "A1000"
    name: str
    wbs_uid: str
    calendar_uid: str
    activity_type: str = "Task Dependent"  # Task Dependent | Resource Dependent | Level of Effort | WBS Summary | Start Milestone | Finish Milestone
    status: str = "Not Started"            # Not Started | In Progress | Completed
    planned_duration: float = 0.0          # hours
    remaining_duration: float = 0.0        # hours
    actual_duration: float = 0.0           # hours
    percent_complete: float = 0.0
    planned_start: Optional[str] = None    # ISO date string
    planned_finish: Optional[str] = None
    actual_start: Optional[str] = None
    actual_finish: Optional[str] = None
    early_start: Optional[str] = None
    early_finish: Optional[str] = None
    late_start: Optional[str] = None
    late_finish: Optional[str] = None
    total_float: Optional[float] = None    # hours
    free_float: Optional[float] = None     # hours
    is_critical: bool = False
    is_longest_path: bool = False
    constraint_type: Optional[str] = None
    constraint_date: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Relation:
    uid: str
    predecessor_uid: str
    successor_uid: str
    type: str = "Finish to Start"   # Finish to Start | Start to Start | Finish to Finish | Start to Finish
    lag: float = 0.0                # hours


@dataclass
class Project:
    uid: str
    name: str
    id: str                         # Short project code e.g. "MTJ-UP08"
    data_date: Optional[str] = None
    planned_start: Optional[str] = None
    must_finish_by: Optional[str] = None
    status_code: str = "Active"
    calendars: List[Calendar] = field(default_factory=list)
    wbs_nodes: List[WBSNode] = field(default_factory=list)
    activities: List[Activity] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)

    # Lookup helpers (populated after load)
    _activity_by_uid: Dict[str, Activity] = field(default_factory=dict, repr=False)
    _activity_by_id: Dict[str, Activity] = field(default_factory=dict, repr=False)
    _wbs_by_uid: Dict[str, WBSNode] = field(default_factory=dict, repr=False)

    def build_lookups(self):
        """Build fast-access lookup dicts after loading."""
        self._activity_by_uid = {a.uid: a for a in self.activities}
        self._activity_by_id = {a.activity_id: a for a in self.activities}
        self._wbs_by_uid = {w.uid: w for w in self.wbs_nodes}

    def get_activity(self, uid: Optional[str] = None, activity_id: Optional[str] = None) -> Optional[Activity]:
        if uid:
            return self._activity_by_uid.get(uid)
        if activity_id:
            return self._activity_by_id.get(activity_id)
        return None

    def get_wbs(self, uid: str) -> Optional[WBSNode]:
        return self._wbs_by_uid.get(uid)

    def summary(self) -> str:
        return (
            f"Project: {self.name} ({self.id})\n"
            f"  Data Date: {self.data_date}\n"
            f"  Activities: {len(self.activities)}\n"
            f"  WBS Nodes: {len(self.wbs_nodes)}\n"
            f"  Relations: {len(self.relations)}\n"
            f"  Calendars: {len(self.calendars)}"
        )

    def llm_context(self, max_activities: int = 120) -> str:
        """
        Rich context string for the LLM.
        Includes WBS structure, full activity list with pred/succ links,
        float-derived criticality, critical path chain, and suggested next ID.

        Criticality rules (per DCMA / P6 best practice):
          critical      = total_float <= 0 h
          near_critical = 0 < total_float <= 80 h  (~10 working days)
        """
        from typing import Dict as _Dict, List as _List

        wbs_map = {w.uid: w for w in self.wbs_nodes}
        act_by_uid: _Dict[str, "Activity"] = {a.uid: a for a in self.activities}

        # ── Build pred/succ maps keyed by activity uid ─────────────────────
        preds_of: _Dict[str, _List[str]] = {}   # uid -> list of "A1000 FS" strings
        succs_of: _Dict[str, _List[str]] = {}

        for rel in self.relations:
            p = act_by_uid.get(rel.predecessor_uid)
            s = act_by_uid.get(rel.successor_uid)
            if not p or not s:
                continue
            # Abbreviate relation type
            rt = rel.type
            abbr = ("FS" if "Finish to Start" in rt else
                    "SS" if "Start to Start"  in rt else
                    "FF" if "Finish to Finish" in rt else
                    "SF")
            lag_str = ""
            if rel.lag and rel.lag != 0:
                lag_days = rel.lag / 8.0
                lag_str = f"+{lag_days:.0f}d" if lag_days > 0 else f"{lag_days:.0f}d"
            link = f"{s.activity_id} {abbr}{lag_str}"
            succs_of.setdefault(p.uid, []).append(link)
            link2 = f"{p.activity_id} {abbr}{lag_str}"
            preds_of.setdefault(s.uid, []).append(link2)

        # ── Derive criticality from float (do NOT trust P6's is_critical) ──
        def float_hrs(a: "Activity") -> Optional[float]:
            """Return best available float in hours, or None."""
            return (a.total_float if a.total_float is not None
                    else a.free_float)

        def is_critical(a: "Activity") -> bool:
            f = float_hrs(a)
            return f is not None and f <= 0

        def is_near_critical(a: "Activity") -> bool:
            f = float_hrs(a)
            return f is not None and 0 < f <= 80

        # ── Walk critical path backward from latest finish milestone ────────
        MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}
        finish_milestones = [
            a for a in self.activities
            if a.activity_type == "Finish Milestone" and a.status != "Completed"
        ]
        # Pick the one with the latest planned finish as the CP target
        def _sort_key(a: "Activity"):
            return a.planned_finish or ""

        cp_chain: _List[str] = []
        if finish_milestones:
            target = max(finish_milestones, key=_sort_key)
            # Build predecessor uid map for walk
            pred_uid_map: _Dict[str, _List[str]] = {}
            for rel in self.relations:
                pred_uid_map.setdefault(rel.successor_uid, []).append(rel.predecessor_uid)

            visited: set = set()
            current_uid = target.uid
            MAX_DEPTH = 60
            for _ in range(MAX_DEPTH):
                act = act_by_uid.get(current_uid)
                if not act or current_uid in visited:
                    break
                visited.add(current_uid)
                cp_chain.append(act.activity_id)
                candidates = [
                    act_by_uid[uid]
                    for uid in pred_uid_map.get(current_uid, [])
                    if uid in act_by_uid and uid not in visited
                ]
                if not candidates:
                    break
                # Sort: lowest float first, then latest finish as tiebreaker
                candidates.sort(key=lambda x: (
                    float_hrs(x) if float_hrs(x) is not None else 9999,
                    -(ord((_sort_key(x) or "0")[0]) if (_sort_key(x) or "") else 0),
                ))
                current_uid = candidates[0].uid

        # ── Header counts ────────────────────────────────────────────────────
        HARD_CONSTRAINT_TYPES = {"Must Start On", "Must Finish On", "Start On", "Finish On"}
        SOFT_CONSTRAINT_TYPES = {"Start On Or After", "Finish On Or Before", "Start On Or Before", "Finish On Or After"}

        task_acts   = [a for a in self.activities if a.activity_type not in MILESTONE_TYPES]
        critical_count    = sum(1 for a in self.activities if is_critical(a))
        near_crit_count   = sum(1 for a in self.activities if is_near_critical(a))
        open_start_acts   = [a for a in task_acts if not preds_of.get(a.uid)]
        open_finish_acts  = [a for a in task_acts if not succs_of.get(a.uid)]
        long_dur_acts     = [a for a in task_acts if a.planned_duration and a.planned_duration > 352]  # >44 working days
        zero_dur_tasks    = [a for a in task_acts if not a.planned_duration or a.planned_duration == 0]
        hard_constrained  = [a for a in self.activities if a.constraint_type in HARD_CONSTRAINT_TYPES]
        soft_constrained  = [a for a in self.activities if a.constraint_type in SOFT_CONSTRAINT_TYPES]

        # Missed tasks: past data date, not completed
        missed_tasks = []
        if self.data_date:
            dd = str(self.data_date)[:10]
            for a in task_acts:
                pf = str(a.planned_finish or "")[:10]
                if pf and pf < dd and a.status != "Completed" and a.actual_finish is None:
                    missed_tasks.append(a)

        # Relationship type breakdown
        rel_type_counts: dict = {}
        lagged_rels = []
        for rel in self.relations:
            rt = rel.type
            abbr = ("FS" if "Finish to Start" in rt else
                    "SS" if "Start to Start"  in rt else
                    "FF" if "Finish to Finish" in rt else "SF")
            rel_type_counts[abbr] = rel_type_counts.get(abbr, 0) + 1
            if rel.lag and abs(rel.lag) >= 8:  # >=1 working day of lag
                p = act_by_uid.get(rel.predecessor_uid)
                s = act_by_uid.get(rel.successor_uid)
                if p and s:
                    lag_d = rel.lag / 8.0
                    lagged_rels.append(f"{p.activity_id}->{s.activity_id} {abbr} lag={lag_d:+.0f}d")

        total_rels = len(self.relations)
        fs_pct = round(rel_type_counts.get("FS", 0) / total_rels * 100) if total_rels else 0
        density = round(total_rels / len(task_acts), 2) if task_acts else 0

        # Per-WBS risk rollup
        wbs_risk: dict = {}  # wbs_name -> {total, critical, open_s, open_f}
        for a in task_acts:
            wbs = wbs_map.get(a.wbs_uid)
            wn  = wbs.name if wbs else "Unknown"
            r   = wbs_risk.setdefault(wn, {"total": 0, "crit": 0, "open_s": 0, "open_f": 0})
            r["total"] += 1
            if is_critical(a):   r["crit"]   += 1
            if not preds_of.get(a.uid): r["open_s"] += 1
            if not succs_of.get(a.uid): r["open_f"] += 1

        lines = [
            f"Project: {self.name} ({self.id})",
            f"Data Date: {self.data_date}  |  Planned Start: {self.planned_start}  |  Must Finish By: {self.must_finish_by or 'not set'}",
            f"Activities: {len(self.activities)} ({len(task_acts)} tasks, {len(self.activities)-len(task_acts)} milestones)  |  WBS Nodes: {len(self.wbs_nodes)}  |  Relations: {total_rels}",
            f"Network Density: {density} rels/task  |  FS: {rel_type_counts.get('FS',0)} ({fs_pct}%)  SS: {rel_type_counts.get('SS',0)}  FF: {rel_type_counts.get('FF',0)}  SF: {rel_type_counts.get('SF',0)}",
            f"Critical (float<=0): {critical_count}  |  Near-Critical: {near_crit_count}  |  Open Start: {len(open_start_acts)}  |  Open Finish: {len(open_finish_acts)}",
            f"Hard Constraints: {len(hard_constrained)}  |  Soft Constraints: {len(soft_constrained)}  |  Missed Tasks: {len(missed_tasks)}  |  Long Duration (>44d): {len(long_dur_acts)}",
            "",
            "WBS STRUCTURE & RISK ROLLUP:",
        ]

        for w in self.wbs_nodes:
            parent = wbs_map.get(w.parent_uid) if w.parent_uid else None
            indent = "    " if parent else "  "
            risk = wbs_risk.get(w.name, {})
            risk_note = ""
            if risk.get("total"):
                crit_pct = round(risk["crit"] / risk["total"] * 100)
                risk_note = f"  [{risk['total']} acts | {crit_pct}% crit | open_s:{risk['open_s']} open_f:{risk['open_f']}]"
            lines.append(f"{indent}{w.code} — {w.name}{risk_note}")

        # ── Critical path chain ─────────────────────────────────────────────
        if cp_chain:
            lines.append("")
            lines.append(f"CRITICAL PATH CHAIN ({len(cp_chain)} steps, backward from project end):")
            lines.append("  " + " -> ".join(cp_chain))

        # ── Open ends (explicitly listed) ───────────────────────────────────
        if open_start_acts or open_finish_acts:
            lines.append("")
            lines.append("OPEN ENDS — ACTIVITIES MISSING LOGIC:")
            if open_start_acts:
                lines.append("  NO PREDECESSOR (open start):")
                for a in open_start_acts[:30]:
                    wbs = wbs_map.get(a.wbs_uid)
                    lines.append(f"    {a.activity_id} — {a.name}  [{wbs.name if wbs else '?'}]")
            if open_finish_acts:
                lines.append("  NO SUCCESSOR (open finish):")
                for a in open_finish_acts[:30]:
                    wbs = wbs_map.get(a.wbs_uid)
                    lines.append(f"    {a.activity_id} — {a.name}  [{wbs.name if wbs else '?'}]")

        # ── Hard constraints ─────────────────────────────────────────────────
        if hard_constrained:
            lines.append("")
            lines.append(f"HARD CONSTRAINTS ({len(hard_constrained)}) — may drive negative float:")
            for a in hard_constrained[:20]:
                lines.append(f"  {a.activity_id} — {a.name}  |  {a.constraint_type}: {a.constraint_date}")

        # ── Missed tasks ─────────────────────────────────────────────────────
        if missed_tasks:
            lines.append("")
            lines.append(f"MISSED TASKS ({len(missed_tasks)}) — planned finish before data date, not complete:")
            for a in missed_tasks[:20]:
                lines.append(f"  {a.activity_id} — {a.name}  |  planned finish: {str(a.planned_finish or '')[:10]}")

        # ── Long duration (DCMA #8) ──────────────────────────────────────────
        if long_dur_acts:
            lines.append("")
            lines.append(f"LONG DURATION >{44}d (DCMA #8) — {len(long_dur_acts)} activities:")
            for a in sorted(long_dur_acts, key=lambda x: -(x.planned_duration or 0))[:15]:
                d = round(a.planned_duration / 8)
                lines.append(f"  {a.activity_id} — {a.name}  |  {d}d")

        # ── Lagged relationships (DCMA #3) ──────────────────────────────────
        if lagged_rels:
            lines.append("")
            lines.append(f"LAGGED RELATIONSHIPS (DCMA #3) — {len(lagged_rels)} with lag >= 1d:")
            for lr in lagged_rels[:15]:
                lines.append(f"  {lr}")

        # ── Activity list ───────────────────────────────────────────────────
        lines.append("")
        lines.append(f"ACTIVITIES ({len(self.activities)} total):")

        shown = self.activities[:max_activities]
        for a in shown:
            wbs      = wbs_map.get(a.wbs_uid)
            wbs_name = wbs.name if wbs else "?"
            dur_days = f"{a.planned_duration / 8:.0f}d" if a.planned_duration else "0d"

            fh = float_hrs(a)
            if fh is None:
                float_tag = " [no float data]"
            elif fh <= 0:
                float_tag = " [CRITICAL, float=0]"
            elif fh <= 80:
                float_tag = f" [NEAR-CRITICAL, float={fh/8:.1f}d]"
            else:
                float_tag = f" [float={fh/8:.0f}d]"

            flags = []
            if a.constraint_type in HARD_CONSTRAINT_TYPES:
                flags.append(f"HARD-CON:{a.constraint_type}")
            elif a.constraint_type:
                flags.append(f"CON:{a.constraint_type}")
            if a.planned_duration and a.planned_duration > 352:
                flags.append("LONG-DUR")
            if not a.planned_duration and a.activity_type not in MILESTONE_TYPES:
                flags.append("ZERO-DUR")
            flag_str = (" [" + " | ".join(flags) + "]") if flags else ""

            preds_str = "PREDS: " + ", ".join(preds_of.get(a.uid, [])) if preds_of.get(a.uid) else "NO-PRED"
            succs_str = "SUCCS: " + ", ".join(succs_of.get(a.uid, [])) if succs_of.get(a.uid) else "NO-SUCC"
            rel_str   = f"  |  {preds_str}  |  {succs_str}"

            lines.append(
                f"  {a.activity_id} — {a.name}"
                f"  |  WBS: {wbs_name}"
                f"  |  {dur_days}"
                f"  |  {a.status}"
                f"{rel_str}"
                f"{float_tag}"
                f"{flag_str}"
            )

        if len(self.activities) > max_activities:
            lines.append(f"  ... ({len(self.activities) - max_activities} more activities not shown)")

        # ── Suggested next activity ID ───────────────────────────────────────
        numeric_ids = []
        for a in self.activities:
            raw = a.activity_id.lstrip("AaBbCc")
            try:
                numeric_ids.append(int(raw))
            except ValueError:
                pass
        if numeric_ids:
            last_num = max(numeric_ids)
            next_num = ((last_num // 10) + 1) * 10
            prefix = ""
            for a in self.activities:
                try:
                    int(a.activity_id.lstrip("AaBbCc"))
                    prefix = a.activity_id[0] if a.activity_id[0].isalpha() else ""
                    break
                except ValueError:
                    pass
            lines.append("")
            lines.append(f"SUGGESTED NEXT ACTIVITY ID: {prefix}{next_num:04d}  (last used: {prefix}{last_num:04d})")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CPM Forward / Backward Pass
# ─────────────────────────────────────────────────────────────────────────────

def compute_dates(project: "Project") -> None:
    """
    Run a CPM forward + backward pass on the project network.

    Updates for every activity:
      early_start, early_finish  — from forward pass
      late_start,  late_finish   — from backward pass
      total_float, is_critical   — derived
      planned_start, planned_finish — set to early dates for not-started/in-progress
                                      (matches P6's "Start" / "Finish" column convention)

    Working calendar: Mon–Fri, hours_per_day from the first project calendar (default 8h).
    Weekends are skipped; holidays are not modelled.
    Completed activities are anchored to their actual dates (not recomputed).
    """
    from datetime import date as _date, timedelta as _td
    import math as _math

    if not project.activities:
        return

    # ── Calendar ────────────────────────────────────────────────────────────
    hpd: float = 8.0
    if project.calendars:
        hpd = project.calendars[0].hours_per_day or 8.0

    # ── Origin date ──────────────────────────────────────────────────────────
    origin_str = (
        str(project.planned_start)[:10] if project.planned_start
        else str(project.data_date)[:10] if project.data_date
        else None
    )
    if not origin_str:
        return
    try:
        origin: _date = _date.fromisoformat(origin_str)
    except (ValueError, TypeError):
        return
    # Advance to Monday if origin falls on a weekend
    while origin.weekday() >= 5:
        origin += _td(days=1)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _parse(s) -> Optional[_date]:
        if not s:
            return None
        try:
            return _date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None

    def _snap(d: _date) -> _date:
        """Advance to next working day if d is a weekend."""
        while d.weekday() >= 5:
            d += _td(days=1)
        return d

    def _add_wd(start: _date, days: float) -> _date:
        """Add working days (Mon–Fri) to start.  Negative days goes backward."""
        d = _snap(start)
        if days == 0:
            return d
        step = 1 if days > 0 else -1
        remaining = abs(int(_math.ceil(abs(days))))
        added = 0
        while added < remaining:
            d += _td(days=step)
            if d.weekday() < 5:
                added += 1
        return d

    def _wd_between(d1: _date, d2: _date) -> float:
        """Working-day count from d1 to d2 (positive when d2 > d1)."""
        if d2 == d1:
            return 0.0
        sign = 1 if d2 > d1 else -1
        count = 0
        d = min(d1, d2)
        end = max(d1, d2)
        while d < end:
            d += _td(days=1)
            if d.weekday() < 5:
                count += 1
        return sign * float(count)

    MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}

    # ── Build predecessor / successor maps ───────────────────────────────────
    act_by_uid: Dict[str, "Activity"] = {a.uid: a for a in project.activities}
    preds: Dict[str, list] = {a.uid: [] for a in project.activities}
    succs: Dict[str, list] = {a.uid: [] for a in project.activities}

    for rel in project.relations:
        if rel.predecessor_uid in act_by_uid and rel.successor_uid in act_by_uid:
            lag_d = rel.lag / hpd  # hours → working days
            preds[rel.successor_uid].append((rel.predecessor_uid, rel.type, lag_d))
            succs[rel.predecessor_uid].append((rel.successor_uid, rel.type, lag_d))

    # ── Topological sort (Kahn's) ────────────────────────────────────────────
    in_deg = {a.uid: len(preds[a.uid]) for a in project.activities}
    queue = [a.uid for a in project.activities if in_deg[a.uid] == 0]
    topo: list = []
    while queue:
        uid = queue.pop(0)
        topo.append(uid)
        for s_uid, _, _ in succs.get(uid, []):
            in_deg[s_uid] -= 1
            if in_deg[s_uid] == 0:
                queue.append(s_uid)
    # Append any cycle members so they still get dates
    in_topo = set(topo)
    for a in project.activities:
        if a.uid not in in_topo:
            topo.append(a.uid)

    # ── Forward pass ─────────────────────────────────────────────────────────
    es: Dict[str, _date] = {}   # early start
    ef: Dict[str, _date] = {}   # early finish

    for uid in topo:
        act = act_by_uid.get(uid)
        if not act:
            continue

        is_ms = act.activity_type in MILESTONE_TYPES
        dur_d = 0.0 if is_ms else (act.planned_duration or 0.0) / hpd

        # Completed → anchor to actual dates
        if act.status == "Completed" and act.actual_start and act.actual_finish:
            es[uid] = _parse(act.actual_start) or origin
            ef[uid] = _parse(act.actual_finish) or origin
            continue

        # In-progress → actual start is fixed
        if act.status == "In Progress" and act.actual_start:
            es_date = _snap(_parse(act.actual_start) or origin)
        else:
            # Derive ES from predecessors
            es_date = origin
            for p_uid, rel_type, lag_d in preds[uid]:
                pef = ef.get(p_uid, origin)
                pes = es.get(p_uid, origin)
                if "Start to Start" in rel_type:
                    cand = _add_wd(pes, lag_d)
                elif "Finish to Finish" in rel_type:
                    cand = _add_wd(_add_wd(pef, lag_d), -dur_d)
                elif "Start to Finish" in rel_type:
                    cand = _add_wd(pes, lag_d - dur_d)
                else:  # Finish to Start (default)
                    cand = _add_wd(pef, lag_d)
                if cand > es_date:
                    es_date = cand
            es_date = _snap(es_date)

            # Hard / soft constraints on start
            ct = act.constraint_type or ""
            cd = _parse(act.constraint_date)
            if ct in ("Must Start On", "Start On") and cd:
                es_date = _snap(cd)
            elif ct in ("Start On Or After", "Start On Or Before") and cd:
                if ct == "Start On Or After" and cd > es_date:
                    es_date = _snap(cd)

        ef_date = _add_wd(es_date, dur_d) if dur_d > 0 else es_date

        # Hard constraints on finish
        ct = act.constraint_type or ""
        cd = _parse(act.constraint_date)
        if ct in ("Must Finish On", "Finish On") and cd:
            ef_date = _snap(cd)
            es_date = _add_wd(ef_date, -dur_d) if dur_d > 0 else ef_date
        elif ct == "Finish On Or Before" and cd and cd < ef_date:
            ef_date = _snap(cd)

        es[uid] = es_date
        ef[uid] = ef_date

    # ── Backward pass ────────────────────────────────────────────────────────
    all_ef = [d for d in ef.values() if d]
    if not all_ef:
        return

    if project.must_finish_by:
        mfb = _parse(str(project.must_finish_by)[:10])
        project_lf: _date = mfb if mfb else max(all_ef)
    else:
        project_lf = max(all_ef)

    ls: Dict[str, _date] = {}
    lf: Dict[str, _date] = {}

    for uid in reversed(topo):
        act = act_by_uid.get(uid)
        if not act:
            continue
        is_ms = act.activity_type in MILESTONE_TYPES
        dur_d = 0.0 if is_ms else (act.planned_duration or 0.0) / hpd

        if act.status == "Completed":
            ls[uid] = es.get(uid, origin)
            lf[uid] = ef.get(uid, origin)
            continue

        lf_date = project_lf
        for s_uid, rel_type, lag_d in succs.get(uid, []):
            sls = ls.get(s_uid, project_lf)
            slf = lf.get(s_uid, project_lf)
            if "Start to Start" in rel_type:
                cand = _add_wd(_add_wd(sls, -lag_d), dur_d)
            elif "Finish to Finish" in rel_type:
                cand = _add_wd(slf, -lag_d)
            elif "Start to Finish" in rel_type:
                cand = _add_wd(_add_wd(slf, -lag_d), dur_d)
            else:  # Finish to Start
                cand = _add_wd(sls, -lag_d)
            if cand < lf_date:
                lf_date = cand

        ls_date = _add_wd(lf_date, -dur_d) if dur_d > 0 else lf_date
        ls[uid] = ls_date
        lf[uid] = lf_date

    # ── Write results back to Activity objects ───────────────────────────────
    for act in project.activities:
        uid = act.uid
        es_d = es.get(uid)
        ef_d = ef.get(uid)
        ls_d = ls.get(uid)
        lf_d = lf.get(uid)

        if es_d is None:
            continue

        act.early_start  = es_d.isoformat()
        act.early_finish = ef_d.isoformat() if ef_d else es_d.isoformat()
        if ls_d:
            act.late_start  = ls_d.isoformat()
        if lf_d:
            act.late_finish = lf_d.isoformat()

        # Total float (working days → hours)
        if ls_d and es_d:
            float_days = _wd_between(es_d, ls_d)
            act.total_float = float_days * hpd
            act.is_critical = act.total_float <= 0

        # Update planned_start / planned_finish to match P6 "Start" / "Finish":
        #   Completed   → actual dates
        #   In Progress → actual start / projected finish (EF)
        #   Not Started → early start / early finish
        if act.status == "Completed":
            if act.actual_start:
                act.planned_start = str(act.actual_start)[:10]
            if act.actual_finish:
                act.planned_finish = str(act.actual_finish)[:10]
        elif act.status == "In Progress":
            if act.actual_start:
                act.planned_start = str(act.actual_start)[:10]
            act.planned_finish = ef_d.isoformat() if ef_d else None
        else:
            act.planned_start  = es_d.isoformat()
            act.planned_finish = ef_d.isoformat() if ef_d else es_d.isoformat()
