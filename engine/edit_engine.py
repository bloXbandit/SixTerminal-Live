"""
edit_engine.py — Apply structured JSON edit commands to a Project object.

The LLM interpreter produces edit commands as JSON dicts.
This engine applies them safely to the in-memory Project model.
The result is then serialized to P6 XML by xml_writer.py.

Supported commands:

  rename_activity           — Change activity name (by ID or name match)
  update_duration           — Change planned/remaining duration (days → hours internally)
  update_activity_id        — Change the user-visible activity code
  add_activity              — Add a new activity to a WBS node
  delete_activity           — Remove an activity (and its relations)
  add_relation              — Add a predecessor/successor link
  delete_relation           — Remove a predecessor/successor link
  rename_wbs                — Rename a WBS node
  add_wbs                   — Add a new WBS node
  move_activity_wbs         — Move an activity to a different WBS node
  bulk_rename               — Rename multiple activities matching a regex pattern
  bulk_update_duration      — Change duration for all activities matching a pattern
  set_constraint            — Set a date constraint on an activity
  clear_constraint          — Remove a date constraint from an activity
  bulk_add_activity         — Add the same activity to multiple WBS nodes in one call
  bulk_create_wbs           — Create multiple WBS folders under the same parent in one call
  bulk_rename_activities    — Rename activities by explicit from→to list (ID, name, or WBS scope)
  bulk_update_activity_id   — Mass ID updates: resequence, pattern replace, or prefix swap

Each command dict must have an "action" key. Other keys depend on the action.
"""

import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from .schedule_model import Project, Activity, Relation, WBSNode


class EditError(Exception):
    """Raised when an edit command cannot be applied."""
    pass


def _hours(days: float) -> float:
    """Convert days to hours (8h/day)."""
    return days * 8.0


def _find_activity(project: Project, activity_id: Optional[str] = None,
                   name: Optional[str] = None) -> List[Activity]:
    """
    Find activities by ID (exact) or name (case-insensitive substring).
    Returns a list — may be multiple matches for name searches.
    """
    results = []
    if activity_id:
        a = project.get_activity(activity_id=activity_id)
        if a:
            return [a]
    if name:
        name_low = name.lower()
        for a in project.activities:
            if name_low in a.name.lower():
                results.append(a)
    return results


def _find_wbs(project: Project, wbs_code: Optional[str] = None,
              wbs_name: Optional[str] = None) -> Optional[WBSNode]:
    """Find a WBS node by code or name."""
    if wbs_code:
        for w in project.wbs_nodes:
            if w.code.lower() == wbs_code.lower():
                return w
    if wbs_name:
        name_low = wbs_name.lower()
        for w in project.wbs_nodes:
            if name_low in w.name.lower():
                return w
    return None


def _new_uid() -> str:
    """Generate a new unique ID for new objects."""
    return str(uuid.uuid4().int)[:10]


def _next_activity_id(project: Project) -> str:
    """
    Compute the next available activity ID following the project's dominant
    prefix + 4-digit numbering (e.g. A1000 -> A1010), skipping any collisions.
    Used when add_activity / paste are called without an explicit ID.
    """
    prefix = "A"
    numeric_ids: List[int] = []
    for a in project.activities:
        raw = a.activity_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        try:
            numeric_ids.append(int(raw))
            if a.activity_id and a.activity_id[0].isalpha():
                prefix = a.activity_id[0]
        except ValueError:
            pass
    current = (((max(numeric_ids) // 10) + 1) * 10) if numeric_ids else 1000
    while project.get_activity(activity_id=f"{prefix}{current:04d}"):
        current += 10
    return f"{prefix}{current:04d}"


def _would_create_cycle(project: Project, pred_uid: str, succ_uid: str) -> bool:
    """
    Return True if adding a predecessor→successor link would create a circular
    dependency (or is a self-loop). Walks forward from succ along existing
    successor edges; if it can already reach pred, the new edge closes a loop.
    """
    if pred_uid == succ_uid:
        return True
    adj: Dict[str, List[str]] = {}
    for r in project.relations:
        adj.setdefault(r.predecessor_uid, []).append(r.successor_uid)
    stack = [succ_uid]
    seen: set = set()
    while stack:
        u = stack.pop()
        if u == pred_uid:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, []))
    return False


def apply_command(project: Project, command: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Apply a single edit command to the project.
    Returns (success: bool, message: str).
    """
    action = command.get("action", "").lower().strip()

    if action in ("chat", "clarify"):
        return (True, command.get("message", command.get("question", "")))

    try:
        if action == "rename_activity":
            return _rename_activity(project, command)
        elif action == "update_duration":
            return _update_duration(project, command)
        elif action == "update_activity_id":
            return _update_activity_id(project, command)
        elif action == "add_activity":
            return _add_activity(project, command)
        elif action == "delete_activity":
            return _delete_activity(project, command)
        elif action == "add_relation":
            return _add_relation(project, command)
        elif action == "delete_relation":
            return _delete_relation(project, command)
        elif action == "rename_wbs":
            return _rename_wbs(project, command)
        elif action == "add_wbs":
            return _add_wbs(project, command)
        elif action == "move_activity_wbs":
            return _move_activity_wbs(project, command)
        elif action == "bulk_rename":
            return _bulk_rename(project, command)
        elif action == "bulk_update_duration":
            return _bulk_update_duration(project, command)
        elif action == "set_constraint":
            return _set_constraint(project, command)
        elif action == "clear_constraint":
            return _clear_constraint(project, command)
        elif action == "bulk_add_activity":
            return _bulk_add_activity(project, command)
        elif action == "bulk_create_wbs":
            return _bulk_create_wbs(project, command)
        elif action == "bulk_rename_activities":
            return _bulk_rename_activities(project, command)
        elif action == "bulk_update_activity_id":
            return _bulk_update_activity_id(project, command)
        else:
            return False, f"Unknown action: '{action}'"
    except EditError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error applying '{action}': {e}"


def apply_commands(project: Project, commands: List[Dict[str, Any]]) -> List[Tuple[bool, str]]:
    """Apply a list of edit commands in order. Returns list of (success, message) tuples.
    After all commands, re-runs a CPM forward/backward pass to keep projected
    Start / Finish dates current (including newly added activities)."""
    from engine.schedule_model import compute_dates
    results = []
    any_ok = False
    for cmd in commands:
        ok, msg = apply_command(project, cmd)
        results.append((ok, msg))
        if ok:
            any_ok = True
        if not ok:
            break  # Stop on first failure to avoid cascading bad state
    if any_ok:
        try:
            compute_dates(project)
        except Exception:
            pass  # CPM failure must never block an edit from completing
    return results


# ── Disambiguation helpers ────────────────────────────────────────────────────

# Actions that support name-based target lookup and may need disambiguation
_NAME_TARGET_ACTIONS = {
    "rename_activity", "update_duration", "update_activity_id",
    "delete_activity", "move_activity_wbs", "set_constraint", "clear_constraint",
}


def get_wbs_path(project: Project, wbs_uid: str) -> str:
    """Return full WBS path string, e.g. 'Structure / Level 2 / Concrete'."""
    wbs_map = {w.uid: w for w in project.wbs_nodes}
    path = []
    uid = wbs_uid
    seen = set()
    while uid and uid not in seen:
        seen.add(uid)
        node = wbs_map.get(uid)
        if not node:
            break
        path.insert(0, node.name)
        uid = node.parent_uid
    return " / ".join(path) if path else ""


def activity_display(project: Project, a: Activity) -> Dict[str, str]:
    """Return a display dict for an activity — used in disambiguation cards."""
    return {
        "uid": a.uid,
        "activity_id": a.activity_id,
        "name": a.name,
        "wbs_path": get_wbs_path(project, a.wbs_uid),
        "planned_start": a.planned_start or "",
        "planned_finish": a.planned_finish or "",
        "status": a.status,
        "activity_type": a.activity_type,
    }


def check_disambiguation(
    project: Project, commands: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Pre-check commands for ambiguous name matches before applying.

    Returns a disambiguation dict if any command matches multiple activities
    and apply_to_all is not explicitly set:
        {
          "command_index": int,
          "command": dict,
          "field": "target_name" | "predecessor_name" | "successor_name",
          "search_term": str,
          "matches": [activity_display dicts],
        }

    Returns None if all commands are unambiguous.
    """
    for idx, cmd in enumerate(commands):
        action = cmd.get("action", "").lower().strip()

        if action in _NAME_TARGET_ACTIONS:
            if cmd.get("target_name") and not cmd.get("activity_id") and not cmd.get("apply_to_all"):
                matches = _find_activity(project, name=cmd["target_name"])
                if len(matches) > 1:
                    return {
                        "command_index": idx,
                        "command": cmd,
                        "field": "target_name",
                        "search_term": cmd["target_name"],
                        "matches": [activity_display(project, a) for a in matches],
                    }

        elif action == "add_relation":
            # Check predecessor
            if cmd.get("predecessor_name") and not cmd.get("predecessor_id"):
                matches = _find_activity(project, name=cmd["predecessor_name"])
                if len(matches) > 1:
                    return {
                        "command_index": idx,
                        "command": cmd,
                        "field": "predecessor_name",
                        "search_term": cmd["predecessor_name"],
                        "matches": [activity_display(project, a) for a in matches],
                    }
            # Check successor
            if cmd.get("successor_name") and not cmd.get("successor_id"):
                matches = _find_activity(project, name=cmd["successor_name"])
                if len(matches) > 1:
                    return {
                        "command_index": idx,
                        "command": cmd,
                        "field": "successor_name",
                        "search_term": cmd["successor_name"],
                        "matches": [activity_display(project, a) for a in matches],
                    }

        elif action == "delete_relation":
            if cmd.get("predecessor_name") and not cmd.get("predecessor_id"):
                matches = _find_activity(project, name=cmd["predecessor_name"])
                if len(matches) > 1:
                    return {
                        "command_index": idx,
                        "command": cmd,
                        "field": "predecessor_name",
                        "search_term": cmd["predecessor_name"],
                        "matches": [activity_display(project, a) for a in matches],
                    }

    return None


# ── Schedule health / constraint report ──────────────────────────────────────

_HARD_CONSTRAINT_TYPES = {
    "Must Start On", "Must Finish On", "Start On", "Finish On",
}
_SOFT_CONSTRAINT_TYPES = {
    "Start On Or Before", "Finish On Or Before",
    "Start On Or After", "Finish On Or After",
    "As Late As Possible",
}
_SKIP_TYPES_FOR_OPEN_END = {"WBS Summary", "Level of Effort"}


def generate_schedule_report(project: Project) -> Dict[str, Any]:
    """
    Analyze schedule health and return a structured report dict.

    Checks:
      - Activities with hard constraints (Must Start/Finish On, Start/Finish On)
      - Activities with soft constraints
      - Activities with no predecessors (open start)
      - Activities with no successors (open finish)
    """
    has_predecessor: set = {r.successor_uid for r in project.relations}
    has_successor: set = {r.predecessor_uid for r in project.relations}

    hard_constraints = []
    soft_constraints = []
    open_start = []   # no predecessors
    open_finish = []  # no successors

    for a in project.activities:
        skip_open_end = a.activity_type in _SKIP_TYPES_FOR_OPEN_END or a.status == "Completed"

        if a.constraint_type in _HARD_CONSTRAINT_TYPES:
            hard_constraints.append(activity_display(project, a) | {"constraint_type": a.constraint_type, "constraint_date": a.constraint_date or ""})
        elif a.constraint_type in _SOFT_CONSTRAINT_TYPES:
            soft_constraints.append(activity_display(project, a) | {"constraint_type": a.constraint_type, "constraint_date": a.constraint_date or ""})

        if not skip_open_end:
            if a.uid not in has_predecessor:
                open_start.append(activity_display(project, a))
            if a.uid not in has_successor:
                open_finish.append(activity_display(project, a))

    total = len(project.activities)
    checkable = [a for a in project.activities if a.activity_type not in _SKIP_TYPES_FOR_OPEN_END and a.status != "Completed"]

    return {
        "total_activities": total,
        "total_relations": len(project.relations),
        "hard_constraints": hard_constraints,
        "soft_constraints": soft_constraints,
        "open_start": open_start,
        "open_finish": open_finish,
        "health_pct": round(
            100 * (1 - (len(open_start) + len(open_finish)) / max(len(checkable) * 2, 1)), 1
        ),
    }


# --- Individual command handlers ---

def _rename_activity(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found matching: {cmd.get('activity_id') or cmd.get('target_name')}")
    new_name = cmd.get("new_name", "").strip()
    if not new_name:
        raise EditError("new_name is required for rename_activity")
    if len(matches) > 1 and not cmd.get("apply_to_all"):
        raise EditError(f"Found {len(matches)} activities matching '{cmd.get('target_name')}'. "
                        f"Use activity_id for exact match, or set apply_to_all=true for bulk rename.")
    for a in matches:
        a.name = new_name
    return True, f"Renamed {len(matches)} activity/activities to '{new_name}'"


def _update_duration(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found matching: {cmd.get('activity_id') or cmd.get('target_name')}")
    new_days = cmd.get("new_duration_days")
    if new_days is None:
        raise EditError("new_duration_days is required for update_duration")
    new_hours = _hours(float(new_days))
    if len(matches) > 1 and not cmd.get("apply_to_all"):
        raise EditError(f"Found {len(matches)} activities matching '{cmd.get('target_name')}'. "
                        f"Use activity_id for exact match, or set apply_to_all=true.")
    for a in matches:
        a.planned_duration = new_hours
        if a.status == "Not Started":
            a.remaining_duration = new_hours
    return True, f"Updated duration to {new_days} days ({new_hours}h) for {len(matches)} activity/activities"


def _update_activity_id(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found matching: {cmd.get('activity_id') or cmd.get('target_name')}")
    if len(matches) > 1:
        raise EditError(f"Found {len(matches)} activities — use activity_id for exact match when changing IDs")
    new_id = cmd.get("new_activity_id", "").strip()
    if not new_id:
        raise EditError("new_activity_id is required")
    # Check for duplicate
    if project.get_activity(activity_id=new_id):
        raise EditError(f"Activity ID '{new_id}' already exists in this project")
    old_id = matches[0].activity_id
    matches[0].activity_id = new_id
    project.build_lookups()
    return True, f"Changed activity ID from '{old_id}' to '{new_id}'"


def _add_activity(project: Project, cmd: Dict) -> Tuple[bool, str]:
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"))
    if not wbs:
        raise EditError(f"WBS node not found: {cmd.get('wbs_code') or cmd.get('wbs_name')}")
    act_id = cmd.get("activity_id", "").strip()
    if not act_id:
        # Auto-assign the next available ID (quick-add / paste from the grid)
        act_id = _next_activity_id(project)
    if project.get_activity(activity_id=act_id):
        raise EditError(f"Activity ID '{act_id}' already exists")
    name = cmd.get("name", "").strip()
    if not name:
        raise EditError("name is required for add_activity")
    duration_days = float(cmd.get("duration_days", 0))
    cal_uid = cmd.get("calendar_uid") or (project.calendars[0].uid if project.calendars else "1")
    new_act = Activity(
        uid=_new_uid(),
        activity_id=act_id,
        name=name,
        wbs_uid=wbs.uid,
        calendar_uid=cal_uid,
        activity_type=cmd.get("activity_type", "Task Dependent"),
        status="Not Started",
        planned_duration=_hours(duration_days),
        remaining_duration=_hours(duration_days),
        planned_start=cmd.get("planned_start"),
        planned_finish=cmd.get("planned_finish"),
    )
    project.activities.append(new_act)
    project.build_lookups()
    return True, f"Added activity '{act_id} — {name}' ({duration_days}d) to WBS '{wbs.name}'"


def _delete_activity(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found matching: {cmd.get('activity_id') or cmd.get('target_name')}")
    if len(matches) > 1 and not cmd.get("apply_to_all"):
        raise EditError(f"Found {len(matches)} activities. Use activity_id for exact match or set apply_to_all=true.")
    uids = {a.uid for a in matches}
    project.activities = [a for a in project.activities if a.uid not in uids]
    project.relations = [r for r in project.relations
                         if r.predecessor_uid not in uids and r.successor_uid not in uids]
    project.build_lookups()
    return True, f"Deleted {len(matches)} activity/activities and their relations"


def _add_relation(project: Project, cmd: Dict) -> Tuple[bool, str]:
    pred_matches = _find_activity(project, cmd.get("predecessor_id"), cmd.get("predecessor_name"))
    succ_matches = _find_activity(project, cmd.get("successor_id"), cmd.get("successor_name"))
    if not pred_matches:
        raise EditError(f"Predecessor not found: {cmd.get('predecessor_id') or cmd.get('predecessor_name')}")
    if not succ_matches:
        raise EditError(f"Successor not found: {cmd.get('successor_id') or cmd.get('successor_name')}")
    if len(pred_matches) > 1:
        raise EditError(f"Multiple predecessors matched '{cmd.get('predecessor_name')}' — use activity_id")
    if len(succ_matches) > 1:
        raise EditError(f"Multiple successors matched '{cmd.get('successor_name')}' — use activity_id")
    pred = pred_matches[0]
    succ = succ_matches[0]
    # Reject self-loops and circular dependencies before mutating the network
    if pred.uid == succ.uid:
        raise EditError(f"Cannot link {pred.activity_id} to itself")
    if _would_create_cycle(project, pred.uid, succ.uid):
        raise EditError(
            f"Adding {pred.activity_id} → {succ.activity_id} would create a circular "
            f"dependency ({succ.activity_id} already leads back to {pred.activity_id})"
        )
    # Check for duplicate
    for r in project.relations:
        if r.predecessor_uid == pred.uid and r.successor_uid == succ.uid:
            return True, f"Relation already exists: {pred.activity_id} → {succ.activity_id}"
    rel_type_map = {
        "fs": "Finish to Start", "ss": "Start to Start",
        "ff": "Finish to Finish", "sf": "Start to Finish",
    }
    rel_type = rel_type_map.get(cmd.get("type", "fs").lower(), "Finish to Start")
    lag_days = float(cmd.get("lag_days", 0))
    project.relations.append(Relation(
        uid=_new_uid(),
        predecessor_uid=pred.uid,
        successor_uid=succ.uid,
        type=rel_type,
        lag=_hours(lag_days),
    ))
    return True, f"Added {rel_type} relation: {pred.activity_id} → {succ.activity_id} (lag: {lag_days}d)"


def _delete_relation(project: Project, cmd: Dict) -> Tuple[bool, str]:
    pred_matches = _find_activity(project, cmd.get("predecessor_id"), cmd.get("predecessor_name"))
    succ_matches = _find_activity(project, cmd.get("successor_id"), cmd.get("successor_name"))
    if not pred_matches or not succ_matches:
        raise EditError("Both predecessor and successor must be specified to delete a relation")
    pred_uids = {a.uid for a in pred_matches}
    succ_uids = {a.uid for a in succ_matches}
    before = len(project.relations)
    project.relations = [r for r in project.relations
                         if not (r.predecessor_uid in pred_uids and r.successor_uid in succ_uids)]
    removed = before - len(project.relations)
    if removed == 0:
        return False, "No matching relation found to delete"
    return True, f"Removed {removed} relation(s)"


def _rename_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"))
    if not wbs:
        raise EditError(f"WBS node not found: {cmd.get('wbs_code') or cmd.get('wbs_name')}")
    new_name = cmd.get("new_name", "").strip()
    new_code = cmd.get("new_code", "").strip()
    if not new_name and not new_code:
        raise EditError("new_name or new_code is required for rename_wbs")
    old = wbs.name
    if new_name:
        wbs.name = new_name
    if new_code:
        wbs.code = new_code
    return True, f"Renamed WBS '{old}' → '{wbs.name}'"


def _add_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    name = cmd.get("name", "").strip()
    code = cmd.get("code", name[:20]).strip()
    if not name:
        raise EditError("name is required for add_wbs")
    parent = None
    if cmd.get("parent_code") or cmd.get("parent_name"):
        parent = _find_wbs(project, cmd.get("parent_code"), cmd.get("parent_name"))
        if not parent:
            raise EditError(f"Parent WBS not found: {cmd.get('parent_code') or cmd.get('parent_name')}")
    # Sequence number: sit AFTER the last existing sibling so P6 displays it last
    parent_uid = parent.uid if parent else None
    siblings = [w for w in project.wbs_nodes if w.parent_uid == parent_uid]
    next_seq = (max(s.sequence_num for s in siblings) + 10) if siblings else 0
    new_wbs = WBSNode(
        uid=_new_uid(),
        name=name,
        code=code,
        parent_uid=parent_uid,
        sequence_num=next_seq,
    )
    project.wbs_nodes.append(new_wbs)
    project.build_lookups()
    return True, f"Added WBS node '{code} — {name}'" + (f" under '{parent.name}'" if parent else " at root")


def _move_activity_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found: {cmd.get('activity_id') or cmd.get('target_name')}")
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"))
    if not wbs:
        raise EditError(f"Target WBS not found: {cmd.get('wbs_code') or cmd.get('wbs_name')}")
    if len(matches) > 1 and not cmd.get("apply_to_all"):
        raise EditError(f"Found {len(matches)} activities. Use activity_id or set apply_to_all=true.")
    for a in matches:
        a.wbs_uid = wbs.uid
    return True, f"Moved {len(matches)} activity/activities to WBS '{wbs.name}'"


def _bulk_rename(project: Project, cmd: Dict) -> Tuple[bool, str]:
    pattern = cmd.get("pattern", "").strip()
    replacement = cmd.get("replacement", "").strip()
    if not pattern:
        raise EditError("pattern is required for bulk_rename")
    count = 0
    for a in project.activities:
        if re.search(pattern, a.name, re.IGNORECASE):
            a.name = re.sub(pattern, replacement, a.name, flags=re.IGNORECASE)
            count += 1
    return True, f"Bulk renamed {count} activities matching '{pattern}'"


def _bulk_update_duration(project: Project, cmd: Dict) -> Tuple[bool, str]:
    pattern = cmd.get("pattern", "").strip()
    new_days = cmd.get("new_duration_days")
    if not pattern:
        raise EditError("pattern is required for bulk_update_duration")
    if new_days is None:
        raise EditError("new_duration_days is required")
    new_hours = _hours(float(new_days))
    count = 0
    for a in project.activities:
        if re.search(pattern, a.name, re.IGNORECASE):
            a.planned_duration = new_hours
            if a.status == "Not Started":
                a.remaining_duration = new_hours
            count += 1
    return True, f"Updated duration to {new_days}d for {count} activities matching '{pattern}'"


def _set_constraint(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found: {cmd.get('activity_id') or cmd.get('target_name')}")
    if len(matches) > 1:
        raise EditError(f"Found {len(matches)} activities — use activity_id for constraints")
    constraint_type = cmd.get("constraint_type", "").strip()
    constraint_date = cmd.get("constraint_date", "").strip()
    if not constraint_type:
        raise EditError("constraint_type is required (e.g. 'Start On Or After', 'Finish On Or Before')")
    matches[0].constraint_type = constraint_type
    matches[0].constraint_date = constraint_date or None
    return True, f"Set constraint '{constraint_type}' on '{matches[0].name}'"


def _clear_constraint(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(f"No activity found: {cmd.get('activity_id') or cmd.get('target_name')}")
    for a in matches:
        a.constraint_type = None
        a.constraint_date = None
    return True, f"Cleared constraints on {len(matches)} activity/activities"


def _bulk_add_activity(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Add the same activity to multiple WBS nodes in one call.
    Auto-assigns sequential activity IDs starting from next available (or start_id).

    Required:
      name       — activity name
      wbs_names  — list of WBS names (each gets its own copy of the activity)
    Optional:
      duration_days  — default 0
      activity_type  — default "Task Dependent"
      start_id       — e.g. "A2000". Defaults to next available ID in project.
      id_increment   — default 10
    """
    name = cmd.get("name", "").strip()
    if not name:
        raise EditError("name is required for bulk_add_activity")
    wbs_names = cmd.get("wbs_names", [])
    if not wbs_names:
        raise EditError("wbs_names (list of WBS names) is required for bulk_add_activity")

    duration_days = float(cmd.get("duration_days", 0))
    act_type = cmd.get("activity_type", "Task Dependent")
    cal_uid = cmd.get("calendar_uid") or (project.calendars[0].uid if project.calendars else "1")
    increment = int(cmd.get("id_increment", 10))

    # Determine prefix and starting number
    prefix = "A"
    numeric_ids = []
    for a in project.activities:
        raw = a.activity_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        try:
            numeric_ids.append(int(raw))
            if a.activity_id and a.activity_id[0].isalpha():
                prefix = a.activity_id[0]
        except ValueError:
            pass

    if cmd.get("start_id"):
        start_str = str(cmd["start_id"]).strip()
        raw_s = start_str.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        if start_str and start_str[0].isalpha():
            prefix = start_str[0]
        try:
            current_num = int(raw_s)
        except ValueError:
            current_num = (((max(numeric_ids) // 10) + 1) * 10) if numeric_ids else 1000
    else:
        current_num = (((max(numeric_ids) // 10) + 1) * 10) if numeric_ids else 1000

    added = []
    skipped = []
    for wbs_name in wbs_names:
        wbs = _find_wbs(project, wbs_name=wbs_name)
        if not wbs:
            skipped.append(f"WBS '{wbs_name}' not found")
            continue
        # Advance past any collisions
        while project.get_activity(activity_id=f"{prefix}{current_num:04d}"):
            current_num += increment
        act_id = f"{prefix}{current_num:04d}"
        new_act = Activity(
            uid=_new_uid(),
            activity_id=act_id,
            name=name,
            wbs_uid=wbs.uid,
            calendar_uid=cal_uid,
            activity_type=act_type,
            status="Not Started",
            planned_duration=_hours(duration_days),
            remaining_duration=_hours(duration_days),
        )
        project.activities.append(new_act)
        added.append(f"{act_id} → {wbs.name}")
        current_num += increment

    project.build_lookups()
    msg = f"Added '{name}' ({duration_days}d) to {len(added)} WBS node(s): {', '.join(added)}"
    if skipped:
        msg += f". Skipped: {'; '.join(skipped)}"
    return bool(added), msg


def _bulk_create_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Create multiple WBS folders under the same optional parent in one call.

    Required:
      nodes — list of {name, code} dicts (code is optional, defaults to name[:20])
    Optional:
      parent_name — parent WBS name
      parent_code — parent WBS code
    """
    nodes = cmd.get("nodes", [])
    if not nodes:
        raise EditError("nodes (list of {name, code}) is required for bulk_create_wbs")

    parent = None
    if cmd.get("parent_code") or cmd.get("parent_name"):
        parent = _find_wbs(project, cmd.get("parent_code"), cmd.get("parent_name"))
        if not parent:
            raise EditError(f"Parent WBS not found: {cmd.get('parent_code') or cmd.get('parent_name')}")

    created = []
    parent_uid_for_seq = parent.uid if parent else None
    existing_siblings = [w for w in project.wbs_nodes if w.parent_uid == parent_uid_for_seq]
    seq_base = (max(s.sequence_num for s in existing_siblings) + 10) if existing_siblings else 0
    for i, node_def in enumerate(nodes):
        name = str(node_def.get("name", "")).strip()
        code = str(node_def.get("code", name[:20])).strip() or name[:20]
        if not name:
            continue
        new_wbs = WBSNode(
            uid=_new_uid(),
            name=name,
            code=code,
            parent_uid=parent.uid if parent else None,
            sequence_num=seq_base + (i * 10),
        )
        project.wbs_nodes.append(new_wbs)
        created.append(f"'{code} — {name}'")

    project.build_lookups()
    parent_str = f" under '{parent.name}'" if parent else " at root level"
    return bool(created), f"Created {len(created)} WBS node(s){parent_str}: {', '.join(created)}"


def _bulk_rename_activities(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Rename multiple activities by explicit from→to list.
    Each entry can target by activity_id, from_name (substring), or wbs_name (all in that WBS).
    Supports {original} placeholder in to_name to build on existing name.

    Required:
      renames — list of rename entries, each with:
        activity_id OR from_name OR wbs_name   (how to find)
        to_name                                 (new name; supports {original})
    """
    renames = cmd.get("renames", [])
    if not renames:
        raise EditError("renames list is required for bulk_rename_activities")

    applied = 0
    errors = []

    for r in renames:
        act_id   = r.get("activity_id")
        from_name = r.get("from_name") or r.get("target_name")
        wbs_name  = r.get("wbs_name")
        to_name   = str(r.get("to_name", "")).strip()
        if not to_name:
            errors.append("Missing to_name in a rename entry")
            continue

        # Scope: entire WBS
        if wbs_name and not act_id and not from_name:
            wbs = _find_wbs(project, wbs_name=wbs_name)
            if not wbs:
                errors.append(f"WBS '{wbs_name}' not found")
                continue
            for a in project.activities:
                if a.wbs_uid == wbs.uid:
                    a.name = to_name.replace("{original}", a.name)
                    applied += 1
            continue

        # Scope: by ID or name
        matches = _find_activity(project, act_id, from_name)
        if not matches:
            errors.append(f"No activity found: {act_id or from_name}")
            continue
        for a in matches:
            a.name = to_name.replace("{original}", a.name)
            applied += 1

    msg = f"Renamed {applied} activity/activities"
    if errors:
        msg += f". Issues: {'; '.join(errors)}"
    return applied > 0, msg


def _bulk_update_activity_id(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Mass activity ID updates. Three modes:

    mode="resequence"   — renumber activities in their current order
      start_id    — e.g. "A2000" (required)
      increment   — default 10
      filter_wbs  — optional WBS name to limit scope

    mode="pattern"      — regex find/replace on ID strings
      pattern     — regex to match
      replacement — replacement string (backreferences supported)

    mode="prefix_swap"  — swap the letter prefix on matching IDs
      old_prefix  — e.g. "A"
      new_prefix  — e.g. "B"
      filter_wbs  — optional WBS name to limit scope
    """
    mode = str(cmd.get("mode", "pattern")).lower()

    if mode == "resequence":
        start_id = str(cmd.get("start_id", "A1000")).strip()
        raw_s = start_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        prefix = start_id[0] if start_id and start_id[0].isalpha() else "A"
        try:
            current_num = int(raw_s)
        except ValueError:
            current_num = 1000
        increment = int(cmd.get("increment", 10))

        filter_wbs = cmd.get("filter_wbs")
        target_wbs_uid = None
        if filter_wbs:
            wbs = _find_wbs(project, wbs_name=filter_wbs)
            if not wbs:
                raise EditError(f"filter_wbs '{filter_wbs}' not found")
            target_wbs_uid = wbs.uid

        acts = [a for a in project.activities
                if target_wbs_uid is None or a.wbs_uid == target_wbs_uid]

        # Pass 1: temp IDs to avoid mid-sequence collisions
        for a in acts:
            a.activity_id = f"__TEMP_{a.uid}__"
        project.build_lookups()

        # Pass 2: final IDs
        for a in acts:
            a.activity_id = f"{prefix}{current_num:04d}"
            current_num += increment
        project.build_lookups()
        scope = f" in WBS '{filter_wbs}'" if filter_wbs else ""
        return True, f"Resequenced {len(acts)} activity IDs{scope} starting from {start_id} (increment {increment})"

    elif mode == "pattern":
        pattern = str(cmd.get("pattern", "")).strip()
        replacement = str(cmd.get("replacement", "")).strip()
        if not pattern:
            raise EditError("pattern is required for bulk_update_activity_id with mode=pattern")
        count = 0
        for a in project.activities:
            if re.search(pattern, a.activity_id):
                new_id = re.sub(pattern, replacement, a.activity_id)
                if new_id != a.activity_id and not project.get_activity(activity_id=new_id):
                    a.activity_id = new_id
                    count += 1
        project.build_lookups()
        return True, f"Updated {count} activity IDs matching pattern '{pattern}'"

    elif mode == "prefix_swap":
        old_prefix = str(cmd.get("old_prefix", "")).strip()
        new_prefix = str(cmd.get("new_prefix", "")).strip()
        if not old_prefix or not new_prefix:
            raise EditError("old_prefix and new_prefix are required for prefix_swap mode")
        filter_wbs = cmd.get("filter_wbs")
        target_wbs_uid = None
        if filter_wbs:
            wbs = _find_wbs(project, wbs_name=filter_wbs)
            if wbs:
                target_wbs_uid = wbs.uid
        count = 0
        for a in project.activities:
            if target_wbs_uid and a.wbs_uid != target_wbs_uid:
                continue
            if a.activity_id.startswith(old_prefix):
                new_id = new_prefix + a.activity_id[len(old_prefix):]
                if not project.get_activity(activity_id=new_id):
                    a.activity_id = new_id
                    count += 1
        project.build_lookups()
        return True, f"Swapped prefix '{old_prefix}' → '{new_prefix}' on {count} activity IDs"

    else:
        raise EditError(f"Unknown mode '{mode}' for bulk_update_activity_id. Use: resequence, pattern, prefix_swap")
