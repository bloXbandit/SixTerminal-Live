"""
edit_engine.py — Apply structured JSON edit commands to a Project object.

The LLM interpreter produces edit commands as JSON dicts.
This engine applies them safely to the in-memory Project model.
The result is then serialized to P6 XML by xml_writer.py.

Supported commands:

  rename_activity       — Change activity name (by ID or name match)
  update_duration       — Change planned/remaining duration (days → hours internally)
  update_activity_id    — Change the user-visible activity code
  add_activity          — Add a new activity to a WBS node
  delete_activity       — Remove an activity (and its relations)
  add_relation          — Add a predecessor/successor link
  delete_relation       — Remove a predecessor/successor link
  rename_wbs            — Rename a WBS node
  add_wbs               — Add a new WBS node
  move_activity_wbs     — Move an activity to a different WBS node
  bulk_rename           — Rename multiple activities matching a pattern
  bulk_update_duration  — Change duration for all activities matching a pattern
  set_constraint        — Set a date constraint on an activity
  clear_constraint      — Remove a date constraint from an activity

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


def apply_command(project: Project, command: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Apply a single edit command to the project.
    Returns (success: bool, message: str).
    """
    action = command.get("action", "").lower().strip()

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
        else:
            return False, f"Unknown action: '{action}'"
    except EditError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error applying '{action}': {e}"


def apply_commands(project: Project, commands: List[Dict[str, Any]]) -> List[Tuple[bool, str]]:
    """Apply a list of edit commands in order. Returns list of (success, message) tuples."""
    results = []
    for cmd in commands:
        ok, msg = apply_command(project, cmd)
        results.append((ok, msg))
        if not ok:
            break  # Stop on first failure to avoid cascading bad state
    return results


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
        raise EditError("activity_id is required for add_activity")
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
    new_wbs = WBSNode(
        uid=_new_uid(),
        name=name,
        code=code,
        parent_uid=parent.uid if parent else None,
        sequence_num=len(project.wbs_nodes),
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
