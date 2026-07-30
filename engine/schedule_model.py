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
    # Working pattern. Defaults reproduce the previous hard-coded behaviour
    # (Mon-Fri, works through holidays) so existing schedules are unaffected.
    work_days: frozenset = field(default_factory=lambda: frozenset({0, 1, 2, 3, 4}))
    holidays: frozenset = field(default_factory=frozenset)   # ISO date strings


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
    planned_labor_units: float = 0.0       # Budgeted Labor Units (BLU)


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

    def llm_context(self, max_activities: int = 3000,
                    compact_above: int = 400) -> str:
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

        # Per-WBS risk rollup, keyed by UID.
        # Folder NAMES repeat constantly in real schedules — every building has a
        # "Level 1", every level an "ER 209" — so keying by name merged unrelated
        # folders and reported counts that belonged to neither.
        wbs_risk: dict = {}  # wbs_uid -> {total, crit, open_s, open_f}  (direct)
        for a in task_acts:
            wbs = wbs_map.get(a.wbs_uid)
            key = wbs.uid if wbs else "__none__"
            r   = wbs_risk.setdefault(key, {"total": 0, "crit": 0, "open_s": 0, "open_f": 0})
            r["total"] += 1
            if is_critical(a):   r["crit"]   += 1
            if not preds_of.get(a.uid): r["open_s"] += 1
            if not succs_of.get(a.uid): r["open_f"] += 1

        # Depth + child map, so the tree can be printed at its real shape.
        _children: dict = {}
        for w in self.wbs_nodes:
            _children.setdefault(w.parent_uid, []).append(w)

        def _depth(w):
            d, cur, guard = 0, w.parent_uid, 0
            while cur and cur in wbs_map and guard < 200:
                d += 1
                cur = wbs_map[cur].parent_uid
                guard += 1
            return d

        # A "rollup" must include descendants, otherwise a parent folder whose
        # work all sits in sub-folders reads as empty.
        _rollup_cache: dict = {}

        def _rollup(uid):
            if uid in _rollup_cache:
                return _rollup_cache[uid]
            tot = dict(wbs_risk.get(uid, {"total": 0, "crit": 0, "open_s": 0, "open_f": 0}))
            _rollup_cache[uid] = tot           # guard against a cyclic parent chain
            for c in _children.get(uid, []):
                sub = _rollup(c.uid)
                for k in tot:
                    tot[k] += sub[k]
            _rollup_cache[uid] = tot
            return tot

        def _wbs_path(w):
            parts, cur, guard = [], w, 0
            while cur and guard < 200:
                parts.insert(0, cur.name)
                cur = wbs_map.get(cur.parent_uid)
                guard += 1
            return " / ".join(parts)

        # A folder name that occurs more than once is ambiguous on its own —
        # "WBS: Level 1" could be any building. Those get their full path so the
        # agent can tell them apart (and so an edit it proposes targets the right
        # one); unique names stay short to save context.
        _name_counts: dict = {}
        for w in self.wbs_nodes:
            _name_counts[w.name] = _name_counts.get(w.name, 0) + 1
        _label_cache: dict = {}

        def _wbs_label(w):
            if w is None:
                return "?"
            if w.uid not in _label_cache:
                _label_cache[w.uid] = (_wbs_path(w) if _name_counts.get(w.name, 0) > 1
                                       else w.name)
            return _label_cache[w.uid]

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

        # Print the tree at its true depth, parents before children, so the
        # nesting is actually readable. Two fixed indents made a 5-deep
        # hierarchy look completely flat.
        def _emit(w):
            d = _depth(w)
            direct = wbs_risk.get(w.uid, {"total": 0, "crit": 0, "open_s": 0, "open_f": 0})
            roll = _rollup(w.uid)
            note = ""
            if roll["total"]:
                crit_pct = round(roll["crit"] / roll["total"] * 100)
                note = f"  [{roll['total']} acts"
                if direct["total"] != roll["total"]:
                    note += f" ({direct['total']} direct)"
                note += (f" | {crit_pct}% crit"
                         f" | open_s:{roll['open_s']} open_f:{roll['open_f']}]")
            lines.append(f"{'  ' * (d + 1)}{w.code} — {w.name}{note}")
            for c in sorted(_children.get(w.uid, []), key=lambda x: x.sequence_num):
                _emit(c)

        for w in sorted([x for x in self.wbs_nodes
                         if not x.parent_uid or x.parent_uid not in wbs_map],
                        key=lambda x: x.sequence_num):
            _emit(w)

        # ── WBS Phase Sequence ───────────────────────────────────────────────
        # Build parent → children map, sorted by sequence_num
        _parent_to_children: dict = {}
        _top_level: list = []
        for w in self.wbs_nodes:
            if w.parent_uid is None or w.parent_uid not in wbs_map:
                _top_level.append(w)
            else:
                _parent_to_children.setdefault(w.parent_uid, []).append(w)
        _top_level.sort(key=lambda x: x.sequence_num)
        for _v in _parent_to_children.values():
            _v.sort(key=lambda x: x.sequence_num)

        lines.append("")
        lines.append("WBS PHASE SEQUENCE (this project's actual phase order — authoritative phase map):")
        if _top_level:
            _flow_parts = [f"{i+1}. {w.name}" for i, w in enumerate(_top_level)]
            lines.append("  Phase flow: " + " → ".join(_flow_parts))
            for i, w in enumerate(_top_level):
                _children = _parent_to_children.get(w.uid, [])
                if _children:
                    _sub = [f"{chr(97+j)}. {c.name}" for j, c in enumerate(_children)]
                    lines.append(f"    Phase {i+1} ({w.name}) → " + " → ".join(_sub))
        else:
            lines.append("  (no WBS defined)")

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
                    lines.append(f"    {a.activity_id} — {a.name}  [{_wbs_label(wbs)}]")
            if open_finish_acts:
                lines.append("  NO SUCCESSOR (open finish):")
                for a in open_finish_acts[:30]:
                    wbs = wbs_map.get(a.wbs_uid)
                    lines.append(f"    {a.activity_id} — {a.name}  [{_wbs_label(wbs)}]")

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
        # Two-tier strategy:
        #   ≤400 activities  → full format (per-line pred/succ, flags, float)
        #   >400 activities  → compact WBS-grouped format (scales to 3000+)
        #     Each activity: "A1000 Name | 5d | NS | C" (~45 chars vs ~120)
        #     Relations listed separately in a compact block at the end.
        total_acts = len(self.activities)
        lines.append("")
        lines.append(f"ACTIVITIES ({total_acts} total):")

        # `compact_above` picks the FORMAT; `max_activities` is a separate hard
        # cap. Using one number for both meant the compact format only engaged
        # past 3000, so a 2999-activity schedule cost roughly twice the context
        # of a 3001-activity one.
        if total_acts <= compact_above:
            # ── Full format (small schedules) ───────────────────────────────
            for a in self.activities:
                wbs      = wbs_map.get(a.wbs_uid)
                wbs_name = _wbs_label(wbs)
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
        else:
            # ── Compact format (>400, scales to 3000+) ─────────────────────
            # Group activities by WBS, one line per activity but much shorter.
            # Abbreviations: NS=Not Started, IP=In Progress, CP=Completed
            #                C=Critical, NC=Near-Critical, MS=Milestone
            _STATUS_ABBR = {"Not Started": "NS", "In Progress": "IP", "Completed": "CP"}
            _MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}

            # Build WBS → activities map (preserve WBS order from self.wbs_nodes)
            _wbs_acts: dict = {}
            _acts_no_wbs: list = []
            for a in self.activities:
                w = wbs_map.get(a.wbs_uid)
                if w:
                    _wbs_acts.setdefault(w.uid, []).append(a)
                else:
                    _acts_no_wbs.append(a)

            for w in self.wbs_nodes:
                acts = _wbs_acts.get(w.uid)
                if not acts:
                    continue
                lines.append(f"  [{w.code} — {_wbs_label(w)}] ({len(acts)} acts)")
                for a in acts:
                    dur = f"{a.planned_duration / 8:.0f}d" if a.planned_duration else "0d"
                    st = _STATUS_ABBR.get(a.status, a.status[:2])
                    fh = float_hrs(a)
                    if fh is None:
                        ft = ""
                    elif fh <= 0:
                        ft = " C"
                    elif fh <= 80:
                        ft = " NC"
                    else:
                        ft = ""
                    ms = " MS" if a.activity_type in _MILESTONE_TYPES else ""
                    lines.append(f"    {a.activity_id} {a.name} | {dur} | {st}{ft}{ms}")

            if _acts_no_wbs:
                lines.append(f"  [No WBS] ({len(_acts_no_wbs)} acts)")
                for a in _acts_no_wbs:
                    dur = f"{a.planned_duration / 8:.0f}d" if a.planned_duration else "0d"
                    st = _STATUS_ABBR.get(a.status, a.status[:2])
                    lines.append(f"    {a.activity_id} {a.name} | {dur} | {st}")

            # ── Compact relations block (only for large schedules) ──────────
            # Instead of per-activity pred/succ, list relations as
            # "A1000→A1010 FS" — much denser than embedding in each activity line.
            lines.append("")
            lines.append(f"RELATIONSHIPS ({len(self.relations)} total, compact):")
            for rel in self.relations:
                p = act_by_uid.get(rel.predecessor_uid)
                s = act_by_uid.get(rel.successor_uid)
                if not p or not s:
                    continue
                rt = rel.type
                abbr = ("FS" if "Finish to Start" in rt else
                        "SS" if "Start to Start"  in rt else
                        "FF" if "Finish to Finish" in rt else "SF")
                lag = ""
                if rel.lag and rel.lag != 0:
                    ld = rel.lag / 8.0
                    lag = f"+{ld:.0f}d" if ld > 0 else f"{ld:.0f}d"
                lines.append(f"  {p.activity_id}→{s.activity_id} {abbr}{lag}")

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
    Weekends and holidays come from each activity's calendar.
    Completed activities are anchored to their actual dates (not recomputed).
    """
    from datetime import date as _date, timedelta as _td
    import math as _math

    if not project.activities:
        return

    # ── Calendars ───────────────────────────────────────────────────────────
    # Each activity is scheduled on its own calendar's working pattern. The
    # defaults on Calendar reproduce the previous Mon-Fri / no-holiday
    # behaviour, so schedules that never pick a calendar are unaffected.
    _DEFAULT_WD = frozenset({0, 1, 2, 3, 4})
    cal_by_uid = {c.uid: c for c in (project.calendars or [])}
    default_cal = project.calendars[0] if project.calendars else None

    def _wd_of(cal):
        wd = getattr(cal, "work_days", None) if cal else None
        return wd if wd else _DEFAULT_WD

    def _hol_of(cal):
        return (getattr(cal, "holidays", None) if cal else None) or frozenset()

    def _hpd_of(cal):
        return (getattr(cal, "hours_per_day", None) if cal else None) or 8.0

    def _cal_of(act):
        return cal_by_uid.get(getattr(act, "calendar_uid", None)) or default_cal

    hpd: float = _hpd_of(default_cal)                 # project-level fallback
    base_wd, base_hol = _wd_of(default_cal), _hol_of(default_cal)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _parse(s) -> Optional[_date]:
        if not s:
            return None
        try:
            return _date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None

    def _is_work(d: _date, wd, hol) -> bool:
        # Short-circuit the weekday test first, and skip the isoformat() string
        # build entirely when the calendar has no holidays (the default) — that
        # formatting was the dominant per-day cost in the day-by-day walk.
        if d.weekday() not in wd:
            return False
        return (not hol) or (d.isoformat() not in hol)

    def _snap(d: _date, wd, hol) -> _date:
        """Advance to the next working day on this calendar."""
        while not _is_work(d, wd, hol):
            d += _td(days=1)
        return d

    def _add_wd(start: _date, days: float, wd, hol) -> _date:
        """Add working days on this calendar. Negative days goes backward."""
        d = _snap(start, wd, hol)
        if days == 0:
            return d
        step = 1 if days > 0 else -1
        remaining = abs(int(_math.ceil(abs(days))))
        added = 0
        while added < remaining:
            d += _td(days=step)
            if _is_work(d, wd, hol):
                added += 1
        return d

    def _wd_between(d1: _date, d2: _date, wd, hol) -> float:
        """Working-day count from d1 to d2 (positive when d2 > d1)."""
        if d2 == d1:
            return 0.0
        sign = 1 if d2 > d1 else -1
        count = 0
        d, end = min(d1, d2), max(d1, d2)
        while d < end:
            d += _td(days=1)
            if _is_work(d, wd, hol):
                count += 1
        return sign * float(count)

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
    origin = _snap(origin, base_wd, base_hol)

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

        _cal = _cal_of(act)
        wd, hol, a_hpd = _wd_of(_cal), _hol_of(_cal), _hpd_of(_cal)
        is_ms = act.activity_type in MILESTONE_TYPES
        dur_d = 0.0 if is_ms else (act.planned_duration or 0.0) / a_hpd

        # Completed → anchor to actual dates
        if act.status == "Completed" and act.actual_start and act.actual_finish:
            es[uid] = _parse(act.actual_start) or origin
            ef[uid] = _parse(act.actual_finish) or origin
            continue

        # In-progress → actual start is fixed
        if act.status == "In Progress" and act.actual_start:
            es_date = _snap(_parse(act.actual_start) or origin, wd, hol)
        else:
            # Derive ES from predecessors
            es_date = origin
            for p_uid, rel_type, lag_d in preds[uid]:
                pef = ef.get(p_uid, origin)
                pes = es.get(p_uid, origin)
                if "Start to Start" in rel_type:
                    cand = _add_wd(pes, lag_d, wd, hol)
                elif "Finish to Finish" in rel_type:
                    cand = _add_wd(_add_wd(pef, lag_d), -dur_d, wd, hol)
                elif "Start to Finish" in rel_type:
                    cand = _add_wd(pes, lag_d - dur_d, wd, hol)
                else:  # Finish to Start (default)
                    cand = _add_wd(pef, lag_d, wd, hol)
                if cand > es_date:
                    es_date = cand
            es_date = _snap(es_date, wd, hol)

            # Hard / soft constraints on start
            ct = act.constraint_type or ""
            cd = _parse(act.constraint_date)
            if ct in ("Must Start On", "Start On") and cd:
                es_date = _snap(cd, wd, hol)
            elif ct in ("Start On Or After", "Start On Or Before") and cd:
                if ct == "Start On Or After" and cd > es_date:
                    es_date = _snap(cd, wd, hol)

        ef_date = _add_wd(es_date, dur_d, wd, hol) if dur_d > 0 else es_date

        # Hard constraints on finish
        ct = act.constraint_type or ""
        cd = _parse(act.constraint_date)
        if ct in ("Must Finish On", "Finish On") and cd:
            ef_date = _snap(cd, wd, hol)
            es_date = _add_wd(ef_date, -dur_d, wd, hol) if dur_d > 0 else ef_date
        elif ct == "Finish On Or Before" and cd and cd < ef_date:
            ef_date = _snap(cd, wd, hol)

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
        _cal = _cal_of(act)
        wd, hol, a_hpd = _wd_of(_cal), _hol_of(_cal), _hpd_of(_cal)
        is_ms = act.activity_type in MILESTONE_TYPES
        dur_d = 0.0 if is_ms else (act.planned_duration or 0.0) / a_hpd

        if act.status == "Completed":
            ls[uid] = es.get(uid, origin)
            lf[uid] = ef.get(uid, origin)
            continue

        lf_date = project_lf
        for s_uid, rel_type, lag_d in succs.get(uid, []):
            sls = ls.get(s_uid, project_lf)
            slf = lf.get(s_uid, project_lf)
            if "Start to Start" in rel_type:
                cand = _add_wd(_add_wd(sls, -lag_d), dur_d, wd, hol)
            elif "Finish to Finish" in rel_type:
                cand = _add_wd(slf, -lag_d, wd, hol)
            elif "Start to Finish" in rel_type:
                cand = _add_wd(_add_wd(slf, -lag_d), dur_d, wd, hol)
            else:  # Finish to Start
                cand = _add_wd(sls, -lag_d, wd, hol)
            if cand < lf_date:
                lf_date = cand

        ls_date = _add_wd(lf_date, -dur_d, wd, hol) if dur_d > 0 else lf_date
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

        # Total float (working days → hours) on the activity's own calendar
        _cal = _cal_of(act)
        if ls_d and es_d:
            float_days = _wd_between(es_d, ls_d, _wd_of(_cal), _hol_of(_cal))
            act.total_float = float_days * _hpd_of(_cal)
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
