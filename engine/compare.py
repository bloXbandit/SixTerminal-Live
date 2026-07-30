# -*- coding: utf-8 -*-
"""
compare.py — Schedule comparison and cross-schedule branch copy.

Provides:
  compare_projects(proj_a, proj_b) -> dict
      Read-only diff of two schedules: added / removed / changed activities,
      WBS structure differences, grouped by WBS for the compare view.

  copy_wbs_branch(src_project, src_wbs_code, tgt_project, ...) -> (bool, str, dict)
      Copy a WBS branch (folder + descendants + activities + internal logic)
      from one project into another. Boundary logic (links to activities
      outside the branch) is dropped and reported as open ends — not silently
      carried, not silently lost.
"""

import uuid
from typing import Dict, Any, List, Optional, Tuple, Set
from .schedule_model import Project, Activity, Relation, WBSNode


# ──────────────────────────────────────────────────────────────────────────────
# Schedule comparison
# ──────────────────────────────────────────────────────────────────────────────

_FIELDS_TO_DIFF = [
    ("name",              "Name"),
    ("planned_duration",  "Duration"),
    ("planned_start",     "Start"),
    ("planned_finish",    "Finish"),
    ("actual_start",      "Actual Start"),
    ("actual_finish",     "Actual Finish"),
    ("status",            "Status"),
    ("percent_complete",  "% Complete"),
    ("activity_type",     "Type"),
    ("constraint_type",   "Constraint"),
    ("constraint_date",   "Constraint Date"),
]


def _fmt_dur(h: Optional[float]) -> str:
    if h is None:
        return ""
    return f"{h / 8.0:.0f}d"


def _fmt_val(field: str, act: Activity) -> str:
    v = getattr(act, field, None)
    if v is None or v == "":
        return ""
    if field == "planned_duration":
        return _fmt_dur(v)
    if field == "percent_complete":
        return f"{v:.0f}%"
    return str(v)


def _wbs_path(project: Project, wbs_uid: str) -> str:
    wbs_map = {w.uid: w for w in project.wbs_nodes}
    parts = []
    uid = wbs_uid
    seen: Set[str] = set()
    while uid and uid not in seen:
        seen.add(uid)
        node = wbs_map.get(uid)
        if not node:
            break
        parts.insert(0, node.name)
        uid = node.parent_uid
    return " / ".join(parts) if parts else "(root)"


def _wbs_tree_map(project: Project) -> Dict[str, Dict[str, Any]]:
    """code -> {node, children: [codes]} for quick lookup."""
    by_code: Dict[str, Dict[str, Any]] = {}
    for w in project.wbs_nodes:
        by_code[w.code] = {"node": w, "children": []}
    for w in project.wbs_nodes:
        if w.parent_uid:
            parent = next((n for n in project.wbs_nodes if n.uid == w.parent_uid), None)
            if parent and parent.code in by_code:
                by_code[parent.code]["children"].append(w.code)
    return by_code


def compare_projects(proj_a: Project, proj_b: Project) -> Dict[str, Any]:
    """
    Compare two schedules and return a structured diff.

    Matching strategy:
      1. Exact activity_id match (P6 IDs are stable across updates)
      2. Fallback: same name + same WBS name

    Returns:
      {
        "summary": {added, removed, changed, unchanged, total_a, total_b},
        "wbs_diff": {added: [...], removed: [...]},
        "sections": [  # grouped by WBS (using proj_b's structure)
          {
            "wbs_code", "wbs_name", "wbs_path",
            "added": [...], "removed": [...],
            "changed": [{activity_id, name, field, from, to}, ...],
            "unchanged_count": int,
          }
        ]
      }
    """
    # ── Activity matching ──────────────────────────────────────────────────
    acts_a_by_id = {a.activity_id: a for a in proj_a.activities}
    acts_b_by_id = {a.activity_id: a for a in proj_b.activities}

    # First pass: exact ID match
    matched: Dict[str, Tuple[Activity, Activity]] = {}  # activity_id -> (a, b)
    consumed_a: Set[str] = set()
    consumed_b: Set[str] = set()
    for aid, a_act in acts_a_by_id.items():
        if aid in acts_b_by_id:
            matched[aid] = (a_act, acts_b_by_id[aid])
            consumed_a.add(aid)
            consumed_b.add(aid)

    # Second pass: fuzzy match by name + WBS name for unmatched
    wbs_a_map = {w.uid: w for w in proj_a.wbs_nodes}
    wbs_b_map = {w.uid: w for w in proj_b.wbs_nodes}
    remaining_a = [a for a in proj_a.activities if a.activity_id not in consumed_a]
    remaining_b = [a for a in proj_b.activities if a.activity_id not in consumed_b]
    for a_act in remaining_a:
        a_wbs = wbs_a_map.get(a_act.wbs_uid)
        a_wbs_name = a_wbs.name if a_wbs else ""
        for b_act in list(remaining_b):
            if b_act.activity_id in consumed_b:
                continue
            b_wbs = wbs_b_map.get(b_act.wbs_uid)
            b_wbs_name = b_wbs.name if b_wbs else ""
            if a_act.name.lower() == b_act.name.lower() and a_wbs_name.lower() == b_wbs_name.lower():
                matched[a_act.activity_id] = (a_act, b_act)
                consumed_a.add(a_act.activity_id)
                consumed_b.add(b_act.activity_id)
                remaining_b.remove(b_act)
                break

    added = [a for a in proj_b.activities if a.activity_id not in consumed_b]
    removed = [a for a in proj_a.activities if a.activity_id not in consumed_a]

    # ── Field-level diff for matched activities ─────────────────────────────
    changed_list: List[Dict[str, Any]] = []
    unchanged_count = 0
    for aid, (a_act, b_act) in matched.items():
        diffs: List[Dict[str, str]] = []
        for field, label in _FIELDS_TO_DIFF:
            va = _fmt_val(field, a_act)
            vb = _fmt_val(field, b_act)
            if va != vb:
                diffs.append({"field": label, "attr": field, "from": va, "to": vb})
        if diffs:
            changed_list.append({
                "activity_id": aid,
                "name": b_act.name,
                "wbs_uid": b_act.wbs_uid,
                "changes": diffs,
            })
            # remember the attr names so the UI can apply individual fields
            changed_list[-1]["attrs"] = [f for f, _ in _FIELDS_TO_DIFF
                                         if _fmt_val(f, a_act) != _fmt_val(f, b_act)]
        else:
            unchanged_count += 1

    # ── WBS diff ────────────────────────────────────────────────────────────
    wbs_a_codes = {w.code for w in proj_a.wbs_nodes}
    wbs_b_codes = {w.code for w in proj_b.wbs_nodes}
    wbs_added = [{"code": w.code, "name": w.name}
                 for w in proj_b.wbs_nodes if w.code not in wbs_a_codes]
    wbs_removed = [{"code": w.code, "name": w.name}
                   for w in proj_a.wbs_nodes if w.code not in wbs_b_codes]

    # ── Group by WBS (using proj_b's structure) ─────────────────────────────
    # Build WBS sections from proj_b, then attach added/changed/removed
    sections: Dict[str, Dict[str, Any]] = {}
    for w in proj_b.wbs_nodes:
        sections[w.uid] = {
            "wbs_uid": w.uid,
            "wbs_code": w.code,
            "wbs_name": w.name,
            "wbs_path": _wbs_path(proj_b, w.uid),
            "added": [],
            "removed": [],
            "changed": [],
            "unchanged_count": 0,
        }

    # Added activities → their WBS in proj_b
    for a in added:
        sec = sections.get(a.wbs_uid)
        if sec:
            sec["added"].append({
                "activity_id": a.activity_id,
                "name": a.name,
                "duration": _fmt_dur(a.planned_duration),
                "start": a.planned_start or "",
                "finish": a.planned_finish or "",
                "status": a.status,
            })

    # Changed activities → their WBS in proj_b
    for c in changed_list:
        sec = sections.get(c["wbs_uid"])
        if sec:
            sec["changed"].append(c)

    # Unchanged count per WBS
    for aid, (a_act, b_act) in matched.items():
        diffs = []
        for field, label in _FIELDS_TO_DIFF:
            if _fmt_val(field, a_act) != _fmt_val(field, b_act):
                diffs.append(field)
        if not diffs:
            sec = sections.get(b_act.wbs_uid)
            if sec:
                sec["unchanged_count"] += 1

    # Removed activities → try to find their WBS in proj_b by code match,
    # otherwise put in a "removed" catch-all section
    wbs_a_by_uid = {w.uid: w for w in proj_a.wbs_nodes}
    wbs_b_by_code = {w.code: w for w in proj_b.wbs_nodes}
    for a in removed:
        a_wbs = wbs_a_by_uid.get(a.wbs_uid)
        a_wbs_code = a_wbs.code if a_wbs else ""
        b_wbs = wbs_b_by_code.get(a_wbs_code) if a_wbs_code else None
        sec = sections.get(b_wbs.uid) if b_wbs else None
        if sec:
            sec["removed"].append({
                "activity_id": a.activity_id,
                "name": a.name,
                "duration": _fmt_dur(a.planned_duration),
                "start": a.planned_start or "",
                "finish": a.planned_finish or "",
                "status": a.status,
            })
        else:
            # No matching WBS in proj_b — create a virtual section
            virt_key = f"_removed_{a_wbs_code}"
            if virt_key not in sections:
                sections[virt_key] = {
                    "wbs_uid": virt_key,
                    "wbs_code": a_wbs_code or "(removed)",
                    "wbs_name": (a_wbs.name if a_wbs else "Removed WBS"),
                    "wbs_path": "(no longer exists in target)",
                    "added": [],
                    "removed": [],
                    "changed": [],
                    "unchanged_count": 0,
                }
            sections[virt_key]["removed"].append({
                "activity_id": a.activity_id,
                "name": a.name,
                "duration": _fmt_dur(a.planned_duration),
                "start": a.planned_start or "",
                "finish": a.planned_finish or "",
                "status": a.status,
            })

    # Only include sections that have content
    section_list = [s for s in sections.values()
                    if s["added"] or s["removed"] or s["changed"] or s["unchanged_count"] > 0]
    # Sort: WBS path order
    section_list.sort(key=lambda s: s["wbs_path"])

    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed_list),
            "unchanged": unchanged_count,
            "total_a": len(proj_a.activities),
            "total_b": len(proj_b.activities),
            "wbs_added": len(wbs_added),
            "wbs_removed": len(wbs_removed),
        },
        "wbs_diff": {"added": wbs_added, "removed": wbs_removed},
        "sections": section_list,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Cross-schedule branch copy
# ──────────────────────────────────────────────────────────────────────────────

def _new_uid() -> str:
    return str(uuid.uuid4().int)[:10]


def _collect_branch(project: Project, root_wbs: WBSNode) -> Tuple[List[WBSNode], List[Activity]]:
    """Collect a WBS node + all descendants + activities in them."""
    children_of: Dict[str, List[WBSNode]] = {}
    for w in project.wbs_nodes:
        children_of.setdefault(w.parent_uid, []).append(w)
    branch: List[WBSNode] = []

    def collect(node: WBSNode):
        branch.append(node)
        for c in children_of.get(node.uid, []):
            collect(c)

    collect(root_wbs)
    branch_uids = {w.uid for w in branch}
    acts_in = [a for a in project.activities if a.wbs_uid in branch_uids]
    return branch, acts_in


def copy_wbs_branch(
    src_project: Project,
    src_wbs_code: str,
    tgt_project: Project,
    tgt_parent_code: Optional[str] = None,
    id_mode: str = "renumber",
    new_wbs_name: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Copy a WBS branch from src_project into tgt_project.

    Parameters:
      src_project     — the schedule to copy FROM
      src_wbs_code    — the WBS code of the branch root in the source
      tgt_project     — the schedule to copy INTO (mutated in place)
      tgt_parent_code — WBS code in target to nest under (None = root)
      id_mode         — "renumber" (auto-assign new IDs) or "keep" (preserve
                        original IDs, fail on collision)
      new_wbs_name    — override the root WBS name (default: keep source name)

    Returns:
      (success, message, detail_dict)
      detail_dict includes:
        wbs_copied, activities_copied, relations_copied,
        boundary_links_dropped: [{from_id, to_id, type, outside_id}]
    """
    # ── Find source branch root ─────────────────────────────────────────────
    src_wbs = None
    for w in src_project.wbs_nodes:
        if w.code.lower() == src_wbs_code.lower():
            src_wbs = w
            break
    if not src_wbs:
        return False, f"WBS '{src_wbs_code}' not found in source schedule", {}

    # ── Find target parent ──────────────────────────────────────────────────
    tgt_parent_uid = None
    if tgt_parent_code:
        for w in tgt_project.wbs_nodes:
            if w.code.lower() == tgt_parent_code.lower():
                tgt_parent_uid = w.uid
                break
        if not tgt_parent_uid:
            return False, f"Target parent WBS '{tgt_parent_code}' not found", {}

    # ── Collect branch + activities ─────────────────────────────────────────
    branch, acts_in = _collect_branch(src_project, src_wbs)
    if not acts_in:
        return False, f"WBS '{src_wbs.name}' has no activities to copy", {}

    # ── Collect relations: internal vs boundary ─────────────────────────────
    branch_act_uids = {a.uid for a in acts_in}
    internal_rels: List[Relation] = []
    boundary_rels: List[Relation] = []
    for r in src_project.relations:
        p_in = r.predecessor_uid in branch_act_uids
        s_in = r.successor_uid in branch_act_uids
        if p_in and s_in:
            internal_rels.append(r)
        elif p_in or s_in:
            boundary_rels.append(r)

    # ── Build boundary link report ──────────────────────────────────────────
    src_act_by_uid = {a.uid: a for a in src_project.activities}
    boundary_dropped: List[Dict[str, str]] = []
    for r in boundary_rels:
        p_act = src_act_by_uid.get(r.predecessor_uid)
        s_act = src_act_by_uid.get(r.successor_uid)
        if p_act and s_act:
            if r.predecessor_uid not in branch_act_uids:
                # predecessor is outside, successor is inside
                boundary_dropped.append({
                    "inside_id": s_act.activity_id,
                    "inside_name": s_act.name,
                    "outside_id": p_act.activity_id,
                    "outside_name": p_act.name,
                    "direction": "predecessor",
                    "type": r.type,
                })
            else:
                # predecessor is inside, successor is outside
                boundary_dropped.append({
                    "inside_id": p_act.activity_id,
                    "inside_name": p_act.name,
                    "outside_id": s_act.activity_id,
                    "outside_name": s_act.name,
                    "direction": "successor",
                    "type": r.type,
                })

    # ── Copy WBS nodes ──────────────────────────────────────────────────────
    wbs_uid_map: Dict[str, str] = {}  # src uid -> new uid
    for w in branch:
        new_uid = _new_uid()
        wbs_uid_map[w.uid] = new_uid
        is_root = (w.uid == src_wbs.uid)
        parent_for_new = tgt_parent_uid if is_root else wbs_uid_map.get(w.parent_uid)
        # Sequence: place after last sibling in target
        siblings = [x for x in tgt_project.wbs_nodes
                    if x.parent_uid == parent_for_new]
        next_seq = (max(s.sequence_num for s in siblings) + 10) if siblings else 0
        new_name_val = (new_wbs_name or w.name) if is_root else w.name
        tgt_project.wbs_nodes.append(WBSNode(
            uid=new_uid,
            name=new_name_val,
            code=w.code,
            parent_uid=parent_for_new,
            sequence_num=next_seq,
        ))
    tgt_project.build_lookups()

    # ── Map calendars (match by name, else use target's first) ──────────────
    tgt_cal_by_name = {c.name: c.uid for c in tgt_project.calendars}
    default_cal_uid = tgt_project.calendars[0].uid if tgt_project.calendars else "1"
    src_cal_by_uid = {c.uid: c for c in src_project.calendars}

    # ── Copy activities ─────────────────────────────────────────────────────
    act_uid_map: Dict[str, str] = {}  # src uid -> new uid
    existing_ids = {a.activity_id for a in tgt_project.activities}

    # Figure out renumbering scheme from target project
    import re as _re
    pat = _re.compile(r"^(.*?)(\d+)$")
    parsed: List[Tuple[str, int, int]] = []
    counts: Dict[str, int] = {}
    for a in tgt_project.activities:
        m = pat.match((a.activity_id or "").strip())
        if not m:
            continue
        pre, digits = m.group(1), m.group(2)
        counts[pre] = counts.get(pre, 0) + 1
        parsed.append((pre, int(digits), len(digits)))

    if parsed:
        dominant_prefix = max(counts, key=counts.get)
        same = [(n, w) for (p, n, w) in parsed if p == dominant_prefix]
        next_num = ((max(n for n, _ in same) // 10) + 1) * 10
        id_width = max(w for _, w in same)
    else:
        dominant_prefix, next_num, id_width = "A", 1000, 4

    for a in acts_in:
        new_uid = _new_uid()
        act_uid_map[a.uid] = new_uid

        if id_mode == "keep":
            new_id = a.activity_id
            if new_id in existing_ids:
                tgt_project.build_lookups()
                return False, (
                    f"Activity ID '{new_id}' already exists in target. "
                    f"Use id_mode='renumber' to auto-assign new IDs."
                ), {}
        else:  # renumber
            new_id = f"{dominant_prefix}{next_num:0{id_width}d}"
            while new_id in existing_ids:
                next_num += 10
                new_id = f"{dominant_prefix}{next_num:0{id_width}d}"
            next_num += 10

        existing_ids.add(new_id)

        # Map calendar
        src_cal = src_cal_by_uid.get(a.calendar_uid)
        cal_uid = tgt_cal_by_name.get(src_cal.name, default_cal_uid) if src_cal else default_cal_uid

        tgt_project.activities.append(Activity(
            uid=new_uid,
            activity_id=new_id,
            name=a.name,
            wbs_uid=wbs_uid_map.get(a.wbs_uid, tgt_parent_uid or ""),
            calendar_uid=cal_uid,
            activity_type=a.activity_type,
            status="Not Started",
            planned_duration=a.planned_duration,
            remaining_duration=a.planned_duration,
            constraint_type=a.constraint_type,
            constraint_date=a.constraint_date,
        ))

    tgt_project.build_lookups()

    # ── Copy internal relations ─────────────────────────────────────────────
    rels_copied = 0
    for r in internal_rels:
        new_pred = act_uid_map.get(r.predecessor_uid)
        new_succ = act_uid_map.get(r.successor_uid)
        if not new_pred or not new_succ:
            continue
        tgt_project.relations.append(Relation(
            uid=_new_uid(),
            predecessor_uid=new_pred,
            successor_uid=new_succ,
            type=r.type,
            lag=r.lag,
        ))
        rels_copied += 1

    # ── Recompute dates ─────────────────────────────────────────────────────
    from .schedule_model import compute_dates
    try:
        compute_dates(tgt_project)
    except Exception:
        pass

    detail = {
        "wbs_copied": len(branch),
        "activities_copied": len(acts_in),
        "relations_copied": rels_copied,
        "boundary_links_dropped": boundary_dropped,
    }

    msg = (f"Copied '{src_wbs.name}' ({len(branch)} WBS nodes, "
           f"{len(acts_in)} activities, {rels_copied} internal relations)")
    if boundary_dropped:
        msg += f" — {len(boundary_dropped)} boundary link(s) dropped (open ends)"

    return True, msg, detail


# ──────────────────────────────────────────────────────────────────────────────
# Feature C — seam-preserving replace-in-place
# ──────────────────────────────────────────────────────────────────────────────

def replace_wbs_branch(
    src_project: Project,
    src_wbs_code: str,
    tgt_project: Project,
    tgt_wbs_code: str,
    id_mode: str = "keep",
    match: str = "id",
    new_wbs_name: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Replace an existing WBS branch in the target with the source branch, keeping
    the surrounding logic (the "seam") intact wherever it can be reconnected.

    How it differs from copy_wbs_branch:
      • copy  → APPENDS the source branch as a new folder; boundary logic dropped.
      • replace → DELETES the target's version of the branch, drops the source
        branch in the same place, and RE-STITCHES the target's boundary links
        (predecessors feeding in, successors fed from it) onto the incoming
        activities. Seams that can't be matched dead-end as open ends and are
        reported — nothing is silently rewired to the wrong activity.

    Matching of a seam's inside endpoint to an incoming activity:
      match="id"   → by activity_id (natural when id_mode="keep" and the source
                     is an updated version of the same section)
      match="name" → by activity name (use when IDs were renumbered)

    Returns (success, message, detail) where detail includes:
      wbs_removed, activities_removed, activities_copied, relations_copied,
      seams_reconnected, seams_dropped:[{outside_id, inside_id, direction, type}],
      boundary_links_dropped (from the source side, same shape as copy).
    """
    # ── Validate BOTH ends before mutating anything ─────────────────────────
    src_wbs = next((w for w in src_project.wbs_nodes
                    if w.code.lower() == src_wbs_code.lower()), None)
    if not src_wbs:
        return False, f"WBS '{src_wbs_code}' not found in source schedule", {}

    tgt_wbs = next((w for w in tgt_project.wbs_nodes
                    if w.code.lower() == tgt_wbs_code.lower()), None)
    if not tgt_wbs:
        return False, f"WBS '{tgt_wbs_code}' not found in target schedule", {}

    _, src_acts = _collect_branch(src_project, src_wbs)
    if not src_acts:
        return False, f"Source WBS '{src_wbs.name}' has no activities to copy", {}

    # Parent code of the target branch — the source drops into the same slot.
    tgt_parent_uid = tgt_wbs.parent_uid
    tgt_parent_code = None
    if tgt_parent_uid:
        p = next((w for w in tgt_project.wbs_nodes if w.uid == tgt_parent_uid), None)
        tgt_parent_code = p.code if p else None

    # ── Record the target branch + its seams BEFORE deleting ────────────────
    branch_nodes, branch_acts = _collect_branch(tgt_project, tgt_wbs)
    branch_node_uids = {w.uid for w in branch_nodes}
    branch_act_uids = {a.uid for a in branch_acts}
    tgt_act_by_uid = {a.uid: a for a in tgt_project.activities}

    seams: List[Dict[str, Any]] = []
    for r in tgt_project.relations:
        p_in = r.predecessor_uid in branch_act_uids
        s_in = r.successor_uid in branch_act_uids
        if p_in == s_in:          # both in (internal) or both out (unrelated)
            continue
        inside_uid = r.predecessor_uid if p_in else r.successor_uid
        outside_uid = r.successor_uid if p_in else r.predecessor_uid
        inside_act = tgt_act_by_uid.get(inside_uid)
        outside_act = tgt_act_by_uid.get(outside_uid)
        if not inside_act or not outside_act:
            continue
        seams.append({
            "outside_uid": outside_uid,
            "outside_id": outside_act.activity_id,
            "inside_id": inside_act.activity_id,
            "inside_name": inside_act.name,
            # "in" = inside activity is the successor (outside feeds into branch)
            # "out" = inside activity is the predecessor (branch feeds outside)
            "direction": "out" if p_in else "in",
            "type": r.type,
            "lag": r.lag,
        })

    # ── Delete the target branch (activities, its relations, WBS nodes) ─────
    tgt_project.activities = [a for a in tgt_project.activities
                              if a.uid not in branch_act_uids]
    tgt_project.relations = [r for r in tgt_project.relations
                             if r.predecessor_uid not in branch_act_uids
                             and r.successor_uid not in branch_act_uids]
    tgt_project.wbs_nodes = [w for w in tgt_project.wbs_nodes
                             if w.uid not in branch_node_uids]
    tgt_project.build_lookups()

    # ── Drop the source branch into the same slot ───────────────────────────
    ok, copy_msg, detail = copy_wbs_branch(
        src_project, src_wbs_code, tgt_project,
        tgt_parent_code=tgt_parent_code, id_mode=id_mode, new_wbs_name=new_wbs_name)
    if not ok:
        return False, f"Replace failed while copying source: {copy_msg}", {}

    # ── Re-stitch seams onto the incoming activities ────────────────────────
    reconnected = 0
    seams_dropped: List[Dict[str, str]] = []
    for s in seams:
        if match == "name":
            new_inside = next((a for a in tgt_project.activities
                               if a.name.lower() == s["inside_name"].lower()
                               and a.wbs_uid in {w.uid for w in tgt_project.wbs_nodes}), None)
        else:  # id
            new_inside = tgt_project.get_activity(activity_id=s["inside_id"])
        outside = tgt_project.get_activity(uid=s["outside_uid"])
        if new_inside and outside:
            if s["direction"] == "out":     # inside was predecessor
                pred, succ = new_inside.uid, outside.uid
            else:                            # inside was successor
                pred, succ = outside.uid, new_inside.uid
            tgt_project.relations.append(Relation(
                uid=_new_uid(), predecessor_uid=pred, successor_uid=succ,
                type=s["type"], lag=s["lag"],
            ))
            reconnected += 1
        else:
            seams_dropped.append({
                "outside_id": s["outside_id"],
                "inside_id": s["inside_id"],
                "direction": s["direction"],
                "type": s["type"],
            })

    tgt_project.build_lookups()
    from .schedule_model import compute_dates
    try:
        compute_dates(tgt_project)
    except Exception:
        pass

    detail.update({
        "wbs_removed": len(branch_nodes),
        "activities_removed": len(branch_acts),
        "seams_total": len(seams),
        "seams_reconnected": reconnected,
        "seams_dropped": seams_dropped,
    })
    msg = (f"Replaced '{tgt_wbs.name}' — {len(branch_acts)} activities out, "
           f"{detail['activities_copied']} in, "
           f"{reconnected}/{len(seams)} seam link(s) reconnected")
    if seams_dropped:
        msg += f", {len(seams_dropped)} could not reconnect (open ends)"
    return True, msg, detail


# ──────────────────────────────────────────────────────────────────────────────
# Activity-level replace / merge
# ──────────────────────────────────────────────────────────────────────────────

_APPLIABLE_ATTRS = {f for f, _ in _FIELDS_TO_DIFF}

# Public alias — the single source of truth for "which fields make up one
# activity's copyable data" (name/duration/dates/status/%/type/constraint).
# Anything that overwrites one activity's data with another's (apply_activity_changes
# here, and the merge-dedupe "replace" mode in server.py) should read this list
# rather than keep its own copy, so the two stay in sync automatically.
ACTIVITY_DATA_FIELDS = _APPLIABLE_ATTRS


def apply_activity_changes(
    src_project: Project,
    tgt_project: Project,
    changes: List[Dict[str, Any]],
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Pull individual activity field values from src into tgt — the "combine the
    differences" path. For each change, the matched target activity takes the
    source's value for the named attributes.

    changes: [{"activity_id": "A1000", "attrs": ["name", "planned_duration"]}]
             attrs omitted or ["*"] → apply every comparable field.

    Returns (success, message, {applied, skipped:[activity_id], fields_set}).
    """
    applied = 0
    fields_set = 0
    skipped: List[str] = []
    for ch in changes:
        aid = str(ch.get("activity_id") or "").strip()
        src_act = src_project.get_activity(activity_id=aid)
        tgt_act = tgt_project.get_activity(activity_id=aid)
        if not src_act or not tgt_act:
            skipped.append(aid)
            continue
        attrs = ch.get("attrs") or ["*"]
        if "*" in attrs:
            attrs = list(_APPLIABLE_ATTRS)
        touched = False
        for attr in attrs:
            if attr not in _APPLIABLE_ATTRS:
                continue
            setattr(tgt_act, attr, getattr(src_act, attr, None))
            fields_set += 1
            touched = True
        if touched:
            applied += 1
    tgt_project.build_lookups()
    from .schedule_model import compute_dates
    try:
        compute_dates(tgt_project)
    except Exception:
        pass
    msg = f"Applied {fields_set} field(s) across {applied} activit(y/ies)"
    if skipped:
        msg += f"; {len(skipped)} not found in both schedules"
    return True, msg, {"applied": applied, "fields_set": fields_set, "skipped": skipped}
