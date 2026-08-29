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
  match_subfolder_numbers   — Renumber subfolders to match their parent folder's number
  add_wbs                   — Add a new WBS node
  move_activity_wbs         — Move an activity to a different WBS node
  move_activities           — Move a set of activities into a folder (cut & paste)
  reorder_wbs               — Move a WBS folder up/down among its siblings
  delete_wbs                — Delete a folder (contents move up, or go with it)
  recommend_logic           — Report what logic is missing (advisory; changes nothing)
  bulk_rename               — Rename multiple activities matching a regex pattern
  bulk_update_duration      — Change duration for all activities matching a pattern
  set_constraint            — Set a date constraint on an activity
  clear_constraint          — Remove a date constraint from an activity
  set_actual_date           — Move (or clear) an actual start/finish date
  set_progress              — Status a row: not started / in progress / completed
  update_planned_date       — Set a planned start, or a finish (adjusts duration)
  update_udf                — Set a user-defined field (e.g. Number of Electricians)
  apply_crew_to_name        — Put a crew count on every activity doing that work
  fill_crew_defaults        — Fill blank crew cells from counts set elsewhere
  bulk_rules                — If/then find-and-change across the schedule or one folder
  bulk_add_activity         — Add the same activity to multiple WBS nodes in one call
  bulk_create_wbs           — Create multiple WBS folders under the same parent in one call
  add_wbs_for_each          — Add a child folder under EVERY folder matching a pattern
  bulk_rename_activities    — Rename activities by explicit from→to list (ID, name, or WBS scope)
  bulk_update_activity_id   — Mass ID updates: resequence, pattern replace, or prefix swap
  normalize_activity_ids    — Put stray activity codes back on the job's own ID pattern
  read_document             — Look inside a document already given (advisory; changes nothing)

Each command dict must have an "action" key. Other keys depend on the action.
"""

import re
import uuid
import datetime as _dt
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
              wbs_name: Optional[str] = None,
              wbs_uid: Optional[str] = None) -> Optional[WBSNode]:
    """
    Find a WBS node by uid, code, or name — in that order of precision.
    Name matching is a substring match, so it can hit the wrong folder when
    one name contains another ('Site' inside 'Sitework'); the grid passes
    wbs_uid so a click always targets exactly the folder that was clicked.
    """
    if wbs_uid:
        for w in project.wbs_nodes:
            if w.uid == wbs_uid:
                return w
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


def _suggest(needle: str, candidates: List[Tuple[str, str]], n: int = 5) -> str:
    """
    "Did you mean" for a name or id that did not resolve.

    `candidates` is a list of (id, label) pairs. Matching is deliberately
    generous: an exact-prefix or substring hit ranks above a fuzzy one, so a
    half-remembered id still lands somewhere useful.
    """
    import difflib
    if not needle or not candidates:
        return ""
    low = str(needle).strip().lower()
    scored = []
    for ident, label in candidates:
        hay = f"{ident} {label}".lower()
        if low and low in hay:
            score = 1.0 + (0.5 if str(ident).lower().startswith(low) else 0.0)
        else:
            score = max(difflib.SequenceMatcher(None, low, str(ident).lower()).ratio(),
                        difflib.SequenceMatcher(None, low, str(label).lower()).ratio())
        if score >= 0.5:
            scored.append((score, ident, label))
    if not scored:
        return ""
    scored.sort(key=lambda t: (-t[0], t[1]))
    shown = "; ".join(f"{i} — {l}" for _, i, l in scored[:n])
    return f" Did you mean: {shown}"


def _no_activity(project: Project, needle) -> str:
    """
    The message for an activity that does not exist.

    An agent that gets a flat "not found" tends to guess another id and fail
    again — that is how MDC1.MIL.1130 and MDC1.MIL.1070 got invented. Handing
    back the real neighbours turns a dead end into the answer.
    """
    cands = [(a.activity_id, a.name) for a in project.activities]
    hint = _suggest(needle, cands)
    if not hint and cands:
        # Nothing scored close enough, but handing back NO real id is what
        # sends an agent looking for another guess. A few genuine ones give it
        # something to check the shape against.
        shown = "; ".join(f"{i} — {n}" for i, n in cands[:3])
        hint = (f" Real activity IDs in this schedule look like: {shown}."
                f" Use one exactly as it appears.")
    return f"Activity '{needle}' not found in this schedule.{hint}"


def _no_wbs(project: Project, needle) -> str:
    cands = [(w.code or w.uid, w.name) for w in project.wbs_nodes]
    return (f"WBS folder '{needle}' not found in this schedule."
            + (_suggest(needle, cands) or
               " Use a folder name or code exactly as it appears in the context."))


def _new_uid() -> str:
    """Generate a new unique ID for new objects."""
    return str(uuid.uuid4().int)[:10]


def _next_activity_id(project: Project) -> str:
    """
    Compute the next available activity ID following the project's dominant
    prefix + 4-digit numbering (e.g. A1000 -> A1010), skipping any collisions.
    Used when add_activity / paste are called without an explicit ID.
    """
    # Split each id into "everything before the trailing digits" + those digits.
    # Stripping leading letters instead breaks every real-world scheme:
    # "T-1000" -> -1000, "MILE-001" -> -1, "MDC1.MIL.1000" -> unparseable.
    pat = re.compile(r"^(.*?)(\d+)$")
    counts: Dict[str, int] = {}
    parsed: List[Tuple[str, int, int]] = []      # (prefix, number, zero-pad width)
    for a in project.activities:
        m = pat.match((a.activity_id or "").strip())
        if not m:
            continue
        pre, digits = m.group(1), m.group(2)
        counts[pre] = counts.get(pre, 0) + 1
        parsed.append((pre, int(digits), len(digits)))

    if parsed:
        prefix = max(counts, key=counts.get)     # the project's dominant scheme
        same   = [(n, w) for (p, n, w) in parsed if p == prefix]
        top    = max(n for n, _ in same)
        width  = max(w for _, w in same)
        current = ((top // 10) + 1) * 10
    else:
        prefix, width, current = "A", 4, 1000

    while project.get_activity(activity_id=f"{prefix}{current:0{width}d}"):
        current += 10
    return f"{prefix}{current:0{width}d}"


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


# Actions that READ the schedule and report on it. They never mutate anything,
# so counting one as "an edit applied" is how the tool ends up announcing
# "Applied 5 edits" after running five reports — and how the agent, reading
# that same record back, tells the user it wired logic it never wired.
ADVISORY_ACTIONS = frozenset({"recommend_logic", "read_document", "describe_brain",
                              "wbs_flow_report", "find_duplicates",
                              "schedule_preview", "normalize_plan",
                              "bridge_folder", "backward_report",
                              "procurement_report", "ripple_preview",
                              "procurement_map", "procurement_story"})


def is_advisory(action: str) -> bool:
    return (action or "").lower().strip() in ADVISORY_ACTIONS


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
        elif action == "move_wbs":
            return _move_wbs(project, command)
        elif action == "reorder_wbs":
            return _reorder_wbs(project, command)
        elif action == "delete_wbs":
            return _delete_wbs(project, command)
        elif action == "recommend_logic":
            return _recommend_logic(project, command)
        elif action == "read_document":
            return _read_document(project, command)
        elif action == "duplicate_wbs":
            return _duplicate_wbs(project, command)
        elif action == "copy_activities":
            return _copy_activities(project, command)
        elif action == "set_data_date":
            return _set_data_date(project, command)
        elif action == "update_relation":
            return _update_relation(project, command)
        elif action == "update_activity_type":
            return _update_activity_type(project, command)
        elif action == "update_progress":
            return _update_progress(project, command)
        elif action == "update_labor_units":
            return _update_labor_units(project, command)
        elif action in ("apply_crew_to_name", "set_crew_for_work"):
            return _apply_crew_to_name(project, command)
        elif action in ("fill_crew_defaults", "fill_crew"):
            return _fill_crew_defaults(project, command)
        elif action == "update_udf":
            return _update_udf(project, command)
        elif action == "set_udf_type":
            return _set_udf_type(project, command)
        elif action in ("describe_brain", "what_do_you_know"):
            return _describe_brain(project, command)
        elif action in ("wbs_flow_report", "folder_flow"):
            from engine import wbs_flow as _wf
            return True, _wf.report(project)
        elif action in ("find_duplicates", "duplicate_report"):
            from engine import wbs_flow as _wf
            return True, _wf.duplicates(project)
        elif action in ("fill_folder_from_template", "fill_folder", "match_folder"):
            return _fill_folder_from_template(project, command)
        elif action in ("match_subfolder_numbers", "renumber_subfolders",
                        "align_subfolders"):
            return _match_subfolder_numbers(project, command)
        elif action in ("schedule_preview", "what_if_schedule", "preview_schedule"):
            from engine import schedule_preview as _sp
            return True, _sp.report(project)
        elif action in ("normalize_plan", "diagnose_schedule", "health_plan"):
            from engine import normalize as _nz
            return True, _nz.plan(project, _BRAIN_FOR(project) if _BRAIN_FOR else None)
        elif action in ("normalize_logic", "normalize_schedule", "wire_all"):
            return _normalize_logic(project, command)
        elif action in ("requirements", "requirement", "check_requirements"):
            return _requirements(project, command)
        elif action in ("bridge_folder", "bridge"):
            return _bridge_folder(project, command)
        elif action in ("backward_report", "backward_flow"):
            from engine import bridge as _br
            return True, _br.backward_report(project)
        elif action in ("fix_backward", "clear_backward"):
            return _fix_backward(project, command)
        elif action in ("procurement_report", "wire_procurement", "link_lle"):
            return _wire_procurement(project, command)
        elif action in ("procurement_map", "delivery_map", "material_map"):
            return _procurement_map(project, command)
        elif action in ("procurement_story", "delivery_story"):
            return _procurement_story(project, command)
        elif action in ("procurement_cover", "cover_procurement_gaps"):
            return _procurement_cover(project, command)
        elif action in ("replicate_pattern", "copy_logic_pattern"):
            return _replicate_pattern(project, command)
        elif action in ("ripple_preview", "simulate_activity"):
            return _ripple(project, command, apply_it=False)
        elif action in ("ripple", "schedule_activity", "reflow_path"):
            return _ripple(project, command, apply_it=bool(command.get("apply")))
        elif action in ("bulk_rules", "if_then"):
            return _bulk_rules(project, command)
        elif action == "move_activity_wbs":
            return _move_activity_wbs(project, command)
        elif action == "move_activities":
            return _move_activities(project, command)
        elif action == "bulk_rename":
            return _bulk_rename(project, command)
        elif action == "bulk_update_duration":
            return _bulk_update_duration(project, command)
        elif action in ("set_progress", "set_status"):
            return _set_progress(project, command)
        elif action == "set_actual_date":
            return _set_actual_date(project, command)
        elif action == "update_planned_date":
            return _update_planned_date(project, command)
        elif action == "set_constraint":
            return _set_constraint(project, command)
        elif action == "clear_constraint":
            return _clear_constraint(project, command)
        elif action == "bulk_clear_constraints":
            return _bulk_clear_constraints(project, command)
        elif action == "bulk_append_name":
            return _bulk_append_name(project, command)
        elif action == "bulk_add_activity":
            return _bulk_add_activity(project, command)
        elif action == "bulk_create_wbs":
            return _bulk_create_wbs(project, command)
        elif action in ("add_wbs_for_each", "bulk_create_wbs_for_each"):
            return _add_wbs_for_each(project, command)
        elif action == "bulk_rename_activities":
            return _bulk_rename_activities(project, command)
        elif action == "bulk_update_activity_id":
            return _bulk_update_activity_id(project, command)
        elif action == "normalize_activity_ids":
            return _normalize_activity_ids(project, command)
        elif action == "set_wbs_color":
            return _set_wbs_color(project, command)
        else:
            return False, f"Unknown action: '{action}'"
    except EditError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error applying '{action}': {e}"


def apply_commands(project: Project, commands: List[Dict[str, Any]]) -> List[Tuple[bool, str]]:
    """Apply a list of edit commands in order. Returns list of (success, message) tuples.

    After all commands, re-runs a CPM forward/backward pass to keep the derived
    columns — early / late dates, total float, the critical path — current.
    It does NOT rewrite Start / Finish: editing a cell is not a reschedule, the
    same way it is not one in P6. Dates reflow when the user asks, via the
    Schedule (F9) action. Activities that arrive without a date are still
    seeded, so a newly added row is never blank."""
    from engine.schedule_model import compute_dates
    results = []
    any_ok = False
    stopped_at = None
    for i, cmd in enumerate(commands):
        ok, msg = apply_command(project, cmd)
        results.append((ok, msg))
        if ok:
            any_ok = True
        if not ok:
            stopped_at = i
            break  # Stop on first failure to avoid cascading bad state
    if stopped_at is not None and stopped_at + 1 < len(commands):
        # Commands after the failure are never attempted — one of them may
        # depend on what the failed command was supposed to create. zip()ing
        # this against the original command list in the caller used to just
        # truncate, so the back half of a batch vanished with no explanation:
        # a request for ten edits would read back as "3 applied" with the
        # other six never mentioned. Recorded explicitly instead.
        failed_action = commands[stopped_at].get("action", "?")
        for cmd in commands[stopped_at + 1:]:
            results.append((False, f"not attempted — batch stopped after the "
                            f"'{failed_action}' failure above"))
    if any_ok:
        try:
            compute_dates(project, apply_dates=False)
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
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
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
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
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
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
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
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))
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
        # A paste carries the row as it was. Without these the pasted rows land
        # with blank dates, which is exactly what a paste is not supposed to do.
        constraint_type=cmd.get("constraint_type") or "",
        constraint_date=cmd.get("constraint_date") or None,
    )
    udfs = cmd.get("udfs")
    if isinstance(udfs, dict):
        new_act.udfs = {str(k): str(v) for k, v in udfs.items() if v not in (None, "")}
    # A new activity doing work the schedule already knows inherits its crew.
    # Typing "Install High Steel Area 9" should not mean filling the count in
    # by hand again when Areas 1-8 all say the same thing.
    if cmd.get("inherit_crew", True):
        try:
            ckey = electricians_field(project)
            if not new_act.udfs.get(ckey):
                learned = {d["match"]: d["crew"] for d in crew_defaults(project, ckey)}
                v = learned.get(_norm_name(name))
                if v is not None:
                    new_act.udfs[ckey] = str(v)
        except Exception:
            pass        # a convenience must never block adding an activity
    project.activities.append(new_act)
    project.build_lookups()
    return True, f"Added activity '{act_id} — {name}' ({duration_days}d) to WBS '{wbs.name}'"


def _delete_activity(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
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
        raise EditError("Predecessor — " + _no_activity(project, cmd.get("predecessor_id") or cmd.get("predecessor_name")))
    if not succ_matches:
        raise EditError("Successor — " + _no_activity(project, cmd.get("successor_id") or cmd.get("successor_name")))
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
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name")))
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


def _set_wbs_color(project: Project, cmd: Dict) -> Tuple[bool, str]:
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name")
                                or cmd.get("wbs_uid")))
    color = (cmd.get("color") or "").strip()
    if color and not color.startswith("#"):
        raise EditError("set_wbs_color needs a hex color like #3b82f6, or empty to clear")
    wbs.color = color or None
    if color:
        return True, f"Set color of '{wbs.name}' to {color}"
    return True, f"Cleared color on '{wbs.name}'"


def _match_subfolder_numbers(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Renumber child folders so their number matches their parent's.

    A copied phase carries subfolder names from the area it was copied from —
    Gen 312's children still saying "Gen 311 - JER". The PARENT numbers are
    the ones the user trusts, so each child's number is replaced with its
    parent's, the separator after the number is normalised to " - ", and
    everything else in the child's name is kept exactly as it was.

    Nothing is guessed: a child with more than one number in its name, or a
    child that shares the parent's prefix but has no number at all, is left
    alone and NAMED in the report so the user can decide. Parents without
    exactly one number ("Generators", "Phase 2") are containers, not part of
    the pattern, and are walked through rather than matched.
    """
    import re as _re

    scope = _find_wbs(project, cmd.get("wbs_code"),
                      cmd.get("wbs_name") or cmd.get("scope"), cmd.get("wbs_uid"))
    if not scope:
        raise EditError(_no_wbs(project, cmd.get("wbs_name") or cmd.get("scope")
                                or cmd.get("wbs_code")))

    by_parent: Dict[str, List[WBSNode]] = {}
    for w in project.wbs_nodes:
        by_parent.setdefault(w.parent_uid, []).append(w)

    # the scope's whole subtree — the pattern holds at every level under it
    subtree = [scope]
    i = 0
    while i < len(subtree):
        subtree.extend(by_parent.get(subtree[i].uid, []))
        i += 1

    _num = _re.compile(r"\d+")
    renamed, skipped = [], []
    for parent in subtree:
        children = by_parent.get(parent.uid, [])
        if not children:
            continue
        pnums = _num.findall(parent.name)
        if len(pnums) != 1:
            continue                     # a container folder — walk through it
        pnum = pnums[0]
        prefix = (parent.name.split() or [""])[0].lower()
        for ch in children:
            cnums = _num.findall(ch.name)
            if len(cnums) > 1:
                skipped.append(f"'{ch.name}' — more than one number in its "
                               f"name, left alone")
                continue
            if not cnums:
                # "Commissioning" under Gen 312 is not part of the pattern;
                # "Gen - JER" is — it shares the parent's prefix and lost its
                # number — so it is reported rather than silently passed over.
                if prefix and ch.name.lower().startswith(prefix):
                    skipped.append(f"'{ch.name}' — no number in its name, "
                                   f"left alone")
                continue
            new = _num.sub(pnum, ch.name, count=1)
            # normalise the separator after the number — "-JER", " -JER" and
            # "  -  JER" all become " - JER"; hyphens elsewhere are untouched
            new = _re.sub(rf"({_re.escape(pnum)})\s*-\s*", r"\1 - ", new,
                          count=1)
            if new == ch.name:
                continue
            renamed.append(f"'{ch.name}' → '{new}'  (under '{parent.name}')")
            ch.name = new

    project.build_lookups()
    if not renamed and not skipped:
        return True, (f"Checked every folder under '{scope.name}' — all "
                      f"subfolder numbers already match their parent.")
    lines = [f"Matched subfolder numbering under '{scope.name}' — "
             f"{len(renamed)} folder{'s' if len(renamed) != 1 else ''} renamed."]
    lines += [f"  {r}" for r in renamed[:40]]
    if len(renamed) > 40:
        lines.append(f"  …and {len(renamed) - 40} more")
    if skipped:
        lines.append("Left alone — tell me the number to use and I'll rename "
                     "them:")
        lines += [f"  {s}" for s in skipped[:10]]
    return True, "\n".join(lines)


def _add_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    name = cmd.get("name", "").strip()
    code = cmd.get("code", name[:20]).strip()
    if not name:
        raise EditError("name is required for add_wbs")
    parent = None
    if cmd.get("parent_uid") or cmd.get("parent_code") or cmd.get("parent_name"):
        parent = _find_wbs(project, cmd.get("parent_code"), cmd.get("parent_name"),
                           cmd.get("parent_uid"))
        if not parent:
            raise EditError(f"Parent WBS not found: "
                            f"{cmd.get('parent_uid') or cmd.get('parent_code') or cmd.get('parent_name')}")
    # Sequence number: sit AFTER the last existing sibling so P6 displays it last
    parent_uid = parent.uid if parent else None
    siblings = [w for w in project.wbs_nodes if w.parent_uid == parent_uid]
    next_seq = (max(s.sequence_num for s in siblings) + 10) if siblings else 0
    # A caller may supply the uid so it can reference the folder in the SAME
    # batch — "create this folder and paste into it" has to be one undo step,
    # and pasting by name would land in a pre-existing folder of that name.
    new_uid = str(cmd.get("new_wbs_uid") or "").strip()
    if new_uid and any(w.uid == new_uid for w in project.wbs_nodes):
        raise EditError(f"A WBS with uid '{new_uid}' already exists")
    new_wbs = WBSNode(
        uid=new_uid or _new_uid(),
        name=name,
        code=code,
        parent_uid=parent_uid,
        sequence_num=next_seq,
    )
    project.wbs_nodes.append(new_wbs)
    project.build_lookups()
    return True, f"Added WBS node '{code} — {name}'" + (f" under '{parent.name}'" if parent else " at root")


def _recommend_logic(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Report the logic a schedule is missing, checked against the dates it has.

    Advisory only — it changes nothing. Recommending a tie and applying it are
    deliberately separate steps, because a wrong relationship is more expensive
    to unpick than a missing one.
    """
    from engine.logic_advisor import (milestone_report, area_report,
                                      procurement_report, CONFIRMS, CONFLICT)

    scope = (cmd.get("scope") or "milestones").strip().lower()
    name = cmd.get("wbs_name") or cmd.get("area")

    if scope in ("wbs", "area") and name:
        # A folder can hold anywhere from a handful of activities to a whole
        # phase's worth. 150 covers a real room/area/phase-slice in full; past
        # that, dumping every row would cost more context than it is worth and
        # the agent is told to narrow to a sub-folder instead.
        rep = area_report(project, name, sample=150)
        if "error" in rep:
            raise EditError(rep["error"])
        a, s = rep["area"], rep["summary"]
        lines = [
            f"{a['path']} — {a['activity_count']} activities, "
            f"{len(a['sub_folders'])} sub-folders, "
            f"{a['date_range']['earliest_start']} to {a['date_range']['latest_finish']}.",
            f"Logic gaps: {a['logic']['missing_predecessor']} without a predecessor, "
            f"{a['logic']['missing_successor']} without a successor.",
            f"Proposed sequence ties: {s['sequence_ties_proposed']} "
            f"({s['confirms']} reproduce the dates, {s['conflicts']} contradict them).",
        ]
        if s["installed_before_delivery"]:
            lines.append(f"WARNING: {s['installed_before_delivery']} item(s) are dated to be "
                         f"installed before the equipment is delivered.")
        if a["sub_folders"]:
            lines.append("Sub-folders: " + ", ".join(
                f"{k['name']} ({k['activity_count']})" for k in a["sub_folders"]))
        shown = a["activities_shown"]
        lines.append("")
        if shown < a["activity_count"]:
            lines.append(f"ACTIVITIES (first {shown} of {a['activity_count']}, "
                         f"earliest start first — ask about a specific sub-folder "
                         f"above to see the rest in full):")
        else:
            lines.append(f"ACTIVITIES (all {shown}):")
        for act in a["activities"]:
            flags = []
            if not act["linked"]:
                flags.append("UNLINKED")
            if act["constraint"]:
                flags.append(act["constraint"])
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {act['activity_id']} — {act['name']}  "
                         f"{act['start']} -> {act['finish']} ({act['duration_days']}d, "
                         f"{act['status']}){flag_str}")
        if s["sequence_ties_proposed"]:
            lines.append("")
            lines.append("PROPOSED SEQUENCE TIES:")
            for r in rep["sequence_recommendations"][:30]:
                lines.append(f"  {r['predecessor_name']} -> {r['successor_name']} "
                             f"[{r['verdict']}, implied lag {r['implied_lag_days']}d]")
        return True, "\n".join(lines)

    if scope == "procurement":
        rep = procurement_report(project, name)
        lines = [f"Long-lead scope: {rep['scope']} — {rep['matched']} supply lines matched "
                 f"to installation work; {rep['installed_before_delivery']} scheduled to be "
                 f"installed BEFORE delivery."]
        for i in rep["items"][:15]:
            mark = "!! " if i["installed_before_delivery"] else "   "
            lines.append(f"{mark}{i['supply_name']} (arrives {i['supply_finish']}) -> "
                         f"{i['first_install_name']} (starts {i['first_install_start']}), "
                         f"{i['implied_lag_days']}d")
        return True, "\n".join(lines)

    rep = milestone_report(project, limit_per_milestone=int(cmd.get("limit") or 3))
    s = rep["summary"]
    lines = [
        f"{rep['unanchored_count']} of {rep['milestone_count']} milestones have nothing driving them.",
        f"Candidate ties: {s[CONFIRMS]} reproduce the dates already set "
        f"(their Start On constraints can be dropped), {s['slack']} are valid with float, "
        f"{s[CONFLICT]} contradict the dates as scheduled.",
    ]
    for item in rep["milestones"]:
        if item["has_predecessor"] or not item["drivers"]:
            continue
        d = item["drivers"][0]
        lines.append(
            f"  {item['name']} ({item['date']}) <- {d['predecessor_name']} "
            f"[{d['verdict']}, implied lag {d['implied_lag_days']}d]")
        if len(lines) > 40:
            lines.append("  …")
            break
    return True, "\n".join(lines)


def _delete_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Delete a WBS folder and everything nested under it.

    What happens to the contents is explicit, because the two intents are very
    different and one of them destroys work:
      delete_contents=False (default) — the folder and its sub-folders go, and
        every activity in the branch moves up to the deleted folder's parent.
        Nothing is lost. A top-level folder's activities move to the first
        remaining root folder.
      delete_contents=True — the branch and every activity in it are removed,
        along with any relationship touching those activities.
    """
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))

    # the whole branch: this folder plus every descendant
    branch = {wbs.uid}
    changed = True
    while changed:
        changed = False
        for w in project.wbs_nodes:
            if w.parent_uid in branch and w.uid not in branch:
                branch.add(w.uid)
                changed = True

    doomed = [a for a in project.activities if a.wbs_uid in branch]
    delete_contents = bool(cmd.get("delete_contents"))

    if not delete_contents and doomed:
        # Move the work somewhere real. The parent is the natural home; for a
        # root folder fall back to another root so nothing is orphaned into a
        # folder that does not exist.
        new_home = wbs.parent_uid
        if not new_home or new_home in branch:
            new_home = next((w.uid for w in project.wbs_nodes if w.uid not in branch), None)
        if not new_home:
            raise EditError(
                f"'{wbs.name}' holds {len(doomed)} activities and there is no other "
                f"folder to move them to — delete the activities too, or add a folder first")
        for a in doomed:
            a.wbs_uid = new_home
        moved = len(doomed)
    else:
        uids = {a.uid for a in doomed}
        project.activities = [a for a in project.activities if a.uid not in uids]
        project.relations = [r for r in project.relations
                             if r.predecessor_uid not in uids and r.successor_uid not in uids]
        moved = 0

    folders = len(branch)
    project.wbs_nodes = [w for w in project.wbs_nodes if w.uid not in branch]
    project.build_lookups()

    what = f"'{wbs.name}'" + (f" and {folders - 1} sub-folder(s)" if folders > 1 else "")
    if delete_contents:
        return True, f"Deleted {what} and {len(doomed)} activity/activities"
    return True, f"Deleted {what}" + (f"; moved {moved} activity/activities up" if moved else "")


def _reorder_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Move a folder up or down among its siblings — reordering only, the parent
    never changes (use move_wbs to re-parent).

    Sibling order is (sequence_num, name). Imported files routinely give every
    folder the same sequence_num, so a bare swap of two equal numbers would do
    nothing visible — the siblings are renumbered 0,10,20… in their current
    displayed order first, which makes every swap actually move the folder.
    """
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))
    direction = (cmd.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        raise EditError("direction must be 'up' or 'down'")

    siblings = [w for w in project.wbs_nodes
                if (w.parent_uid or None) == (wbs.parent_uid or None)]
    siblings.sort(key=lambda w: (w.sequence_num, w.name))
    for i, w in enumerate(siblings):
        w.sequence_num = i * 10

    idx = next(i for i, w in enumerate(siblings) if w.uid == wbs.uid)
    swap_with = idx - 1 if direction == "up" else idx + 1
    if swap_with < 0 or swap_with >= len(siblings):
        edge = "first" if direction == "up" else "last"
        return True, f"'{wbs.name}' is already {edge} — nothing to move"

    other = siblings[swap_with]
    wbs.sequence_num, other.sequence_num = other.sequence_num, wbs.sequence_num
    return True, f"Moved '{wbs.name}' {direction}"


def _move_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Re-parent a WBS folder (with everything under it — child folders and their
    activities move implicitly, since membership is by parent_uid / wbs_uid).

    Pass parent_uid/parent_code/parent_name to nest it, or omit them all (or
    parent_code=None) to move it to the root. The grid passes uids, because
    name matching is a substring match and this schedule has 38 folders whose
    names repeat — a cut-and-paste must land on the folder that was clicked.
    """
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"), cmd.get("wbs_uid"))
    if not wbs:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))

    to_root = cmd.get("to_root") or not (cmd.get("parent_uid") or cmd.get("parent_code")
                                         or cmd.get("parent_name"))
    parent = None
    if not to_root:
        parent = _find_wbs(project, cmd.get("parent_code"), cmd.get("parent_name"),
                           cmd.get("parent_uid"))
        if not parent:
            raise EditError(f"Target WBS not found: "
                            f"{cmd.get('parent_uid') or cmd.get('parent_code') or cmd.get('parent_name')}")
        if parent.uid == wbs.uid:
            raise EditError(f"Cannot move '{wbs.name}' into itself")
        # Walking up from the target must not reach the node being moved,
        # otherwise the tree would become a cycle and orphan the branch.
        by_uid = {w.uid: w for w in project.wbs_nodes}
        seen, cur = set(), parent
        while cur is not None and cur.uid not in seen:
            if cur.uid == wbs.uid:
                raise EditError(
                    f"Cannot move '{wbs.name}' into '{parent.name}' — "
                    f"that folder sits underneath it")
            seen.add(cur.uid)
            cur = by_uid.get(cur.parent_uid) if cur.parent_uid else None

    new_parent_uid = parent.uid if parent else None
    if wbs.parent_uid == new_parent_uid:
        return True, f"WBS '{wbs.name}' is already there"

    siblings = [w for w in project.wbs_nodes
                if w.parent_uid == new_parent_uid and w.uid != wbs.uid]
    wbs.parent_uid = new_parent_uid
    wbs.sequence_num = (max(s.sequence_num for s in siblings) + 10) if siblings else 0
    project.build_lookups()
    where = f"under '{parent.name}'" if parent else "to the root"
    return True, f"Moved WBS '{wbs.name}' {where}"


def _duplicate_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Copy a WBS folder, everything nested under it, its activities, and the
    logic *between* those activities. Repetitive structures (rooms, lineups,
    levels) are built once and stamped out.

      wbs_name / wbs_code : the branch to copy
      new_name            : name for the copy (default "<name> (copy)")
      parent_name/_code   : where to put it (default: alongside the original)
      count               : how many copies (default 1)
    """
    src = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"))
    if not src:
        raise EditError(_no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name")))

    parent_uid = src.parent_uid
    if cmd.get("parent_code") or cmd.get("parent_name"):
        tgt = _find_wbs(project, cmd.get("parent_code"), cmd.get("parent_name"))
        if not tgt:
            raise EditError("Target " + _no_wbs(project, cmd.get("parent_code") or cmd.get("parent_name")))
        parent_uid = tgt.uid

    try:
        count = max(1, int(cmd.get("count", 1)))
    except (TypeError, ValueError):
        count = 1

    # the branch: source + all descendants
    children_of: Dict[str, List[WBSNode]] = {}
    for w in project.wbs_nodes:
        children_of.setdefault(w.parent_uid, []).append(w)
    branch: List[WBSNode] = []

    def collect(node):
        branch.append(node)
        for c in children_of.get(node.uid, []):
            collect(c)
    collect(src)
    branch_uids = {w.uid for w in branch}
    acts_in = [a for a in project.activities if a.wbs_uid in branch_uids]

    made_names = []
    for n in range(count):
        base = (cmd.get("new_name") or f"{src.name} (copy)").strip()
        new_name = base if count == 1 else f"{base} {n + 1}"
        while any(w.name == new_name for w in project.wbs_nodes):
            new_name += "*"

        wbs_map: Dict[str, str] = {}
        for w in branch:
            new_uid = _new_uid()
            wbs_map[w.uid] = new_uid
            siblings = [x for x in project.wbs_nodes
                        if x.parent_uid == (parent_uid if w is src else wbs_map.get(w.parent_uid))]
            project.wbs_nodes.append(WBSNode(
                uid=new_uid,
                name=new_name if w is src else w.name,
                code=(w.code or "")[:20],
                parent_uid=parent_uid if w is src else wbs_map.get(w.parent_uid),
                sequence_num=(max(s.sequence_num for s in siblings) + 10) if siblings else 0,
            ))
        project.build_lookups()

        act_map: Dict[str, str] = {}
        for a in acts_in:
            new_id = _next_activity_id(project)
            new_uid = _new_uid()
            act_map[a.uid] = new_uid
            project.activities.append(Activity(
                uid=new_uid,
                activity_id=new_id,
                name=a.name,
                wbs_uid=wbs_map.get(a.wbs_uid, parent_uid or a.wbs_uid),
                calendar_uid=a.calendar_uid,
                activity_type=a.activity_type,
                status="Not Started",
                planned_duration=a.planned_duration,
                remaining_duration=a.planned_duration,
                constraint_type=a.constraint_type,
                constraint_date=a.constraint_date,
            ))
            project.build_lookups()

        # carry over logic that lived entirely inside the branch
        for r in list(project.relations):
            if r.predecessor_uid in act_map and r.successor_uid in act_map:
                project.relations.append(Relation(
                    uid=_new_uid(),
                    predecessor_uid=act_map[r.predecessor_uid],
                    successor_uid=act_map[r.successor_uid],
                    type=r.type, lag=r.lag,
                ))
        made_names.append(new_name)

    project.build_lookups()
    return True, (f"Duplicated '{src.name}' → {', '.join(made_names)} "
                  f"({len(acts_in)} activities each)")


def _set_data_date(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Set the project data date (the 'as of' line P6 schedules from).

      data_date : YYYY-MM-DD
      also_planned_start : bool — move the project start to match (default False)

    Dates are not recalculated here; run the scheduler afterwards to reflow.
    """
    raw = (cmd.get("data_date") or cmd.get("date") or "").strip()
    if not raw:
        raise EditError("data_date is required (YYYY-MM-DD)")
    iso = str(raw)[:10]
    try:
        _dt.date.fromisoformat(iso)
    except ValueError:
        raise EditError(f"'{raw}' is not a valid date — use YYYY-MM-DD")
    old = str(project.data_date)[:10] if project.data_date else "not set"
    project.data_date = iso
    msg = f"Data date {old} → {iso}"
    if cmd.get("also_planned_start"):
        project.planned_start = iso
        msg += " (project start moved to match)"
    return True, msg


def _copy_activities(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Copy a set of activities into a folder, carrying the logic *between* them.

    This is the in-schedule counterpart to copy_wbs_branch: relationships whose
    two ends are both inside the selection come along, and links that leave the
    selection are dropped (a copy cannot inherit a predecessor it does not own
    without silently rewiring the schedule). Full row data — duration, type,
    constraints, dates — travels with the copy; status resets to Not Started,
    since a copy has not been worked yet.

      activity_ids : ids to copy
      wbs_name / wbs_code : destination folder (default: each row's own folder)
      count : how many copies (default 1)
    """
    ids = cmd.get("activity_ids") or []
    if not ids:
        raise EditError("activity_ids is required for copy_activities")

    # An unresolvable id normally fails the whole command, before anything is
    # created — a typo in one row must not leave a half-copy behind.
    #
    # skip_missing relaxes that for one caller: the grid replaying its
    # clipboard, where the row list was captured earlier and a row may since
    # have been deleted. There, dropping the entire paste is the wrong answer;
    # the surviving rows should still land. Agent-issued commands leave the
    # flag off and keep the strict check.
    skip_missing = bool(cmd.get("skip_missing"))
    src_acts = []
    seen = set()
    missing = []
    for aid in ids:
        matches = _find_activity(project, aid)
        if not matches:
            if not skip_missing:
                raise EditError(_no_activity(project, aid))
            missing.append(aid)
            continue
        a = matches[0]
        if a.uid not in seen:
            seen.add(a.uid)
            src_acts.append(a)
    if not src_acts:
        raise EditError(
            f"None of the {len(ids)} copied activities still exist — "
            f"nothing to copy from")

    target_uid = None
    if cmd.get("wbs_uid") or cmd.get("wbs_code") or cmd.get("wbs_name"):
        tgt = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                        cmd.get("wbs_uid"))
        if not tgt:
            raise EditError(f"Target WBS not found: "
                            f"{cmd.get('wbs_uid') or cmd.get('wbs_code') or cmd.get('wbs_name')}")
        target_uid = tgt.uid

    try:
        count = max(1, int(cmd.get("count", 1)))
    except (TypeError, ValueError):
        count = 1

    src_uids = {a.uid for a in src_acts}
    internal = [r for r in project.relations
                if r.predecessor_uid in src_uids and r.successor_uid in src_uids]
    boundary = [r for r in project.relations
                if (r.predecessor_uid in src_uids) != (r.successor_uid in src_uids)]

    total_new = 0
    rels_made = 0
    for _ in range(count):
        act_map: Dict[str, str] = {}
        for a in src_acts:
            new_uid = _new_uid()
            new_id = _next_activity_id(project)
            act_map[a.uid] = new_uid
            project.activities.append(Activity(
                uid=new_uid,
                activity_id=new_id,
                name=a.name,
                wbs_uid=target_uid or a.wbs_uid,
                calendar_uid=a.calendar_uid,
                activity_type=a.activity_type,
                status="Not Started",
                planned_duration=a.planned_duration,
                remaining_duration=a.planned_duration,
                planned_start=a.planned_start,
                planned_finish=a.planned_finish,
                constraint_type=a.constraint_type,
                constraint_date=a.constraint_date,
            ))
            project.build_lookups()
            total_new += 1
        for r in internal:
            project.relations.append(Relation(
                uid=_new_uid(),
                predecessor_uid=act_map[r.predecessor_uid],
                successor_uid=act_map[r.successor_uid],
                type=r.type, lag=r.lag,
            ))
            rels_made += 1

    project.build_lookups()
    msg = f"Copied {total_new} activity/activities carrying {rels_made} relationship(s)"
    if boundary:
        msg += f"; {len(boundary)} link(s) to activities outside the selection were not carried"
    if missing:
        msg += f"; {len(missing)} copied row(s) no longer exist and were skipped"
    return True, msg


def _wd_delta(d1, d2, wd, hol) -> int:
    """Working days from d1 to d2 on a calendar. Negative if d2 is earlier."""
    import datetime as _d
    if d2 == d1:
        return 0
    step = 1 if d2 > d1 else -1
    days, cur = 0, d1
    guard = 0
    while cur != d2 and guard < 4000:
        cur += _d.timedelta(days=step)
        guard += 1
        if cur.weekday() in wd and ((not hol) or cur.isoformat() not in hol):
            days += step
    return days


def _fill_folder_from_template(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Build a thin folder up to match one that is already right — without
    disturbing a thing that is already in it.

    The situation this exists for: several areas are meant to run the same way,
    one of them has been fully built out and wired, and the rest were started
    and left short. Copying the whole template over would duplicate the rows
    that already exist and orphan the logic hanging off them; adding the
    missing rows by hand is hours of work per area.

    What it does, and nothing else:
      · matches rows by WORK, not by exact name — "Pull Wire MV 101" and
        "Pull Wire MV 105" are the same task in two places (the same
        normalisation the crew-fill uses), so an area that already has the
        work under its own name is left alone
      · adds ONLY the template rows with no counterpart in the target
      · re-creates the template's internal logic between rows in the target,
        skipping any tie that is already there
      · NEVER deletes an activity, NEVER deletes or repoints a relationship,
        and NEVER edits a row that already existed. Logic already in the
        target survives untouched — that is the whole point.

    Names carry the area across when the folder name appears in them, so
    "Set CRAHs MV 101" lands in MV 105 as "Set CRAHs MV 105". When it does
    not appear, the template's name is used as-is.

    Dates are placed at the same working-day OFFSET from the target folder's
    start as they sit from the template's, so the shape is preserved rather
    than every new row piling onto one day. Edits do not reschedule, so run
    Schedule afterwards to let the logic settle them.

      template_wbs / source_wbs : the folder to copy the pattern FROM
      target_wbs                : the folder to fill (or targets: [..])
      with_logic                : default true; false adds rows only
      preview                   : default false; true reports and changes nothing
    """
    import datetime as _d

    src_ref = (cmd.get("template_wbs") or cmd.get("source_wbs")
               or cmd.get("template_wbs_name") or cmd.get("source_wbs_name"))
    if not src_ref:
        raise EditError("template_wbs is required — the folder to copy the pattern from")
    src = _find_wbs(project, src_ref, src_ref, src_ref)
    if not src:
        raise EditError("Template " + _no_wbs(project, src_ref))

    targets_ref = cmd.get("targets") or cmd.get("target_wbs") or cmd.get("target_wbs_name")
    if not targets_ref:
        raise EditError("target_wbs is required — the folder to fill")
    if isinstance(targets_ref, str):
        targets_ref = [targets_ref]

    tgts = []
    for ref in targets_ref:
        w = _find_wbs(project, ref, ref, ref)
        if not w:
            raise EditError("Target " + _no_wbs(project, ref))
        if w.uid == src.uid:
            raise EditError(f"'{w.name}' is the template — pick a different folder to fill")
        tgts.append(w)

    with_logic = cmd.get("with_logic", True)
    preview = bool(cmd.get("preview"))

    by_folder: Dict[str, List[Activity]] = {}
    for a in project.activities:
        by_folder.setdefault(a.wbs_uid, []).append(a)

    src_acts = by_folder.get(src.uid, [])
    if not src_acts:
        raise EditError(f"Template folder '{src.name}' has no activities to copy")

    def _earliest(acts):
        ds = [str(a.planned_start)[:10] for a in acts if a.planned_start]
        return min(ds) if ds else None

    src_start_s = _earliest(src_acts)
    src_uids = {a.uid for a in src_acts}
    src_internal = [r for r in project.relations
                    if r.predecessor_uid in src_uids and r.successor_uid in src_uids]

    lines, total_added, total_ties = [], 0, 0
    for tgt in tgts:
        tgt_acts = by_folder.get(tgt.uid, [])
        have = {_norm_name(a.name): a for a in tgt_acts}
        missing = [a for a in src_acts if _norm_name(a.name) not in have]

        tgt_start_s = _earliest(tgt_acts) or src_start_s
        added_map: Dict[str, Activity] = {}       # src uid -> new activity

        if preview:
            lines.append(f"{tgt.name}: would add {len(missing)} of "
                         f"{len(src_acts)} — {len(tgt_acts)} already there")
            for a in missing[:8]:
                lines.append(f"    + {a.name}")
            if len(missing) > 8:
                lines.append(f"    +{len(missing) - 8} more")
            continue

        for a in missing:
            wd, hol, hpd = _act_calendar(project, a)
            new_start = a.planned_start
            if src_start_s and tgt_start_s and a.planned_start:
                try:
                    off = _wd_delta(_d.date.fromisoformat(src_start_s),
                                    _d.date.fromisoformat(str(a.planned_start)[:10]),
                                    wd, hol)
                    new_start = _add_working_days(
                        _d.date.fromisoformat(tgt_start_s), off, wd, hol).isoformat()
                except ValueError:
                    new_start = a.planned_start
            dur_d = (a.planned_duration or 0.0) / hpd
            new_finish = new_start
            if new_start and dur_d > 0:
                try:
                    new_finish = _add_working_days(
                        _d.date.fromisoformat(new_start), _span_days(dur_d),
                        wd, hol).isoformat()
                except ValueError:
                    new_finish = new_start

            name = a.name or ""
            if src.name and tgt.name and src.name in name:
                name = name.replace(src.name, tgt.name)

            new = Activity(
                uid=_new_uid(), activity_id=_next_activity_id(project),
                name=name, wbs_uid=tgt.uid, calendar_uid=a.calendar_uid,
                activity_type=a.activity_type, status="Not Started",
                planned_duration=a.planned_duration,
                remaining_duration=a.planned_duration,
                planned_start=new_start, planned_finish=new_finish,
            )
            if getattr(a, "udfs", None):
                new.udfs = dict(a.udfs)
            project.activities.append(new)
            project.build_lookups()
            added_map[a.uid] = new
            total_added += 1

        ties = 0
        if with_logic:
            # Where does each template row live in the target? Either a row
            # that was already there doing that work, or one just added.
            counterpart: Dict[str, Activity] = {}
            for a in src_acts:
                if a.uid in added_map:
                    counterpart[a.uid] = added_map[a.uid]
                else:
                    m = have.get(_norm_name(a.name))
                    if m is not None:
                        counterpart[a.uid] = m
            existing = {(r.predecessor_uid, r.successor_uid) for r in project.relations}
            for r in src_internal:
                p = counterpart.get(r.predecessor_uid)
                s = counterpart.get(r.successor_uid)
                if p is None or s is None or p.uid == s.uid:
                    continue
                if (p.uid, s.uid) in existing:
                    continue          # already tied — leave the user's logic alone
                project.relations.append(Relation(
                    uid=_new_uid(), predecessor_uid=p.uid, successor_uid=s.uid,
                    type=r.type, lag=r.lag))
                existing.add((p.uid, s.uid))
                ties += 1
            total_ties += ties

        project.build_lookups()
        lines.append(f"{tgt.name}: +{len(missing)} activities, +{ties} ties "
                     f"({len(tgt_acts)} already there, untouched)")

    if preview:
        return True, ("PREVIEW — nothing changed.\n"
                      f"Template '{src.name}' has {len(src_acts)} activities.\n"
                      + "\n".join(lines)
                      + "\nRun again without preview to apply.")

    head = (f"Filled from '{src.name}': +{total_added} activities, "
            f"+{total_ties} relationships across "
            f"{len(tgts)} folder{'s' if len(tgts) != 1 else ''}. "
            f"Nothing existing was changed or removed.")
    tail = "\n  " + "\n  ".join(lines) if lines else ""
    return True, head + tail + "\n  Run Schedule to let the new logic settle the dates."


def _move_activities(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Move a set of activities into a folder — the paste half of cut-and-paste.

    Unlike copy+delete this keeps each row's identity: the activity_id, dates,
    constraints, UDFs and *all* of its logic survive, including links to
    activities outside the selection. Only the folder changes.

      activity_ids : ids to move
      wbs_uid / wbs_code / wbs_name : destination folder
      skip_missing : drop ids that no longer exist instead of failing (the
                     grid sets this when replaying a clipboard; agent
                     commands leave it off so a typo is reported)
    """
    ids = cmd.get("activity_ids") or []
    if not ids:
        raise EditError("activity_ids is required for move_activities")
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"), cmd.get("wbs_uid"))
    if not wbs:
        raise EditError("Target " + _no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))
    skip_missing = bool(cmd.get("skip_missing"))
    # resolve everything first — a half-finished move is worse than none
    resolved, missing = [], []
    for aid in ids:
        matches = _find_activity(project, aid)
        if not matches:
            if not skip_missing:
                raise EditError(_no_activity(project, aid))
            missing.append(aid)
            continue
        resolved.append(matches[0])
    if not resolved:
        raise EditError(f"None of the {len(ids)} cut activities still exist")
    moved = 0
    for a in resolved:
        if a.wbs_uid != wbs.uid:
            moved += 1
        a.wbs_uid = wbs.uid
    project.build_lookups()
    msg = f"Moved {moved} activity/activities to WBS '{wbs.name}'"
    if missing:
        msg += f"; {len(missing)} row(s) no longer exist and were skipped"
    return True, msg


def _update_relation(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """Change an existing relationship's type and/or lag in place."""
    pred = _find_activity(project, cmd.get("predecessor_id"))
    succ = _find_activity(project, cmd.get("successor_id"))
    if not pred or not succ:
        raise EditError("Both predecessor_id and successor_id are required")
    p_uid, s_uid = pred[0].uid, succ[0].uid
    rel = next((r for r in project.relations
                if r.predecessor_uid == p_uid and r.successor_uid == s_uid), None)
    if not rel:
        raise EditError(f"No relationship {pred[0].activity_id} → {succ[0].activity_id}")
    rel_type_map = {"fs": "Finish to Start", "ss": "Start to Start",
                    "ff": "Finish to Finish", "sf": "Start to Finish"}
    if cmd.get("type"):
        rel.type = rel_type_map.get(str(cmd["type"]).lower(), rel.type)
    if cmd.get("lag_days") is not None:
        rel.lag = float(cmd["lag_days"]) * 8.0
    return True, (f"{pred[0].activity_id} → {succ[0].activity_id} set to "
                  f"{rel.type} (lag {rel.lag / 8.0:g}d)")


_ACTIVITY_TYPES = {
    "task dependent": "Task Dependent",
    "resource dependent": "Resource Dependent",
    "level of effort": "Level of Effort",
    "wbs summary": "WBS Summary",
    "start milestone": "Start Milestone",
    "finish milestone": "Finish Milestone",
}


def _update_activity_type(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """Change an activity's type. Milestones are zero-duration by definition."""
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    raw = str(cmd.get("activity_type", "")).strip()
    new_type = _ACTIVITY_TYPES.get(raw.lower())
    if not new_type:
        raise EditError(f"Unknown activity type '{raw}'")
    for a in matches:
        a.activity_type = new_type
        if "Milestone" in new_type:
            a.planned_duration = 0.0
            a.remaining_duration = 0.0
    return True, f"Set type '{new_type}' on {len(matches)} activity/activities"


def _update_progress(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """Set % complete, keeping status consistent with it."""
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    try:
        pct = float(cmd.get("percent_complete"))
    except (TypeError, ValueError):
        raise EditError("percent_complete must be a number between 0 and 100")
    pct = max(0.0, min(100.0, pct))
    for a in matches:
        a.percent_complete = pct
        a.status = ("Not Started" if pct == 0 else
                    "Completed" if pct >= 100 else "In Progress")
        a.remaining_duration = a.planned_duration * (1 - pct / 100.0)
    return True, f"Set {pct:g}% complete on {len(matches)} activity/activities"


def _span_days(dur_days: float) -> float:
    """
    Working days from start to finish for a given duration.

    P6 counts inclusively: a 5-day activity starting Monday finishes Friday.
    Mirrors _span_of in schedule_model so an edited date and a scheduled one
    land on the same day.
    """
    return max(0.0, dur_days - 1.0) if dur_days >= 1.0 else 0.0


def _add_working_days(start, days: float, wd, hol):
    """Add `days` working days to `start` on the given calendar."""
    import datetime as _d
    import math as _math
    d = start
    while not (d.weekday() in wd and ((not hol) or d.isoformat() not in hol)):
        d += _d.timedelta(days=1)
    remaining = int(_math.ceil(abs(days)))
    step = 1 if days >= 0 else -1
    added = 0
    while added < remaining:
        d += _d.timedelta(days=step)
        if d.weekday() in wd and ((not hol) or d.isoformat() not in hol):
            added += 1
    return d


def _act_calendar(project: Project, act: Activity):
    """The activity's calendar (work days + holidays), with Mon–Fri defaults."""
    cal = None
    for c in project.calendars or []:
        if c.uid == getattr(act, "calendar_uid", None):
            cal = c
            break
    if cal is None and project.calendars:
        cal = project.calendars[0]
    wd = (getattr(cal, "work_days", None) if cal else None) or frozenset({0, 1, 2, 3, 4})
    hol = (getattr(cal, "holidays", None) if cal else None) or frozenset()
    hpd = (getattr(cal, "hours_per_day", None) if cal else None) or 8.0
    return wd, hol, hpd


_START_CONSTRAINTS = {"start on", "must start on", "mandatory start",
                      "start on or after", "start on or before"}
_FINISH_CONSTRAINTS = {"finish on", "must finish on", "mandatory finish",
                       "finish on or after", "finish on or before"}


def _carry_constraint(a, field: str, new_date, old_date, both: bool = False):
    """
    Move an existing constraint along with a date the user just set.

    A constraint is a statement about where a date belongs, so re-dating the
    activity without it leaves the two contradicting each other — and the
    constraint wins on the next recalculation, which is exactly the "I type a
    date and it jumps back" behaviour. Half this schedule is pinned with
    Start On, so that hit most edits.

    The constraint TYPE is never changed and one is never invented: this only
    re-dates a pin that is already there. Clearing it stays an explicit act
    (clear_constraint), so removing a constraint still means removing it.

      start edit  + start pin  → pin moves to the new start
      finish edit + finish pin → pin moves to the new finish
      start edit  + finish pin → the finish travels with the start, so the pin
                                 shifts by the same offset rather than jumping
                                 to a start date it was never about
      finish edit + start pin  → the start did not move; the pin is left alone
                                 (unless `both`, i.e. a milestone, where the
                                 one date is both)

    Returns a note for the result message, or "" if nothing changed.
    """
    ct = (a.constraint_type or "").strip().lower()
    if not ct or not a.constraint_date:
        return ""
    import datetime as _d
    try:
        cur = _d.date.fromisoformat(str(a.constraint_date)[:10])
    except ValueError:
        return ""

    is_start, is_finish = ct in _START_CONSTRAINTS, ct in _FINISH_CONSTRAINTS
    if both and (is_start or is_finish):
        new_cd = new_date
    elif field == "start" and is_start:
        new_cd = new_date
    elif field == "finish" and is_finish:
        new_cd = new_date
    elif field == "start" and is_finish:
        # keep the gap the user had between the pinned finish and the start
        if not old_date:
            return ""
        try:
            old = _d.date.fromisoformat(str(old_date)[:10])
        except ValueError:
            return ""
        new_cd = cur + (new_date - old)
    else:
        return ""

    if new_cd == cur:
        return ""
    a.constraint_date = new_cd.isoformat()
    return f"; {a.constraint_type} constraint moved to {new_cd.isoformat()}"


def _update_planned_date(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Set a planned date directly — the no-constraint path the grid uses for
    rows that don't need one:
      field=start  → moves planned_start. On an unlinked activity the CPM
                     recompute holds it, so no pin is needed; the grid only
                     falls back to a Start On constraint when the row has
                     predecessors that would otherwise drive the date.
      field=finish → P6 semantics: the finish is start + duration, so typing
                     a finish adjusts the DURATION (on the activity's own
                     calendar). The start does not move — unlike a Finish On
                     constraint, which back-computes the start.

    If the activity already carries a constraint it is re-dated to match (see
    _carry_constraint), so the date the user typed is the date that survives
    the next recalculation. Pass move_constraint=false to set the date and
    leave the pin where it is.
    """
    import datetime as _d

    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    if len(matches) > 1:
        raise EditError(f"Found {len(matches)} activities — use activity_id for dates")
    field = (cmd.get("field") or "").strip().lower()
    if field not in ("start", "finish"):
        raise EditError("field must be 'start' or 'finish'")
    date = (cmd.get("date") or "").strip()
    if not date:
        raise EditError("date is required")
    try:
        new_d = _d.date.fromisoformat(date[:10])
    except ValueError:
        raise EditError(f"Not a valid date: {date}")

    a = matches[0]
    is_milestone = a.activity_type in ("Start Milestone", "Finish Milestone")
    carry = cmd.get("move_constraint", True)

    if field == "start":
        if a.actual_start:
            raise EditError(f"'{a.name}' has started — its start is the actual date")
        old_start = a.planned_start
        a.planned_start = date[:10]
        # The finish travels with the start, keeping the duration. Without this
        # the row shows a start and a finish that no longer agree with its own
        # duration — the schedule no longer reflows on every edit, so nothing
        # else was going to fix it up.
        wd, hol, hpd = _act_calendar(project, a)
        dur_d = 0.0 if is_milestone else (a.planned_duration or 0.0) / hpd
        a.planned_finish = (date[:10] if dur_d <= 0 else
                            _add_working_days(new_d, _span_days(dur_d), wd, hol).isoformat())
        note = _carry_constraint(a, "start", new_d, old_start) if carry else ""
        return True, (f"'{a.name}' {date[:10]} → {a.planned_finish} "
                      f"({dur_d:g}d){note}")

    if a.actual_finish:
        raise EditError(f"'{a.name}' is complete — its finish is the actual date")
    if is_milestone:
        old_start = a.planned_start
        a.planned_start = a.planned_finish = date[:10]
        # a milestone is a single instant, so either kind of pin belongs on it
        note = _carry_constraint(a, "finish", new_d, old_start, both=True) if carry else ""
        return True, f"'{a.name}' milestone date → {date[:10]}{note}"

    ref = a.actual_start or a.planned_start or a.early_start
    if not ref:
        old_finish = a.planned_finish
        a.planned_finish = date[:10]
        note = _carry_constraint(a, "finish", new_d, old_finish) if carry else ""
        return True, f"'{a.name}' planned finish → {date[:10]}{note}"
    try:
        ref_d = _d.date.fromisoformat(str(ref)[:10])
    except ValueError:
        old_finish = a.planned_finish
        a.planned_finish = date[:10]
        note = _carry_constraint(a, "finish", new_d, old_finish) if carry else ""
        return True, f"'{a.name}' planned finish → {date[:10]}{note}"
    if new_d < ref_d:
        raise EditError(f"Finish {date[:10]} is before the start ({str(ref)[:10]})")

    # Working days from start to finish INCLUSIVE, matching P6 and the
    # scheduler's finish = start + duration - 1 convention: a task that runs
    # Monday to Friday is five days, not four.
    wd, hol, hpd = _act_calendar(project, a)
    days, d = 0, ref_d
    while d < new_d:
        d += _d.timedelta(days=1)
        if d.weekday() in wd and ((not hol) or d.isoformat() not in hol):
            days += 1
    days += 1
    a.planned_duration = float(days) * hpd
    a.remaining_duration = a.planned_duration * (1 - (a.percent_complete or 0) / 100.0)
    old_finish = a.planned_finish
    a.planned_finish = date[:10]
    note = _carry_constraint(a, "finish", new_d, old_finish) if carry else ""
    return True, f"'{a.name}' finish → {date[:10]} (duration now {days}d){note}"


_STATUS_ALIASES = {
    "not started": "Not Started", "notstarted": "Not Started", "ns": "Not Started",
    "new": "Not Started", "reopen": "Not Started", "unstart": "Not Started",
    "in progress": "In Progress", "inprogress": "In Progress", "started": "In Progress",
    "start": "In Progress", "wip": "In Progress", "active": "In Progress",
    "completed": "Completed", "complete": "Completed", "done": "Completed",
    "finished": "Completed", "finish": "Completed",
}


def _set_progress(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Status an activity the way P6 does — the weekly update, in one command.

    P6 defines status by which actual dates exist, so setting one without the
    other leaves a row that contradicts itself. The three states and what each
    one means:

      Not Started  no actuals at all, 0% and the full duration remaining
      In Progress  an actual START and no actual finish; the finish is still a
                   forecast, which is the state a running activity is in all
                   week and the one that was hardest to reach before
      Completed    both actuals, 100%, nothing remaining

    Dates default to what the row already forecasts, so "mark this started" is
    one click and "started on Tuesday" is the same command with a date:

      status           not started | in progress | completed
      actual_start     defaults to the planned start (or the data date if the
                       planned start is in the future — you cannot have started
                       work that has not been scheduled yet)
      actual_finish    completed only; defaults to the planned finish
      percent_complete optional; in progress defaults to 50 if not already set

    Moving one date on its own stays with set_actual_date.
    """
    import datetime as _d

    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    if len(matches) > 1 and not cmd.get("apply_to_all"):
        raise EditError(f"Found {len(matches)} activities. Use activity_id, "
                        f"or set apply_to_all=true.")

    raw = str(cmd.get("status", "")).strip().lower()
    status = _STATUS_ALIASES.get(raw)
    if not status:
        raise EditError("status must be 'not started', 'in progress' or 'completed' "
                        f"— got {cmd.get('status')!r}")

    def _iso(v):
        if not v:
            return None
        try:
            return _d.date.fromisoformat(str(v)[:10]).isoformat()
        except ValueError:
            raise EditError(f"Not a valid date: {v}")

    a_start = _iso(cmd.get("actual_start"))
    a_finish = _iso(cmd.get("actual_finish"))
    today = _iso(project.data_date) or _d.date.today().isoformat()

    done = []
    for a in matches:
        if status == "Not Started":
            a.actual_start = a.actual_finish = None
            a.percent_complete = 0.0
            a.remaining_duration = a.planned_duration
            a.status = "Not Started"
        elif status == "In Progress":
            # A start that has not happened yet is not an actual. Fall back to
            # the data date so "mark started" on future work still means today.
            start = a_start or a.actual_start or str(a.planned_start or "")[:10] or today
            a.actual_start = min(start, today) if start > today else start
            a.actual_finish = None
            pct = cmd.get("percent_complete")
            if pct is None:
                pct = a.percent_complete if 0 < (a.percent_complete or 0) < 100 else 50.0
            a.percent_complete = max(0.0, min(99.0, float(pct)))
            a.remaining_duration = a.planned_duration * (1 - a.percent_complete / 100.0)
            a.status = "In Progress"
        else:
            a.actual_start = (a_start or a.actual_start
                              or str(a.planned_start or "")[:10] or today)
            a.actual_finish = (a_finish or a.actual_finish
                               or str(a.planned_finish or "")[:10] or a.actual_start)
            if a.actual_finish < a.actual_start:
                raise EditError(f"'{a.name}': actual finish {a.actual_finish} is before "
                                f"the actual start {a.actual_start}")
            a.percent_complete = 100.0
            a.remaining_duration = 0.0
            a.status = "Completed"
        done.append(a.activity_id)

    where = (f"{done[0]}" if len(done) == 1 else f"{len(done)} activities")
    detail = ""
    if status == "In Progress":
        detail = f" (actual start {matches[0].actual_start})"
    elif status == "Completed":
        detail = f" ({matches[0].actual_start} → {matches[0].actual_finish})"
    return True, f"{where} → {status}{detail}"


def _set_actual_date(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Move an actual start/finish date. Constraints can't do this: the CPM pass
    anchors In Progress / Completed activities to their actual dates before it
    ever looks at constraints, so editing a started activity's date has to
    change the actual itself — this is what the grid's date cells use for any
    row whose displayed date is an actual.
    An empty date clears the actual and hands the field back to the scheduler
    (rolling status back if nothing actual remains).
    """
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    if len(matches) > 1:
        raise EditError(f"Found {len(matches)} activities — use activity_id for actual dates")
    field = (cmd.get("field") or "").strip().lower()
    if field not in ("start", "finish"):
        raise EditError("field must be 'start' or 'finish'")
    date = (cmd.get("date") or "").strip() or None
    if date:
        # Unlike the planned-date edit, this never checked. An unparseable
        # string went straight onto the activity as its actual date, where it
        # then failed to compare or sort against every real date in the job —
        # a silent corruption, and of the one field the CPM anchors started
        # work to. Normalised to a plain ISO day as well, so "2026-03-02
        # 07:00" cannot sit next to "2026-03-02" and count as different.
        import datetime as _d
        try:
            date = _d.date.fromisoformat(date[:10]).isoformat()
        except ValueError:
            raise EditError(f"Not a valid date: {cmd.get('date')!r}")
    a = matches[0]
    if field == "start":
        a.actual_start = date
        if date:
            if a.status == "Not Started":
                a.status = "In Progress"
                if not a.percent_complete:
                    a.percent_complete = 1.0
        elif not a.actual_finish:
            a.status = "Not Started"
            a.percent_complete = 0.0
            a.remaining_duration = a.planned_duration
    else:
        a.actual_finish = date
        if date:
            if not a.actual_start:
                a.actual_start = str(a.planned_start or date)[:10]
            a.status = "Completed"
            a.percent_complete = 100.0
            a.remaining_duration = 0.0
        elif a.status == "Completed":
            a.status = "In Progress" if a.actual_start else "Not Started"
            if a.percent_complete >= 100.0:
                a.percent_complete = 50.0 if a.actual_start else 0.0
            a.remaining_duration = a.planned_duration * (1 - a.percent_complete / 100.0)
    verb = f"actual {field} → {date}" if date else f"cleared actual {field}"
    return True, f"'{a.name}': {verb}"


# The crew-size field. P6 teams name it slightly differently from job to job,
# so the grid column binds to whichever UDF on the project matches — rather
# than forcing one exact spelling and silently editing nothing.
ELECTRICIANS_PATTERNS = ("electric", "crew size", "manpower", "headcount")


def electricians_field(project: Project) -> str:
    """
    Title of the UDF the Electricians column edits.

    Prefers a field the project already carries, so an edit writes back into
    the same column P6 exported; falls back to creating the standard name.
    """
    titles = [u.title for u in (getattr(project, "udf_types", None) or []) if u.title]
    for a in project.activities:
        for t in (getattr(a, "udfs", None) or {}):
            if t not in titles:
                titles.append(t)
    for t in titles:
        low = (t or "").lower()
        if any(pat in low for pat in ELECTRICIANS_PATTERNS):
            return t
    return "Number of Electricians"


def _norm_name(s: str) -> str:
    """An activity name reduced to what identifies the WORK, not the location.

    "Install High Steel Area 3" and "Install High Steel Area 7" are the same
    task in two places and take the same crew, so the trailing area / level /
    room / phase and any bare numbers come off before matching.
    """
    t = (s or "").strip().lower()
    t = re.sub(r"\((?:[^()]*)\)", " ", t)                      # (97 Pieces)
    t = re.sub(r"\b(?:area|zone|level|lvl|room|rm|phase|ph|grid(?:\s*line)?|"
               r"bldg|building|unit|line)\s*[\w.-]{0,8}\b", " ", t)
    t = re.sub(r"[-–—:,/]+", " ", t)
    t = re.sub(r"\b\d+(?:\.\d+)?\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def crew_defaults(project: Project, field: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    The crew count already used for each kind of work, learned from the rows
    that carry one.

    Keyed by the normalised name, so "Install High Steel Area 3" teaches the
    default for every other area too. Where a name has been given different
    counts, the most common wins and `varies` says the schedule disagrees with
    itself — worth seeing rather than silently averaging.
    """
    key = field or electricians_field(project)
    seen: Dict[str, Dict[str, Any]] = {}
    for a in project.activities:
        v = (getattr(a, "udfs", None) or {}).get(key)
        if v in (None, ""):
            continue
        try:
            num = float(str(v).strip())
        except (TypeError, ValueError):
            continue
        norm = _norm_name(a.name)
        if not norm:
            continue
        e = seen.setdefault(norm, {"name": a.name, "counts": {}, "with_value": 0})
        e["counts"][num] = e["counts"].get(num, 0) + 1
        e["with_value"] += 1

    blanks: Dict[str, int] = {}
    for a in project.activities:
        if (getattr(a, "udfs", None) or {}).get(key) in (None, ""):
            n = _norm_name(a.name)
            if n in seen:
                blanks[n] = blanks.get(n, 0) + 1

    out = []
    for norm, e in seen.items():
        best = max(e["counts"].items(), key=lambda kv: (kv[1], -kv[0]))
        out.append({
            "name": e["name"],
            "match": norm,
            "crew": int(best[0]) if float(best[0]).is_integer() else best[0],
            "with_value": e["with_value"],
            "missing": blanks.get(norm, 0),
            "varies": len(e["counts"]) > 1,
        })
    out.sort(key=lambda r: (-r["missing"], -r["with_value"]))
    return out


def _apply_crew_to_name(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Put a crew count on every activity doing the same kind of work.

    Set it once on "Install High Steel Area 3" and this carries it to every
    other Install High Steel, past and future — which is the only realistic way
    to fill a column across a few thousand rows.

      activity_id / name : the row (or name) to take the work from
      value              : the count; omitted, it is read off activity_id
      match              : "work" (default) ignores area/level/number, so
                           Area 3 teaches Area 7
                           "exact" only identical names
                           "contains" a plain substring
      only_missing       : default true — never overwrite a count somebody set
      wbs_uid            : restrict to one branch
    """
    key = cmd.get("field") or electricians_field(project)
    mode = (cmd.get("match") or "work").strip().lower()

    src = None
    if cmd.get("activity_id") or cmd.get("target_name"):
        hits = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
        if not hits:
            raise EditError(_no_activity(project, cmd.get("activity_id")
                                         or cmd.get("target_name")))
        src = hits[0]

    raw = cmd.get("value")
    if raw in (None, "") and src is not None:
        raw = (getattr(src, "udfs", None) or {}).get(key)
    if raw in (None, ""):
        raise EditError(f"No {key} value to apply — set one on the activity first, "
                        f"or pass value")
    try:
        value = str(int(float(str(raw).strip())))
    except (TypeError, ValueError):
        raise EditError(f"{key} needs a number — got {raw!r}")

    needle_raw = cmd.get("name") or (src.name if src else "")
    if not needle_raw:
        raise EditError("Nothing to match on — pass activity_id or name")
    needle = (_norm_name(needle_raw) if mode == "work"
              else needle_raw.strip().lower())
    if not needle:
        raise EditError(f"'{needle_raw}' has nothing left to match on once the "
                        f"area and numbers are removed — try match='exact'")

    scope = None
    if cmd.get("wbs_uid") or cmd.get("wbs_name") or cmd.get("wbs_code"):
        w = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"), cmd.get("wbs_uid"))
        if not w:
            raise EditError("Scope " + _no_wbs(project, cmd.get("wbs_name")
                                               or cmd.get("wbs_uid")))
        scope = {n.uid for n in project.wbs_nodes}
        keep, stack = {w.uid}, [w.uid]
        kids = {}
        for n in project.wbs_nodes:
            kids.setdefault(n.parent_uid, []).append(n.uid)
        while stack:
            for k in kids.get(stack.pop(), []):
                if k not in keep:
                    keep.add(k)
                    stack.append(k)
        scope = keep

    only_missing = cmd.get("only_missing", True)
    changed, skipped = 0, 0
    for a in project.activities:
        if scope is not None and a.wbs_uid not in scope:
            continue
        hay = _norm_name(a.name) if mode == "work" else (a.name or "").strip().lower()
        hit = (needle in hay) if mode == "contains" else (hay == needle)
        if not hit:
            continue
        cur = (getattr(a, "udfs", None) or {}).get(key)
        if cur not in (None, "") and only_missing:
            skipped += 1
            continue
        if str(cur) == value:
            continue
        _update_udf(project, {"activity_id": a.activity_id, "field": key, "value": value})
        changed += 1

    if not changed and not skipped:
        raise EditError(f"Nothing matches '{needle_raw}' — no activity does that work")
    msg = f"{key} = {value} on {changed} activit{'y' if changed == 1 else 'ies'} " \
          f"doing '{needle_raw.strip()}'"
    if skipped:
        msg += f" ({skipped} already had a number, left alone)"
    return True, msg


def _fill_crew_defaults(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Fill every blank crew cell from what the schedule already says elsewhere.

    Only blanks, and only from a name that has been given a count somewhere —
    nothing is invented, and nothing already filled in is touched.
    """
    key = cmd.get("field") or electricians_field(project)
    table = {d["match"]: d["crew"] for d in crew_defaults(project, key)}
    if not table:
        raise EditError(f"No {key} values to learn from yet — set a few first")
    changed = 0
    for a in project.activities:
        if (getattr(a, "udfs", None) or {}).get(key) not in (None, ""):
            continue
        v = table.get(_norm_name(a.name))
        if v is None:
            continue
        _update_udf(project, {"activity_id": a.activity_id, "field": key,
                              "value": str(v)})
        changed += 1
    blank = sum(1 for a in project.activities
                if (getattr(a, "udfs", None) or {}).get(key) in (None, ""))
    return True, (f"Filled {key} on {changed} activit{'y' if changed == 1 else 'ies'} "
                  f"from {len(table)} learned default(s); {blank} still blank")


# ── Find-and-change rules ────────────────────────────────────────────────────

_RULE_FIELDS = ("name", "activity_id", "wbs_name", "constraint_type", "type", "status")


def _rule_haystack(project: Project, act: Activity, field: str) -> str:
    if field == "wbs_name":
        w = project.get_wbs(act.wbs_uid)
        return (w.name if w else "") or ""
    if field == "activity_id":
        return act.activity_id or ""
    if field == "type":
        return act.activity_type or ""
    if field == "status":
        return act.status or ""
    if field == "constraint_type":
        return act.constraint_type or ""
    return act.name or ""


def _rule_matches(text: str, op: str, value: str) -> bool:
    t, v = (text or ""), (value or "")
    tl, vl = t.lower(), v.lower()
    if op == "equals":       return tl == vl
    if op == "not_contains": return vl not in tl
    if op == "starts_with":  return tl.startswith(vl)
    if op == "ends_with":    return tl.endswith(vl)
    if op == "regex":
        try:
            return re.search(v, t, re.IGNORECASE) is not None
        except re.error as e:
            raise EditError(f"Bad pattern '{v}': {e}")
    return vl in tl          # "contains" — the default


def _apply_one_set(project: Project, a: Activity, action: Dict,
                   preview: bool) -> Optional[Tuple[Any, Any]]:
    """
    Carry out ONE "then set" against one activity.

    Returns (before, after) when something actually changes, or None when this
    activity already holds the wanted value — the caller uses that to avoid
    counting an activity that no set on the rule moved. Writes nothing when
    preview is set, so the dry run and the real change run the same code.
    """
    target = (action.get("field") or "").strip().lower()
    new_value = action.get("value")
    position = (action.get("position") or "suffix").strip().lower()

    if target in ("name", "activity_name"):
        before = a.name
        text = "" if new_value is None else str(new_value)
        if action.get("mode") == "append":
            if text and text in (a.name or ""):
                return None                    # already carries it — re-running is safe
            after = f"{a.name} {text}".strip() if position != "prefix" else f"{text} {a.name}".strip()
        else:
            after = text
        if after == before:
            return None
        if not preview:
            a.name = after
        return before, after

    if target in ("duration", "duration_days"):
        try:
            days = float(new_value)
        except (TypeError, ValueError):
            raise EditError("duration needs a number of days")
        if days < 0:
            raise EditError("duration cannot be negative")
        before, after = round((a.planned_duration or 0) / 8.0, 2), days
        if before == after:
            return None
        if not preview:
            a.planned_duration = days * 8.0
            if a.status == "Not Started":
                a.remaining_duration = a.planned_duration
        return before, after

    if target in ("electricians", "udf"):
        key = action.get("udf_field") or electricians_field(project)
        before = (a.udfs or {}).get(key, "")
        after = "" if new_value is None else str(new_value).strip()
        if before == after:
            return None
        if not preview:
            _update_udf(project, {"activity_id": a.activity_id,
                                  "field": key, "value": after})
        return before, after

    if target in ("wbs_name", "folder"):
        w = project.get_wbs(a.wbs_uid)
        if not w:
            return None
        before, after = w.name, str(new_value or "").strip()
        if not after or before == after:
            return None
        if not preview:
            w.name = after
        return before, after

    if target == "constraint_type":
        before, after = a.constraint_type or "", str(new_value or "").strip()
        if before == after:
            return None
        if not preview:
            a.constraint_type = after or None
            if not after:
                a.constraint_date = None
        return before, after

    raise EditError(f"Cannot set '{target}'. Use name, duration, "
                    f"electricians, wbs_name or constraint_type.")


def _rule_sets(rule: Dict) -> List[Dict]:
    """
    The "then set" list for one rule, however it was written.

    One IF can drive several SETs — "if the name contains MV 105, set the
    duration AND the electricians AND tag the name" is one decision, not
    three rules that each have to re-state the same condition. A single
    `set` object still works unchanged; `sets` (or a list in `set`) carries
    more than one.
    """
    raw = rule.get("sets")
    if raw is None:
        raw = rule.get("set")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise EditError("Each rule needs at least one 'set'")
    actions = [s for s in raw if isinstance(s, dict) and (s.get("field") or "").strip()]
    if not actions:
        raise EditError("Each rule needs at least one 'set' with a field")

    # Two sets aimed at the same field would make the preview lie: preview
    # writes nothing, so the second one would be measured against the
    # original value while the real run measures it against the first one's
    # result. Refusing is better than showing a dry run that differs from
    # what applying actually does.
    seen = set()
    for s in actions:
        f = (s.get("field") or "").strip().lower()
        if f in seen:
            raise EditError(f"This rule sets '{f}' twice — give each field one "
                            f"value, or split it into two rules.")
        seen.add(f)
    return actions


def _apply_rule(project: Project, rule: Dict, scope: List[Activity],
                preview: bool) -> Tuple[int, List[str]]:
    """
    Apply one if/then rule. Returns (changed_count, sample descriptions).

    The condition is tested once per activity and every "then set" on the
    rule runs against the ones that match. An activity counts once no matter
    how many of its fields the rule moved — the number is "how many
    activities this rule changed", not how many writes it made.
    """
    where = rule.get("where") or {}
    field = (where.get("field") or "name").strip().lower()
    if field not in _RULE_FIELDS:
        raise EditError(f"Cannot match on '{field}'. Use one of: {', '.join(_RULE_FIELDS)}")
    op = (where.get("op") or "contains").strip().lower()
    needle = where.get("value")
    if needle is None:
        raise EditError("where.value is required")

    actions = _rule_sets(rule)

    changed, samples = 0, []
    for a in scope:
        if not _rule_matches(_rule_haystack(project, a, field), op, str(needle)):
            continue
        moved = []
        for action in actions:
            got = _apply_one_set(project, a, action, preview)
            if got is not None:
                moved.append(((action.get("field") or "").strip().lower(), got))
        if not moved:
            continue
        changed += 1
        if len(samples) < 8:
            if len(moved) == 1:
                before, after = moved[0][1]
                samples.append(f"{a.activity_id}: {before} → {after}")
            else:
                detail = ", ".join(f"{f} {b} → {af}" for f, (b, af) in moved)
                samples.append(f"{a.activity_id}: {detail}")
    return changed, samples


def _bulk_rules(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Global find-and-change: "if the name contains X, set the duration to Y".

    Runs over the whole schedule, or only inside a folder when wbs_name/
    wbs_uid is given. preview=true reports what WOULD change without touching
    anything, because a mistyped pattern across 2700 activities is expensive
    to undo by hand even with an undo stack.
    """
    rules = cmd.get("rules")
    if isinstance(cmd.get("where"), dict):        # a single rule, unwrapped
        rules = [cmd]
    if not rules or not isinstance(rules, list):
        raise EditError("rules (a non-empty list) is required")

    scope_name = cmd.get("wbs_name") or cmd.get("wbs_code") or cmd.get("wbs_uid")
    if scope_name:
        w = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"), cmd.get("wbs_uid"))
        if not w:
            raise EditError(f"WBS not found: {scope_name}")
        branch = {w.uid}
        grew = True
        while grew:
            grew = False
            for n in project.wbs_nodes:
                if n.parent_uid in branch and n.uid not in branch:
                    branch.add(n.uid); grew = True
        scope = [a for a in project.activities if a.wbs_uid in branch]
        where = f" in '{w.name}'"
    else:
        scope, where = list(project.activities), ""

    preview = bool(cmd.get("preview"))
    total, lines = 0, []
    for i, rule in enumerate(rules, 1):
        n, samples = _apply_rule(project, rule, scope, preview)
        total += n
        head = f"Rule {i}: {n} activity/activities" if len(rules) > 1 else f"{n} activity/activities"
        lines.append(head + (" would change" if preview else " changed"))
        lines.extend("    " + s for s in samples)
    if not preview:
        project.build_lookups()
    verb = "Would change" if preview else "Changed"
    return True, f"{verb} {total} across {len(scope)} activities{where}.\n" + "\n".join(lines)


def _update_udf(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Set a user-defined field on an activity — the Electricians column and any
    other UDF the schedule carries.
    """
    from engine.schedule_model import UDFType

    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    field = (cmd.get("field") or "").strip() or electricians_field(project)
    raw = cmd.get("value")
    value = "" if raw is None else str(raw).strip()

    known = {u.title for u in (getattr(project, "udf_types", None) or [])}
    if value and field not in known:
        # A field edited into existence here must also be DEFINED, or the
        # export would carry values referencing a column P6 never heard of.
        # Always Text, never guessed from the value's shape. P6 itself may
        # already have this exact field name configured at the enterprise
        # level with a type this app has no way to see — a schedule that
        # never carried it before typing "6" into a cell here is not proof
        # P6 agrees it's numeric. Guessing "Integer" from digits produced
        # exactly that: P6 rejected the whole field as an "invalid UDF data
        # type" because its own definition of the field was Text. Text is
        # never wrong the other way — it holds a number as a string with no
        # validation on P6's end to conflict with.
        project.udf_types.append(UDFType(
            uid=str(900 + len(project.udf_types)), title=field,
            subject_area="Activity", data_type="Text"))

    for a in matches:
        if value == "":
            a.udfs.pop(field, None)
        else:
            a.udfs[field] = value
    what = f"{field} → {value}" if value else f"cleared {field}"
    return True, f"{what} on {len(matches)} activity/activities"


def _requirements(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Contract dates and structural promises, checked against the network.

    A requirement is the checkable half of what the user says in one line —
    "PH1 substantial completion is 15 March 27", "every generator termination
    leads to commissioning". Kept on the brain so they survive a re-export
    and are re-verified after every change, rather than being a sentence the
    agent might remember.

      add     : store one and check it
      check   : verify all stored (or the ones passed in)
      enforce : propose the ties/pins that would satisfy the failures;
                applies only with apply=true
      list / remove
    """
    from engine import requirements as _rq

    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    if brain is None:
        raise EditError("No project brain available for requirements")
    store = getattr(brain, "requirements", None)
    if store is None:
        store = brain.requirements = []

    op = (cmd.get("op") or cmd.get("mode") or "check").strip().lower()
    spec = cmd.get("requirement") or cmd.get("spec")
    specs = cmd.get("requirements") or ([spec] if spec else None)

    if op == "list":
        if not store:
            return True, "No requirements stored for this job yet."
        lines = [f"{len(store)} requirement(s) on this job:"]
        for i, s in enumerate(store, 1):
            lines.append(f"  {i}. {s.get('label') or s.get('kind')}")
        return True, "\n".join(lines)

    if op == "remove":
        label = (cmd.get("label") or "").strip().lower()
        if not label:
            raise EditError("remove needs the requirement's label")
        keep = [s for s in store
                if label not in str(s.get("label") or "").lower()]
        n = len(store) - len(keep)
        brain.requirements = keep
        return True, (f"Removed {n} requirement(s)." if n
                      else f"Nothing matched '{label}'.")

    if op == "add":
        if not specs:
            raise EditError("add needs a requirement")
        added = 0
        for s in specs:
            if not isinstance(s, dict) or not s.get("kind"):
                raise EditError("each requirement needs a 'kind' "
                                "(deadline, reaches or follows)")
            labels = {str(x.get("label") or "").lower() for x in store}
            if str(s.get("label") or "").lower() not in labels:
                store.append(s)
                added += 1
        return True, (f"Stored {added} requirement(s); now checking all "
                      f"{len(store)}.\n" + _rq.report(project, store))

    to_check = specs or store
    if not to_check:
        return True, ("No requirements stored yet. Add one with "
                      "op='add' — e.g. a phase contract date, or that "
                      "burn-ins must lead to commissioning.")

    if op == "enforce":
        all_cmds, checks = [], []
        for s in to_check:
            r = _rq.enforce(project, s)
            checks.append((s, r["check"]))
            all_cmds.extend(r["commands"])
        if not all_cmds:
            return True, ("Nothing to enforce — every requirement already "
                          "holds, or the ones that fail name activities that "
                          "do not exist.\n" + _rq.report(project, to_check))
        if not cmd.get("apply"):
            lines = [f"{len(all_cmds)} change(s) would satisfy the failing "
                     f"requirements. Nothing applied."]
            for c in all_cmds[:20]:
                if c["action"] == "add_relation":
                    lines.append(f"  tie {c['predecessor_id']} → {c['successor_id']}")
                else:
                    lines.append(f"  pin {c['activity_id']} "
                                 f"{c['constraint_type']} {c['constraint_date']}")
            if len(all_cmds) > 20:
                lines.append(f"  …and {len(all_cmds) - 20} more")
            lines.append("Pass apply=true to make these changes.")
            return True, "\n".join(lines)
        results = apply_commands(project, all_cmds)
        ok = sum(1 for good, _ in results if good)
        return True, (f"Applied {ok} of {len(all_cmds)} change(s). "
                      f"Nothing was deleted; undo reverts the batch.\n"
                      + _rq.report(project, to_check))

    return True, _rq.report(project, to_check)


def _ripple(project: Project, cmd: Dict, apply_it: bool) -> Tuple[bool, str]:
    """
    Reschedule ONE activity's path and leave the rest of the job alone.

    The middle speed between "type a date and nothing moves" and "press
    Schedule and everything moves". Only activities downstream of this one are
    written back; anything that would have moved elsewhere under a full reflow
    is deliberately left as it was.
    """
    from engine import ripple as _rp

    aid = cmd.get("activity_id") or cmd.get("target_id")
    if not aid:
        matches = _find_activity(project, None, cmd.get("target_name"))
        if not matches:
            raise EditError("ripple needs an activity_id")
        if len(matches) > 1:
            raise EditError(f"Found {len(matches)} activities — use activity_id")
        aid = matches[0].activity_id

    changes = {k: cmd[k] for k in
               ("actual_start", "actual_finish", "planned_start",
                "planned_finish", "duration_days", "status")
               if cmd.get(k) is not None}
    back = bool(cmd.get("include_predecessors"))
    # "Where does this path land if we are standing on 1 March" — a projection
    # date for the trial. It never moves the project's own data date; that is
    # a whole-job property and the Schedule button owns it.
    as_of = cmd.get("data_date") or cmd.get("as_of")

    if not apply_it:
        return True, _rp.report(project, aid, changes, back, data_date=as_of)

    r = _rp.apply_ripple(project, aid, changes, back, data_date=as_of)
    if r.get("error"):
        raise EditError(r["error"])
    msg = [f"Rippled from {r['activity_id']} — {r['moved_on_path']} activity"
           f"{'' if r['moved_on_path'] == 1 else 'ies'} on its path moved "
           f"({r['written']} rows written)."]
    if r.get("as_of_override"):
        msg.append(f"  Projected as of {r['as_of']}. The project data date "
                   f"({r['project_data_date'] or 'not set'}) was left alone.")
    if r["would_move_off_path"]:
        msg.append(f"  {r['would_move_off_path']} elsewhere were left alone — "
                   f"they would only have moved because a full Schedule run "
                   f"moves everything.")
    msg.append("  Undo reverts the whole ripple.")
    return True, "\n".join(msg)


def _procurement_map(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Every major system: when it lands, when it is first needed, and whether
    anything in the network holds the two together. Read-only.
    """
    from engine import procurement_map as _pm
    return True, _pm.report(project, phase=cmd.get("phase"),
                            max_rows=int(cmd.get("max_rows") or 40))


def _procurement_story(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    One system, told as a sentence — the "the chillers have to be here before
    this chiller work" answer, with the activities named. Read-only.
    """
    from engine import procurement_map as _pm

    system = (cmd.get("system") or cmd.get("equipment")
              or cmd.get("target_name") or "").strip()
    if not system:
        raise EditError("procurement_story needs a system, e.g. "
                        '{"action":"procurement_story","system":"chiller"}')
    return True, _pm.story(project, system, cmd.get("phase"))


def _procurement_cover(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Close the "dates work, nothing holding them" rows — a tie for every
    activity using the equipment that is not already behind its delivery.

    Reports by default. Work dated before its own delivery is never tied.
    """
    from engine import procurement_map as _pm

    phase = cmd.get("phase")
    system = cmd.get("system") or cmd.get("equipment")
    if not cmd.get("apply"):
        return True, _pm.cover_report(project, phase, system)

    r = _pm.cover_gaps(project, phase, system)
    if not r["commands"]:
        return True, ("Nothing to tie — every system whose dates work is "
                      "already held there by logic."
                      + (f" {len(r['blocked'])} are dated before their own "
                         f"delivery and are left alone."
                         if r["blocked"] else ""))

    made = sum(1 for ok, _ in apply_commands(project, r["commands"]) if ok)
    msg = [f"Tied {made} delivery→work relationship(s) across "
           f"{r['systems_touched']} system(s)."]
    if r["blocked"]:
        msg.append(f"  {len(r['blocked'])} NOT tied — dated before the "
                   f"equipment arrives; that conflict is a decision about the "
                   f"job, so it stays visible.")
    msg.append("  Undo reverts the whole pass.")
    return True, "\n".join(msg)


def _wire_procurement(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Tie each long-lead delivery to the work it feeds.

    Reports by default. An install dated BEFORE its own delivery is never
    tied — forcing that would push the work out and hide a conflict only the
    user can resolve.
    """
    from engine import procurement_wire as _pw

    needle = cmd.get("wbs") or cmd.get("scope") or None
    if not cmd.get("apply"):
        return True, _pw.procurement_report_text(project, needle)

    r = _pw.wire_procurement(project, needle)
    if not r["commands"]:
        return True, ("Nothing to tie.\n"
                      + _pw.procurement_report_text(project, needle))
    results = apply_commands(project, r["commands"])
    ok = sum(1 for good, _ in results if good)
    msg = [f"Tied {ok} delivery→install relationship(s)."]
    if r["blocked"]:
        msg.append(f"  {len(r['blocked'])} left alone — the install is dated "
                   f"before its own delivery, which is a decision for you, not "
                   f"a tie. Run the report to see them.")
    msg.append("  Nothing was deleted; undo reverts the batch.")
    return True, "\n".join(msg)


def _replicate_pattern(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Put the sequence of one folder onto others of the same kind.

    Only where both ends of a tie exist in the target and nothing already
    connects them, so a partly-wired area keeps its own logic.
    """
    from engine import procurement_wire as _pw

    src = cmd.get("source") or cmd.get("from_wbs") or cmd.get("template_wbs")
    if not src:
        raise EditError("replicate_pattern needs a source folder")
    targets = cmd.get("targets") or cmd.get("to_wbs") or cmd.get("target_wbs")
    if isinstance(targets, str):
        targets = [targets]
    if not targets:
        raise EditError("replicate_pattern needs targets")

    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    if not cmd.get("apply"):
        return True, _pw.replicate_report(project, src, targets, brain)

    r = _pw.replicate_pattern(project, src, targets, brain)
    if r.get("error"):
        raise EditError(r["error"])
    if not r["commands"]:
        return True, ("Nothing to add.\n"
                      + _pw.replicate_report(project, src, targets, brain))
    results = apply_commands(project, r["commands"])
    ok = sum(1 for good, _ in results if good)
    per = "; ".join(f"{f['folder']} +{f['ties']}" for f in r["per_folder"][:8])
    return True, (f"Replicated '{r['source']}' — {ok} tie(s) added. {per}. "
                  f"Existing logic untouched; undo reverts the batch.")


def _bridge_folder(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Attach one folder to the rest of the job, with the reasoning shown.

    Reports by default. The choice of WHICH activity to bridge on is the part
    worth arguing with, so the candidates and their scores come back rather
    than just a tie.
    """
    from engine import bridge as _br

    ref = (cmd.get("wbs") or cmd.get("folder") or cmd.get("wbs_name")
           or cmd.get("wbs_uid"))
    if not ref:
        raise EditError("bridge_folder needs a folder (wbs)")
    node = _find_wbs(project, ref, ref, ref)
    if not node:
        raise EditError(_no_wbs(project, ref))

    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    if not cmd.get("apply"):
        return True, _br.report(project, node.uid, brain)

    r = _br.propose(project, node.uid, brain)
    if r.get("error"):
        raise EditError(r["error"])
    if not r["commands"]:
        return True, (f"Nothing confident enough to bridge '{r['folder']}'. "
                      + _br.report(project, node.uid, brain))
    results = apply_commands(project, r["commands"])
    ok = sum(1 for good, _ in results if good)
    return True, (f"Bridged '{r['folder']}': {ok} tie(s) added. "
                  f"Nothing was deleted; undo reverts it.")


def _fix_backward(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Reverse only the folder ties that are genuinely upside down.

    Ties whose dates simply drifted are left alone — those need a reflow, and
    reversing them would break correct logic.
    """
    from engine import bridge as _br

    cmds = _br.fix_backward(project)
    if not cmds:
        return True, ("No backward tie is safely reversible.\n"
                      + _br.backward_report(project))
    n = len(cmds) // 2
    if not cmd.get("apply"):
        return True, (f"{n} tie(s) are genuinely reversed and can be flipped. "
                      f"Nothing applied — pass apply=true.\n"
                      + _br.backward_report(project))
    results = apply_commands(project, cmds)
    ok = sum(1 for good, _ in results if good)
    return True, (f"Reversed {n} backward tie(s) ({ok} commands). "
                  f"Ties whose dates had merely drifted were left alone — "
                  f"run Schedule for those. Undo reverts the batch.")


def _normalize_logic(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Close the open ends the ranker is confident about — the bulk repair pass.

    Reports by default. It only APPLIES when explicitly told to, because a
    batch of a couple of hundred relationships is not something to emit on a
    maybe. Every rail is in the engine, not in the caller's good intentions:
    only ends already open, nothing ever deleted, folders holding duplicated
    rows skipped rather than wired, ties contradicting the dates dropped, a
    confidence floor, and a cap so the result stays reviewable.
    """
    from engine import normalize as _nz

    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    try:
        conf = float(cmd.get("min_confidence", 0.55))
    except (TypeError, ValueError):
        conf = 0.55
    try:
        limit = max(1, min(400, int(cmd.get("limit", 150))))
    except (TypeError, ValueError):
        limit = 150
    folders = cmd.get("folders") or cmd.get("wbs") or None
    if isinstance(folders, str):
        folders = [folders]

    if not cmd.get("apply"):
        return True, _nz.normalize_report(project, brain, conf, limit)

    r = _nz.normalize_logic(project, brain, conf, limit, folders)
    cmds = r["commands"]
    if not cmds:
        return True, ("Nothing met the confidence bar — no ties applied. "
                      + (f"{len(r['skipped_for_duplicates'])} folder(s) were "
                         f"skipped for holding duplicated rows. "
                         if r["skipped_for_duplicates"] else "")
                      + f"{r['unresolved']} open row(s) need a human.")

    v = _nz.verify(project, cmds, brain)
    if v["folders_improved"] == 0 and v["floating_removed"] == 0:
        return True, ("Held back — the trial run showed no measurable "
                      "improvement, so nothing was applied. The remaining "
                      "open ends need a decision the ranker cannot make.")

    results = apply_commands(project, cmds)
    ok = sum(1 for good, _ in results if good)
    bad = len(results) - ok
    after = _nz.measure(project)
    msg = [f"Normalized: {ok} command(s) applied"
           + (f", {bad} failed" if bad else "") + ".",
           f"  activities with no logic: {r['before']['floating_activities']} "
           f"→ {after['floating_activities']}",
           f"  isolated folders: {r['before']['isolated']} → {after['isolated']}",
           f"  fully connected folders: {r['before']['connected']} → "
           f"{after['connected']}"]
    if r["skipped_for_duplicates"]:
        msg.append(f"  {len(r['skipped_for_duplicates'])} folder(s) skipped for "
                   f"duplicated rows: {', '.join(r['skipped_for_duplicates'][:5])}")
    if r["unresolved"]:
        msg.append(f"  {r['unresolved']} open row(s) had no candidate above the "
                   f"bar and were left alone")
    if r["capped"]:
        msg.append("  Capped for reviewability — run again to continue.")
    msg.append("  Nothing was deleted. Undo reverts the whole batch.")
    return True, "\n".join(msg)


def _set_udf_type(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Correct the DataType on an existing UDF definition — e.g. a field this
    app created by guessing (now always Text) that turns out to need to
    match what P6 already has on record for that exact field name, such as
    "invalid UDF data type" on import. Does not touch any value; only the
    column's declared type.
    """
    from engine.xml_writer import normalize_udf_type

    field = (cmd.get("field") or "").strip()
    if not field:
        raise EditError("field is required")
    data_type = normalize_udf_type(cmd.get("data_type") or "")
    matches = [u for u in (getattr(project, "udf_types", None) or []) if u.title == field]
    if not matches:
        raise EditError(f"No UDF field named '{field}' — check the exact title "
                        f"(see the column header, or the UDFType list).")
    for u in matches:
        u.data_type = data_type
    return True, f"{field} → DataType {data_type}"


def _update_labor_units(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """Set budgeted labor units on an activity."""
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    try:
        units = float(cmd.get("labor_units"))
    except (TypeError, ValueError):
        raise EditError("labor_units must be a number")
    units = max(0.0, units)
    for a in matches:
        a.planned_labor_units = units
    return True, f"Set {units:g} labor units on {len(matches)} activity/activities"


def _move_activity_wbs(project: Project, cmd: Dict) -> Tuple[bool, str]:
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    wbs = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                    cmd.get("wbs_uid"))
    if not wbs:
        raise EditError("Target " + _no_wbs(project, cmd.get("wbs_code") or cmd.get("wbs_name") or cmd.get("wbs_uid")))
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


# The constraints that DRIVE an early date, and which date each one drives.
# Deadlines ("start on or before", "finish on or before") are deliberately
# absent: they cap the LATE date so a slip past them shows as negative float,
# and moving work onto a deadline would schedule the very problem away.
# This mirrors compute_dates() exactly — if the two ever disagree, the date
# shown and the date computed disagree, which is the bug this list prevents.
_DRIVING_START_CONSTRAINTS = {"start on", "must start on", "mandatory start",
                              "start on or after"}
_DRIVING_FINISH_CONSTRAINTS = {"finish on", "must finish on", "mandatory finish",
                               "finish on or after"}


def _set_constraint(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Pin a date, and move the date being pinned to match.

    Setting the pin alone was not enough. Edits do not reflow Start / Finish
    (that is the Schedule button, as in P6), so a pin that drives an early
    date changed early_start while planned_start — the column the grid
    actually shows — kept its old value. Typing a date into the Start cell of
    a linked, unpinned row sends exactly this command, so the visible result
    was that the date the user typed did nothing at all. Trying again worked,
    because by then the row was pinned and took a different path: that is the
    "sometimes I can't adjust a date that has a constraint" report.

    Only a DRIVING constraint moves the date. A deadline is a statement about
    when work must be finished BY, not an instruction to schedule it then.
    """
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    if len(matches) > 1:
        raise EditError(f"Found {len(matches)} activities — use activity_id for constraints")
    constraint_type = cmd.get("constraint_type", "").strip()
    constraint_date = cmd.get("constraint_date", "").strip()
    if not constraint_type:
        raise EditError("constraint_type is required (e.g. 'Start On Or After', 'Finish On Or Before')")
    a = matches[0]
    a.constraint_type = constraint_type
    a.constraint_date = constraint_date or None

    note = ""
    ct = constraint_type.lower()
    if a.constraint_date and cmd.get("move_date", True):
        import datetime as _d
        try:
            cd = _d.date.fromisoformat(str(a.constraint_date)[:10])
        except ValueError:
            cd = None
        is_ms = a.activity_type in ("Start Milestone", "Finish Milestone")
        if cd and ct in _DRIVING_START_CONSTRAINTS and not a.actual_start:
            wd, hol, hpd = _act_calendar(project, a)
            dur_d = 0.0 if is_ms else (a.planned_duration or 0.0) / hpd
            a.planned_start = a.constraint_date
            a.planned_finish = (a.constraint_date if dur_d <= 0 else
                                _add_working_days(cd, _span_days(dur_d), wd, hol).isoformat())
            note = f" — start moved to {a.constraint_date}"
        elif cd and ct in _DRIVING_FINISH_CONSTRAINTS and not a.actual_finish:
            a.planned_finish = a.constraint_date
            if is_ms:
                a.planned_start = a.constraint_date
            note = f" — finish moved to {a.constraint_date}"
    return True, f"Set constraint '{constraint_type}' on '{a.name}'{note}"


def _clear_constraint(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Unpin. Says what was actually removed, and that the date stays put.

    Removing a pin does not move the work back to where logic would put it —
    edits never reflow Start / Finish here, the same as in P6 where you press
    F9. So the row keeps the date the pin had given it and the pin icon
    disappears, which reads as "the constraint didn't come off" when it did.
    Naming the removed pin and pointing at Schedule is the difference between
    that and a silent no-op.
    """
    matches = _find_activity(project, cmd.get("activity_id"), cmd.get("target_name"))
    if not matches:
        raise EditError(_no_activity(project, cmd.get("activity_id") or cmd.get("target_name")))
    removed = []
    for a in matches:
        if a.constraint_type:
            removed.append(f"{a.activity_id} ({a.constraint_type}"
                           + (f" {str(a.constraint_date)[:10]}" if a.constraint_date else "")
                           + ")")
        a.constraint_type = None
        a.constraint_date = None
    if not removed:
        where = matches[0].activity_id if len(matches) == 1 else f"{len(matches)} activities"
        return True, f"No constraint was set on {where} — nothing to clear"
    if len(removed) == 1:
        return True, (f"Cleared {removed[0]} — the dates stay where they are "
                      f"until you run Schedule")
    return True, (f"Cleared constraints on {len(removed)} activities — the dates "
                  f"stay where they are until you run Schedule")


def _resolve_activity_scope(project: Project, cmd: Dict) -> List[Activity]:
    """
    Shared scope resolver for mass-edit actions. Accepts one of:
      - activity_ids: explicit list of IDs
      - wbs_name / wbs_code: every activity recursively under a folder
      - all: true → every activity in the schedule
    Raises EditError if none of the three are provided or the WBS isn't found.
    """
    activity_ids = cmd.get("activity_ids", [])
    wbs_name = cmd.get("wbs_name")
    wbs_code = cmd.get("wbs_code")
    want_all = cmd.get("all", False)

    if want_all:
        return list(project.activities)
    if activity_ids:
        targets = []
        for aid in activity_ids:
            a = project.get_activity(activity_id=aid)
            if a:
                targets.append(a)
        return targets
    if wbs_name or wbs_code:
        wbs = _find_wbs(project, wbs_code, wbs_name)
        if not wbs:
            raise EditError(f"WBS not found: {wbs_code or wbs_name}")
        # collect all descendant WBS uids
        wbs_uids = {wbs.uid}
        changed = True
        while changed:
            changed = False
            for w in project.wbs_nodes:
                if w.parent_uid in wbs_uids and w.uid not in wbs_uids:
                    wbs_uids.add(w.uid)
                    changed = True
        return [a for a in project.activities if a.wbs_uid in wbs_uids]
    raise EditError("Provide activity_ids, wbs_name/wbs_code, or all=true")


def _bulk_clear_constraints(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Remove all constraints from multiple activities at once.
    Accepts:
      - activity_ids: list of IDs
      - wbs_name / wbs_code: clear recursively under a folder (incl. children)
      - all: true → clear every activity in the schedule
    """
    targets = _resolve_activity_scope(project, cmd)
    if not targets:
        return True, "No activities matched — no constraints to clear"

    cleared = 0
    for a in targets:
        if a.constraint_type or a.constraint_date:
            a.constraint_type = None
            a.constraint_date = None
            cleared += 1

    return True, f"Cleared constraints on {cleared} of {len(targets)} activities"


def _bulk_append_name(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Add text to the end (or start) of multiple activity names WITHOUT
    replacing the name already there — e.g. append "(ER 209)" to every
    activity in a folder so "Terminate wire" becomes "Terminate wire (ER 209)".

    Scope: activity_ids | wbs_name/wbs_code (recursive) | all — same contract
    as bulk_clear_constraints.

    Idempotent: an activity whose name already carries the exact text at that
    position is left alone and counted as "already had it", so re-running the
    same request (e.g. after adding more activities to the folder) doesn't
    pile up "(ER 209) (ER 209)".

      text       — the text to add (required)
      position   — "suffix" (default) or "prefix"
      separator  — joining text, default " " (a single space)
    """
    text = str(cmd.get("text") or "").strip()
    if not text:
        raise EditError("text is required for bulk_append_name")
    position = str(cmd.get("position") or "suffix").lower()
    if position not in ("suffix", "prefix"):
        raise EditError("position must be 'suffix' or 'prefix'")
    separator = cmd.get("separator")
    separator = " " if separator is None else str(separator)

    targets = _resolve_activity_scope(project, cmd)
    if not targets:
        return True, "No activities matched — nothing to rename"

    applied = 0
    already = 0
    for a in targets:
        if position == "suffix":
            if a.name.rstrip().endswith(text):
                already += 1
                continue
            a.name = a.name.rstrip() + separator + text
        else:
            if a.name.lstrip().startswith(text):
                already += 1
                continue
            a.name = text + separator + a.name.lstrip()
        applied += 1

    msg = f"Added \"{text}\" to {applied} of {len(targets)} activity name(s)"
    if already:
        msg += f" ({already} already had it)"
    return True, msg


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


def _add_wbs_for_each(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Add a child folder under EVERY folder matching a pattern, naming each one
    from the parent it lands under.

    This is the shape of request that is painful one folder at a time:
    "add a sub-folder under each MV room called WBO MV <room>". Doing it as
    N separate add_wbs commands means the agent has to enumerate the rooms
    correctly first — which is exactly where it invents names.

      match_contains : parent qualifies if its name contains this (case-insensitive)
      match_regex    : ...or matches this regex. Groups are available to the
                       template as {1}, {2}, ...
      name_template  : name for the new child. Placeholders:
                         {name}  parent folder name        ("MV 105")
                         {code}  parent folder code
                         {num}   first run of digits in the parent name ("105")
                         {1}..{9} regex capture groups
      code_template  : optional, same placeholders (defaults to the name)
      skip_existing  : default true — a parent that already has a child of that
                       name is left alone, so re-running is safe
      under_parent_name / under_parent_code / under_parent_uid :
                       optional, restricts matching to one branch

    Matching never selects a folder that is itself one of the children this
    command would create, so running it twice cannot nest WBO under WBO.
    """
    name_tpl = str(cmd.get("name_template") or "").strip()
    if not name_tpl:
        raise EditError("name_template is required for add_wbs_for_each "
                        "(e.g. \"WBO {name}\")")
    contains = str(cmd.get("match_contains") or "").strip().lower()
    regex_src = str(cmd.get("match_regex") or "").strip()
    if not contains and not regex_src:
        raise EditError("add_wbs_for_each needs match_contains or match_regex")
    try:
        rx = re.compile(regex_src, re.IGNORECASE) if regex_src else None
    except re.error as e:
        raise EditError(f"match_regex is not a valid pattern: {e}")

    scope_uid = None
    if cmd.get("under_parent_uid") or cmd.get("under_parent_code") or cmd.get("under_parent_name"):
        scope = _find_wbs(project, cmd.get("under_parent_code"), cmd.get("under_parent_name"),
                          cmd.get("under_parent_uid"))
        if not scope:
            raise EditError("Scope " + _no_wbs(project, cmd.get("under_parent_name")
                                               or cmd.get("under_parent_code")))
        scope_uid = scope.uid

    def in_scope(node) -> bool:
        if scope_uid is None:
            return True
        by_uid = {w.uid: w for w in project.wbs_nodes}
        cur, guard = node.parent_uid, 0
        while cur and guard < 200:
            if cur == scope_uid:
                return True
            cur = by_uid.get(cur).parent_uid if by_uid.get(cur) else None
            guard += 1
        return False

    def render(tpl: str, node, m) -> str:
        digits = re.search(r"\d+", node.name or "")
        out = (tpl.replace("{name}", node.name or "")
                  .replace("{code}", node.code or "")
                  .replace("{num}", digits.group(0) if digits else ""))
        if m:
            for gi in range(1, (m.re.groups or 0) + 1):
                out = out.replace("{%d}" % gi, m.group(gi) or "")
        return out.strip()

    skip_existing = cmd.get("skip_existing", True)

    # A folder this command already created is usually still a match for the
    # same pattern — "WBO MV 105" contains "MV" — so a second run would nest
    # WBO inside WBO. Recognise the template's own output by the fixed text
    # around its placeholders and never treat that as a parent.
    lit_prefix = name_tpl.split("{", 1)[0].strip().lower()
    lit_suffix = name_tpl.rsplit("}", 1)[-1].strip().lower() if "}" in name_tpl else ""

    def is_own_output(node) -> bool:
        nm = (node.name or "").strip().lower()
        if lit_prefix and nm.startswith(lit_prefix):
            return True
        if lit_suffix and nm.endswith(lit_suffix):
            return True
        return False

    # snapshot first: appending as we go would otherwise let a new child match
    targets = []
    for w in list(project.wbs_nodes):
        if not in_scope(w) or is_own_output(w):
            continue
        m = rx.search(w.name or "") if rx else None
        if rx and not m:
            continue
        if contains and contains not in (w.name or "").lower():
            continue
        targets.append((w, m))

    if not targets:
        raise EditError(f"No WBS folder matches "
                        f"{'regex ' + regex_src if rx else repr(cmd.get('match_contains'))} — "
                        f"nothing to add under")

    children_by_parent = {}
    for w in project.wbs_nodes:
        children_by_parent.setdefault(w.parent_uid, []).append(w)

    created, skipped = [], 0
    for parent, m in targets:
        new_name = render(name_tpl, parent, m)
        if not new_name:
            continue
        sibs = children_by_parent.get(parent.uid, [])
        if skip_existing and any((c.name or "").strip().lower() == new_name.lower()
                                 for c in sibs):
            skipped += 1
            continue
        code_tpl = str(cmd.get("code_template") or "").strip()
        new_code = render(code_tpl, parent, m) if code_tpl else new_name[:20]
        node = WBSNode(uid=_new_uid(), name=new_name, code=new_code,
                       parent_uid=parent.uid,
                       sequence_num=(max(s.sequence_num for s in sibs) + 10) if sibs else 0)
        project.wbs_nodes.append(node)
        children_by_parent.setdefault(parent.uid, []).append(node)
        created.append(f"'{new_name}' under '{parent.name}'")

    project.build_lookups()
    if not created:
        return True, (f"Every one of the {len(targets)} matching folders already has "
                      f"that sub-folder — nothing to add")
    head = "; ".join(created[:8]) + (f"; +{len(created) - 8} more" if len(created) > 8 else "")
    msg = f"Added {len(created)} sub-folder(s): {head}"
    if skipped:
        msg += f" ({skipped} already had one)"
    return True, msg


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
            raise EditError("Parent " + _no_wbs(project, cmd.get("parent_code") or cmd.get("parent_name")))

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

        # Scope: entire WBS, recursively (a folder with sub-folders should not
        # silently skip the activities living one level deeper — the same
        # descendant-collecting rule bulk_clear_constraints uses).
        if wbs_name and not act_id and not from_name:
            try:
                targets = _resolve_activity_scope(project, {"wbs_name": wbs_name})
            except EditError as e:
                errors.append(str(e))
                continue
            for a in targets:
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


def _read_document(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Look inside a document already given for this job.

    Advisory — it changes nothing. The lines were extracted when the document
    arrived, so this costs no model call and no re-upload: it searches what is
    already held and hands back the matching lines, never the whole file. A
    497-line scope answers "what does it say about generators" in a dozen
    lines, and sending the other 485 would cost more than the answer.
    """
    from engine import project_brain
    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    library = getattr(brain, "library", None) if brain else None
    if library is None or not library.docs:
        raise EditError("No documents have been given for this job yet — "
                        "attach the PDF or spreadsheet and I'll read it.")

    name = (cmd.get("document") or cmd.get("name") or "").strip()
    doc = library.find(name) if name else (library.docs[-1] if library.docs else None)
    if doc is None:
        have = ", ".join(d.name for d in library.docs[-6:])
        raise EditError(f"No document here called '{name}'. What I have: {have}")

    query = (cmd.get("query") or cmd.get("about") or "").strip()
    try:
        limit = int(cmd.get("limit") or 12)
    except (TypeError, ValueError):
        limit = 12

    if not doc.searchable:
        lines = [f"{doc.name} (image) — {doc.summary or 'no summary recorded'}"]
        lines.extend(f"  {f}" for f in doc.facts[:10])
        return True, "\n".join(lines)

    got = library.search(doc, query, limit)
    head = (f"{doc.name} — {doc.line_count} lines"
            + (f", {len(doc.sheets)} sheets" if doc.sheets else
               f", {doc.pages} pages" if doc.pages else ""))
    if query:
        head += f". {got['matched']} line(s) mention '{query}'"
        if got["matched"] > len(got["lines"]):
            head += f"; the {len(got['lines'])} strongest follow"
    else:
        head += f". {got.get('note', '')}"
    out = [head + ":"]
    for row in got["lines"]:
        where = f"[{row['where']}] " if row["where"] else ""
        out.append(f"  {where}{row['text']}")
    if not got["lines"]:
        out.append("  nothing in this document mentions that.")
    return True, "\n".join(out)


# The edit engine has no session and no brain — it is handed a project and
# nothing else, deliberately. read_document is the one action that needs the
# job's document library, so the server injects a lookup rather than this
# module reaching for global state it should not know about.
_BRAIN_FOR = None


def set_brain_lookup(fn) -> None:
    """Called once at startup by the server. Tests can point it elsewhere."""
    global _BRAIN_FOR
    _BRAIN_FOR = fn


def _describe_brain(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Everything this job has been taught, in full — read-only.

    The block that rides in every prompt is capped at thirty per section and
    ends with "…and N more (ask to see them all)", but nothing could actually
    answer that: there was no way to see the rest, and the parts deliberately
    kept out of the prompt entirely — how proposals have been received, what
    each document actually is — were invisible to the agent no matter what
    the user asked. This reports the lot, off the real objects rather than
    from anything remembered, so a summary of "what do you know about my job"
    is grounded in what is actually stored.
    """
    from engine.project_brain import describe as _describe

    brain = _BRAIN_FOR(project) if _BRAIN_FOR else None
    if brain is None or brain.is_empty():
        return True, ("Nothing has been taught about this job yet — no rules, no "
                      "objective, no documents. Anything you tell me about how it "
                      "is built I keep against the P6 project id, so it survives "
                      "the next re-export.")

    out = [f"WHAT I KNOW ABOUT THIS JOB ({brain.key}) — everything stored, "
           f"not a sample:"]

    obj = brain.objective_line(project)
    if obj:
        out.append(f"\nOBJECTIVE (measured off the schedule now):\n  {obj}")

    rules = brain.rules
    if rules:
        out.append(f"\nRULES — ENFORCED ({len(rules)}):")
        for d in rules:
            bits = [_describe(d)]
            if d.overridden or d.upheld:
                bits.append(f"overridden {d.overridden}x, kept {d.upheld}x")
            out.append(f"  - {d.text}   [{'; '.join(bits)}]")

    notes = brain.notes
    if notes:
        out.append(f"\nCONTEXT — not enforced ({len(notes)}):")
        out.extend(f"  - {d.text}" for d in notes)

    openq = brain.open_questions
    if openq:
        out.append(f"\nOPEN — stated but matched to no activity ({len(openq)}). "
                   f"These are NOT in force:")
        out.extend(f"  - {d.text}   [{d.note_reason}]" for d in openq)

    disabled = [d for d in brain.directives if not d.enabled]
    if disabled:
        out.append(f"\nTURNED OFF ({len(disabled)}):")
        out.extend(f"  - {d.text}" for d in disabled)

    scope = getattr(brain, "scope", None)
    if scope is not None:
        try:
            out.append("\nSCOPE OF WORK (read from a document):\n"
                       + scope.context_block())
        except Exception:
            pass

    library = getattr(brain, "library", None)
    docs = list(getattr(library, "docs", None) or []) if library else []
    if docs:
        out.append(f"\nDOCUMENTS ON FILE ({len(docs)}) — ask me to read any of "
                   f"these by name:")
        for d in docs:
            kind = getattr(d, "kind", "") or "document"
            out.append(f"  - {getattr(d, 'name', '?')} ({kind})")

    fb = getattr(brain, "feedback", None) or {}
    if fb:
        acc = sum(v.get("accepted", 0) for v in fb.values())
        dec = sum(v.get("declined", 0) for v in fb.values())
        out.append(f"\nHOW MY SUGGESTIONS HAVE LANDED: {acc} accepted, "
                   f"{dec} declined, across {len(fb)} kind(s) of proposal. "
                   f"This steers ranking; it is not a rule.")

    return True, "\n".join(out)


def _normalize_activity_ids(project: Project, cmd: Dict) -> Tuple[bool, str]:
    """
    Put every stray activity code back on the job's own pattern.

    The convention is read out of the ids already in the file — MDC1.MIL.####
    for milestones, MDC1.FDG.#### in foundations — and rows that drifted onto
    generic codes are proposed a conforming one in their own folder's prefix.
    Safe for the network: relations bind by uid, not by this code.

    preview=true reports without writing. `changes` applies an exact list that
    was previewed, so what the user approved is what lands rather than a fresh
    computation that might differ.
    """
    from engine import id_normalizer

    changes = cmd.get("changes")
    if changes is None:
        scope_uid = None
        name = cmd.get("wbs_name") or cmd.get("wbs_code") or cmd.get("wbs_uid")
        if name:
            w = _find_wbs(project, cmd.get("wbs_code"), cmd.get("wbs_name"),
                          cmd.get("wbs_uid"))
            if not w:
                raise EditError(_no_wbs(project, name))
            scope_uid = w.uid
        report = id_normalizer.plan(project, scope_uid)
        changes = report["changes"]
        notes = report["skipped"]
        convention = report["convention"]
    else:
        if not isinstance(changes, list):
            raise EditError("changes must be a list")
        notes, convention = [], None

    problems = id_normalizer.validate(project, changes)
    if problems:
        raise EditError("; ".join(problems[:3]))

    # An explicit list carries only uid and the wanted code — the rest is
    # filled in from the project so the report reads the same either way.
    by_uid = {a.uid: a for a in project.activities}
    rows = []
    for c in changes:
        a = by_uid.get(c.get("uid"))
        rows.append((c.get("from") or (a.activity_id if a else "?"),
                     c.get("to"),
                     c.get("name") or (a.name if a else "")))

    def _listing(fmt):
        out = [fmt(f, t, nm) for f, t, nm in rows[:20]]
        if len(rows) > 20:
            out.append(f"    …and {len(rows) - 20} more")
        out.extend("    NOTE: " + n for n in notes)
        return out

    head = f"{len(changes)} activity id(s)"
    if convention:
        head += f" off the '{convention}' pattern"
    if bool(cmd.get("preview")):
        return True, "\n".join([f"{head} would be renamed:"]
                               + _listing(lambda f, t, nm: f"    {f} → {t}  ({nm})"))

    n = id_normalizer.apply_changes(project, changes)
    return True, "\n".join([f"Renamed {n} activity id(s) onto the project pattern."]
                           + _listing(lambda f, t, nm: f"    {f} → {t}"))
