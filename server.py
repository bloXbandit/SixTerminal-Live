"""
server.py — Local Flask web server for Six Terminal Live.

Lives at the project root so Python can import it directly from main.py
without descending into subdirectories (avoids importlib permission issues
on systems with restrictive Controlled Folder Access policies).

Runs on http://localhost:5100
"""

import os
import sys
import copy
import json
import tempfile
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file

ROOT = Path(__file__).parent          # C:\SixTerminal-Live
sys.path.insert(0, str(ROOT))

from engine.xer_reader import load_xer
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.edit_engine import (
    apply_commands,
    check_disambiguation,
    generate_schedule_report,
)
from interpreter.llm_interpreter import interpret, create_project, MODELS, DEFAULT_MODEL
from engine.importer import extract as import_extract, build_project_from_contract

TEMPLATE_DIR = str(ROOT / "ui" / "templates")
STATIC_DIR   = str(ROOT / "ui" / "static")


# ── Enriched LLM context (lives here to avoid touching engine/ subdirectory) ──

def build_llm_context(project, max_activities: int = 120) -> str:
    """
    Rich context string for the LLM.
    Includes WBS, full activity list with pred/succ links,
    float-derived criticality, critical path chain, and suggested next ID.

    Criticality rules (DCMA / P6 best practice):
      critical      = total_float <= 0 h
      near_critical = 0 < total_float <= 80 h  (~10 working days)
    """
    wbs_map     = {w.uid: w for w in project.wbs_nodes}
    act_by_uid  = {a.uid: a for a in project.activities}

    # ── Pred / succ maps ──────────────────────────────────────────────────
    preds_of = {}   # activity uid -> ["A1000 FS", ...]
    succs_of = {}

    for rel in project.relations:
        p = act_by_uid.get(rel.predecessor_uid)
        s = act_by_uid.get(rel.successor_uid)
        if not p or not s:
            continue
        rt   = rel.type
        abbr = ("FS" if "Finish to Start" in rt else
                "SS" if "Start to Start"  in rt else
                "FF" if "Finish to Finish" in rt else "SF")
        lag_str = ""
        if rel.lag:
            ld = rel.lag / 8.0
            lag_str = f"+{ld:.0f}d" if ld > 0 else f"{ld:.0f}d"
        succs_of.setdefault(p.uid, []).append(f"{s.activity_id} {abbr}{lag_str}")
        preds_of.setdefault(s.uid, []).append(f"{p.activity_id} {abbr}{lag_str}")

    # ── Float helpers (derive criticality — do NOT trust P6's is_critical) ─
    def float_hrs(a):
        return a.total_float if a.total_float is not None else a.free_float

    def crit_tag(a):
        f = float_hrs(a)
        if f is None:       return ""
        if f <= 0:          return " [CRITICAL, float=0]"
        if f <= 80:         return f" [NEAR-CRITICAL, float={f/8:.1f}d]"
        return ""

    # ── Critical path walk (backward from latest finish milestone) ─────────
    MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}
    finish_milestones = [a for a in project.activities
                         if a.activity_type == "Finish Milestone"
                         and a.status != "Completed"]
    cp_chain = []
    if finish_milestones:
        target = max(finish_milestones, key=lambda a: a.planned_finish or "")
        pred_uid_map = {}
        for rel in project.relations:
            pred_uid_map.setdefault(rel.successor_uid, []).append(rel.predecessor_uid)
        visited, current = set(), target.uid
        for _ in range(60):
            act = act_by_uid.get(current)
            if not act or current in visited:
                break
            visited.add(current)
            cp_chain.append(act.activity_id)
            candidates = [act_by_uid[uid] for uid in pred_uid_map.get(current, [])
                          if uid in act_by_uid and uid not in visited]
            if not candidates:
                break
            candidates.sort(key=lambda x: (
                float_hrs(x) if float_hrs(x) is not None else 9999,
                -(hash(x.planned_finish or "")),
            ))
            current = candidates[0].uid

    # ── Summary counts ────────────────────────────────────────────────────
    crit_count     = sum(1 for a in project.activities if (float_hrs(a) or 1) <= 0)
    near_crit_count= sum(1 for a in project.activities
                         if float_hrs(a) is not None and 0 < float_hrs(a) <= 80)
    open_start     = sum(1 for a in project.activities
                         if not preds_of.get(a.uid) and a.activity_type not in MILESTONE_TYPES)
    open_finish    = sum(1 for a in project.activities
                         if not succs_of.get(a.uid) and a.activity_type not in MILESTONE_TYPES)

    # ── Build output ──────────────────────────────────────────────────────
    lines = [
        f"Project: {project.name} ({project.id})",
        f"Data Date: {project.data_date}  |  Planned Start: {project.planned_start}",
        f"Activities: {len(project.activities)}  |  WBS Nodes: {len(project.wbs_nodes)}  |  Relations: {len(project.relations)}",
        f"Critical (float<=0): {crit_count}  |  Near-Critical (<=80h): {near_crit_count}"
        f"  |  Open Start: {open_start}  |  Open Finish: {open_finish}",
        "",
        "WBS STRUCTURE:",
    ]
    for w in project.wbs_nodes:
        parent = wbs_map.get(w.parent_uid) if w.parent_uid else None
        indent = "    " if parent else "  "
        lines.append(f"{indent}{w.code} - {w.name}"
                     + (f"  (parent: {parent.name})" if parent else ""))

    if cp_chain:
        lines += ["", f"CRITICAL PATH ({len(cp_chain)} steps, backward from end):",
                  "  " + " -> ".join(cp_chain)]

    lines += ["", f"ACTIVITIES ({len(project.activities)} total):"]
    for a in project.activities[:max_activities]:
        wbs      = wbs_map.get(a.wbs_uid)
        wbs_name = wbs.name if wbs else "?"
        dur      = f"{a.planned_duration/8:.0f}d" if a.planned_duration else "0d"
        preds_str = ("PREDS: " + ", ".join(preds_of[a.uid])) if preds_of.get(a.uid) else ""
        succs_str = ("SUCCS: " + ", ".join(succs_of[a.uid])) if succs_of.get(a.uid) else ""
        rel_part  = ("  |  " + "  |  ".join(filter(None, [preds_str, succs_str]))
                     if preds_str or succs_str else "")
        constraint = f" [CONSTRAINT: {a.constraint_type}]" if a.constraint_type else ""
        lines.append(
            f"  {a.activity_id} - {a.name}"
            f"  |  WBS: {wbs_name}  |  {dur}  |  {a.status}"
            f"{rel_part}{crit_tag(a)}{constraint}"
        )
    if len(project.activities) > max_activities:
        lines.append(f"  ... ({len(project.activities) - max_activities} more not shown)")

    # ── Suggest next activity ID ──────────────────────────────────────────
    numeric_ids = []
    for a in project.activities:
        try:
            numeric_ids.append(int(a.activity_id.lstrip("AaBbCc")))
        except ValueError:
            pass
    if numeric_ids:
        last_num = max(numeric_ids)
        next_num = ((last_num // 10) + 1) * 10
        prefix = next((a.activity_id[0] for a in project.activities
                       if a.activity_id[0].isalpha()), "")
        lines += ["", f"SUGGESTED NEXT ACTIVITY ID: {prefix}{next_num:04d}"
                      f"  (last used: {prefix}{last_num:04d})"]

    return "\n".join(lines)

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=TEMPLATE_DIR)

_MAX_UNDO = 50

# ── Global settings (not per-project) ─────────────────────────────────────────
_settings: dict = {
    "model_key": DEFAULT_MODEL,
    "api_key":   None,
}

# ── Per-project sessions ───────────────────────────────────────────────────────
_projects: dict = {}    # project_id -> session dict
_active_id: list = [None]   # mutable container so helpers can mutate it


def _make_session(pid: str, source_name: str) -> dict:
    return {
        "project_id":   pid,
        "source_name":  source_name,
        "project":      None,
        "source_path":  None,
        "edit_history": [],
        "undo_stack":   [],
        "redo_stack":   [],
        "chat_history": [],
        "last_undone":  None,
    }


def _get_session() -> dict:
    return _projects.get(_active_id[0]) if _active_id[0] else None


def _unique_pid(stem: str) -> str:
    if stem not in _projects:
        return stem
    i = 2
    while f"{stem}_{i}" in _projects:
        i += 1
    return f"{stem}_{i}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _append_chat(role: str, text: str):
    sess = _get_session()
    if sess is not None:
        sess["chat_history"].append({"role": role, "text": text})

def _push_undo(label: str):
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return
    stack = sess["undo_stack"]
    stack.append((label, copy.deepcopy(sess["project"])))
    if len(stack) > _MAX_UNDO:
        stack.pop(0)


def _project_list_item(pid: str) -> dict:
    sess = _projects[pid]
    proj = sess["project"]
    return {
        "id":             pid,
        "source_name":    sess["source_name"],
        "project_name":   proj.name if proj else sess["source_name"],
        "activity_count": len(proj.activities) if proj else 0,
        "data_date":      str(proj.data_date)[:10] if proj and proj.data_date else None,
        "is_active":      pid == _active_id[0],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    filename = f.filename or "schedule"
    ext = Path(filename).suffix.lower()

    if ext not in (".xer", ".xml"):
        return jsonify({"error": f"Unsupported file type '{ext}'. Upload an XER or P6 XML file."}), 400

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    f.save(tmp.name)
    tmp.close()

    try:
        project = load_xer(tmp.name) if ext == ".xer" else load_xml(tmp.name)
        pid = _unique_pid(Path(filename).stem)
        sess = _make_session(pid, filename)
        sess["project"]     = project
        sess["source_path"] = tmp.name
        _projects[pid]      = sess
        _active_id[0]       = pid

        return jsonify({
            "success":        True,
            "project_id":     pid,
            "summary":        project.summary(),
            "project_name":   project.name,
            "activity_count": len(project.activities),
            "wbs_count":      len(project.wbs_nodes),
            "relation_count": len(project.relations),
            "data_date":      project.data_date,
            "projects":       [_project_list_item(k) for k in _projects],
        })
    except Exception as e:
        return jsonify({"error": f"Failed to parse file: {str(e)}"}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@app.route("/api/import/extract", methods=["POST"])
def import_extract_route():
    """
    Deterministic, offline extraction of a schedule from Excel or PDF.
    Returns the review contract WITHOUT loading it — the user confirms first,
    then calls /api/import/commit. No data leaves the machine unless the caller
    explicitly opts into AI assist (use_llm=true) for a scanned PDF.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    filename = f.filename or "schedule"
    ext = Path(filename).suffix.lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".pdf"):
        return jsonify({"error": f"Unsupported type '{ext}'. Upload an Excel (.xlsx) "
                                 f"or PDF schedule export."}), 400

    pdf_engine = request.form.get("pdf_engine", "auto")
    use_llm    = request.form.get("use_llm", "").lower() in ("1", "true", "yes")

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    f.save(tmp.name)
    tmp.close()
    try:
        contract = import_extract(
            tmp.name, pdf_engine=pdf_engine, use_llm=use_llm,
            api_key=_settings.get("api_key"), model_key=_settings.get("model_key"),
        )
        contract["meta"]["source_name"] = filename
        contract["meta"]["project_name"] = Path(filename).stem   # real upload name, not temp
        return jsonify({"success": True, "contract": contract})
    except NotImplementedError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Extraction failed: {str(e)}", "trace": traceback.format_exc()}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@app.route("/api/import/commit", methods=["POST"])
def import_commit_route():
    """Materialize a reviewed extraction contract and load it as the active schedule."""
    data = request.get_json() or {}
    contract = data.get("contract")
    if not contract or not isinstance(contract, dict):
        return jsonify({"error": "contract is required"}), 400
    mode = (data.get("mode") or "replace").lower()   # replace | merge
    name = data.get("project_name")
    if name:
        contract.setdefault("meta", {})["project_name"] = name

    try:
        project = build_project_from_contract(contract)
        if mode == "merge" and _get_session() and _get_session()["project"]:
            # append imported WBS + activities into the current active project
            base = _get_session()["project"]
            _push_undo(f"Import (merge) {contract['meta'].get('source_name','file')}")
            _merge_projects(base, project)
            base.build_lookups()
            from engine.schedule_model import compute_dates
            try:
                compute_dates(base)
            except Exception:
                pass
            project = base
            pid = _active_id[0]
        else:
            pid = _unique_pid(project.id or Path(str(name or "import")).stem or "import")
            sess = _make_session(pid, contract["meta"].get("source_name", f"{pid}.xlsx"))
            sess["project"] = project
            _projects[pid] = sess
            _active_id[0] = pid

        return jsonify({
            "success": True, "project_id": pid, "project_name": project.name,
            "activity_count": len(project.activities), "wbs_count": len(project.wbs_nodes),
            "relation_count": len(project.relations), "data_date": project.data_date,
            "logic_status": contract["meta"].get("logic_status", "absent"),
            "summary": project.summary(),
            "projects": [_project_list_item(k) for k in _projects],
        })
    except Exception as e:
        return jsonify({"error": f"Import failed: {str(e)}", "trace": traceback.format_exc()}), 500


def _merge_projects(base, incoming):
    """Append incoming WBS nodes + activities into base, de-duplicating IDs."""
    existing_wbs_codes = {w.code for w in base.wbs_nodes}
    for w in incoming.wbs_nodes:
        if w.code not in existing_wbs_codes:
            base.wbs_nodes.append(w)
            existing_wbs_codes.add(w.code)
    existing_ids = {a.activity_id for a in base.activities}
    for a in incoming.activities:
        aid = a.activity_id
        while aid in existing_ids:
            aid += "-i"
        a.activity_id = aid
        existing_ids.add(aid)
        base.activities.append(a)
    base.relations.extend(incoming.relations)


@app.route("/api/edit", methods=["POST"])
def edit():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded. Upload an XER or XML file first."}), 400

    data = request.get_json()
    if not data or not data.get("instruction"):
        return jsonify({"error": "instruction is required"}), 400

    instruction    = data["instruction"].strip()
    project        = sess["project"]
    force_commands = data.get("force_commands")
    raw_llm        = ""

    if not force_commands:
        _append_chat("user", instruction)

    try:
        if force_commands is not None:
            commands = force_commands
        else:
            llm_ctx = project.llm_context()
            if sess.get("last_undone"):
                llm_ctx += f"\n\nRECENT UNDO: The user just undid: \"{sess['last_undone']}\". If asked to redo, you know exactly what was done."
            commands, raw_llm = interpret(
                instruction,
                project_summary=llm_ctx,
                edit_history=sess["edit_history"],
                model_key=_settings["model_key"],
                api_key=_settings["api_key"],
            )

            if commands and commands[0].get("action") == "error":
                return jsonify({"success": False, "error": commands[0].get("message", "Could not interpret instruction"), "raw_llm": raw_llm})

            if commands and commands[0].get("action") == "clarify":
                return jsonify({"type": "clarify", "question": commands[0].get("question", "Could you provide more details?"), "instruction": instruction, "raw_llm": raw_llm})

            ambig = check_disambiguation(project, commands)
            if ambig is not None:
                return jsonify({"type": "disambiguation", "instruction": instruction, "commands": commands,
                                "command_index": ambig["command_index"], "field": ambig["field"],
                                "search_term": ambig["search_term"], "matches": ambig["matches"], "raw_llm": raw_llm})

        chat_message = None
        edit_commands = []
        for cmd in commands:
            action = cmd.get("action")
            if action == "chat":
                if chat_message is None:
                    chat_message = cmd.get("message", "")
            elif action == "clarify":
                if chat_message is None:
                    chat_message = cmd.get("question", cmd.get("message", ""))
            else:
                edit_commands.append(cmd)

        if not edit_commands:
            # Detect cold-start failure: first ever edit attempt returned pure chat.
            # The LLM sometimes returns a conversational response instead of JSON commands
            # on the very first API call when there is no session history yet.
            # Retry once with an explicit JSON reminder injected into the instruction.
            _edit_keywords = (
                "add", "create", "delete", "remove", "move", "update", "change",
                "rename", "set", "assign", "link", "split", "merge", "shift",
                "extend", "shorten", "complete", "finish", "start", "schedule",
                "import", "export", "bulk", "wbs", "activity", "resource",
            )
            is_likely_edit = any(kw in instruction.lower() for kw in _edit_keywords)
            if is_likely_edit and not sess.get("edit_history"):
                retry_instruction = (
                    instruction
                    + "\n\n[SYSTEM REMINDER: You MUST respond with a valid JSON array of "
                    "command objects only — no prose, no markdown. If you are unsure, "
                    "use [{\"action\": \"chat\", \"message\": \"...\"}].]"
                )
                commands2, raw_llm2 = interpret(
                    retry_instruction,
                    project_summary=project.llm_context(),
                    edit_history=[],
                    model_key=_settings["model_key"],
                    api_key=_settings["api_key"],
                )
                raw_llm = raw_llm2
                chat_message = None
                edit_commands = []
                for cmd in commands2:
                    action = cmd.get("action")
                    if action == "chat":
                        if chat_message is None:
                            chat_message = cmd.get("message", "")
                    elif action == "clarify":
                        if chat_message is None:
                            chat_message = cmd.get("question", cmd.get("message", ""))
                    else:
                        edit_commands.append(cmd)

            if not edit_commands:
                # Pure chat — do NOT add to edit_history; no schedule changes were made
                msg = chat_message or "..."
                _append_chat("assistant", msg)
                return jsonify({"type": "chat", "message": msg, "raw_llm": raw_llm})

        _push_undo(instruction)
        results = apply_commands(project, edit_commands)

        applied       = [(cmd, ok, msg) for (cmd, (ok, msg)) in zip(edit_commands, results)]
        success_count = sum(1 for _, ok, _ in applied if ok)
        fail_count    = len(applied) - success_count

        if success_count == 0:
            sess["undo_stack"].pop()
        else:
            sess["redo_stack"].clear()
            sess["last_undone"] = None

        sess["edit_history"].append({
            "instruction": instruction,
            "commands":    commands,
            "results":     [{"action": cmd.get("action"), "success": ok, "message": msg} for cmd, ok, msg in applied],
        })

        if chat_message:
            _append_chat("assistant", chat_message)
        edit_summary = f"Applied {success_count} edit{'s' if success_count != 1 else ''}"
        if fail_count > 0:
            edit_summary += f", {fail_count} failed"
        _append_chat("system_result", edit_summary)

        return jsonify({
            "type":             "result",
            "chat_message":     chat_message,
            "success":          fail_count == 0,
            "commands_applied": success_count,
            "commands_failed":  fail_count,
            "results":          [{"action": cmd.get("action"), "success": ok, "message": msg} for cmd, ok, msg in applied],
            "commands":         commands,
            "project_summary":  project.summary(),
            "undo_count":       len(sess["undo_stack"]),
            "redo_count":       len(sess["redo_stack"]),
            "edit_count":       len(sess["edit_history"]),
        })

    except Exception as e:
        return jsonify({"error": f"Edit failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/undo", methods=["POST"])
def undo():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    stack = sess["undo_stack"]
    if not stack:
        return jsonify({"error": "Nothing to undo"}), 400
    label, snapshot = stack.pop()
    if sess["edit_history"]:
        sess["edit_history"].pop()
    sess["redo_stack"].append((label, copy.deepcopy(sess["project"])))
    sess["last_undone"] = label
    sess["project"] = snapshot
    project = snapshot
    return jsonify({"success": True, "undone_label": label, "undo_count": len(stack),
                    "redo_count": len(sess["redo_stack"]), "project_name": project.name,
                    "activity_count": len(project.activities), "wbs_count": len(project.wbs_nodes),
                    "relation_count": len(project.relations), "edit_count": len(sess["edit_history"])})


@app.route("/api/redo", methods=["POST"])
def redo():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    stack = sess["redo_stack"]
    if not stack:
        return jsonify({"error": "Nothing to redo"}), 400
    label, snapshot = stack.pop()
    _push_undo(label)
    sess["last_undone"] = None
    sess["project"] = snapshot
    project = snapshot
    return jsonify({"success": True, "redone_label": label, "undo_count": len(sess["undo_stack"]),
                    "redo_count": len(stack), "project_name": project.name,
                    "activity_count": len(project.activities), "wbs_count": len(project.wbs_nodes),
                    "relation_count": len(project.relations), "edit_count": len(sess["edit_history"])})


@app.route("/api/direct", methods=["POST"])
def direct_edit():
    """
    Apply structured edit commands directly, bypassing the LLM.

    Used by the Schedule grid for inline edits, relationship links, quick-add
    WBS/activities, and bulk operations — no API round-trip, no token cost.
    Shares the same edit engine, undo stack, and CPM recompute as /api/edit,
    and records the change in edit_history so the agent stays aware of manual
    edits made this session.

    Body: {"commands": [...], "label": "human-readable summary"}
    """
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded. Upload a file first."}), 400

    data = request.get_json() or {}
    commands = data.get("commands")
    label = (data.get("label") or "Direct edit").strip()
    if not commands or not isinstance(commands, list):
        return jsonify({"error": "commands (a non-empty list) is required"}), 400

    project = sess["project"]
    try:
        _push_undo(label)
        results = apply_commands(project, commands)
        applied       = list(zip(commands, results))
        success_count = sum(1 for _, (ok, _) in applied if ok)
        fail_count    = len(applied) - success_count

        if success_count == 0:
            sess["undo_stack"].pop()
        else:
            sess["redo_stack"].clear()
            sess["last_undone"] = None
            sess["edit_history"].append({
                "instruction": f"[direct] {label}",
                "commands":    commands,
                "results":     [{"action": c.get("action"), "success": ok, "message": msg}
                                for c, (ok, msg) in applied],
            })

        return jsonify({
            "type":             "result",
            "success":          fail_count == 0,
            "commands_applied": success_count,
            "commands_failed":  fail_count,
            "results":          [{"action": c.get("action"), "success": ok, "message": msg}
                                 for c, (ok, msg) in applied],
            "undo_count":       len(sess["undo_stack"]),
            "redo_count":       len(sess["redo_stack"]),
            "edit_count":       len(sess["edit_history"]),
            "activity_count":   len(project.activities),
            "wbs_count":        len(project.wbs_nodes),
            "relation_count":   len(project.relations),
        })
    except Exception as e:
        return jsonify({"error": f"Direct edit failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/report", methods=["GET"])
def schedule_report():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    return jsonify(generate_schedule_report(sess["project"]))


@app.route("/api/create", methods=["POST"])
def create_new_project():
    data = request.get_json()
    if not data or not data.get("description"):
        return jsonify({"error": "description is required"}), 400
    description = data["description"].strip()
    try:
        project, raw_llm = create_project(description, model_key=_settings["model_key"], api_key=_settings["api_key"])
        pid = _unique_pid(project.id or "project")
        sess = _make_session(pid, f"{pid}.xml")
        sess["project"] = project
        _projects[pid]  = sess
        _active_id[0]   = pid
        return jsonify({
            "success": True, "project_id": pid, "project_name": project.name,
            "activity_count": len(project.activities), "wbs_count": len(project.wbs_nodes),
            "relation_count": len(project.relations), "data_date": project.data_date,
            "summary": project.summary(), "raw_llm": raw_llm,
            "projects": [_project_list_item(k) for k in _projects],
        })
    except Exception as e:
        return jsonify({"error": f"Project creation failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/download", methods=["GET"])
def download():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    project     = sess["project"]
    stem        = Path(sess.get("source_name", "schedule")).stem
    output_name = f"{stem}_edited.xml"
    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()
    try:
        write_p6_xml(project, tmp.name)
        return send_file(tmp.name, as_attachment=True, download_name=output_name, mimetype="application/xml")
    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@app.route("/api/history", methods=["GET"])
def history():
    sess = _get_session()
    return jsonify({"history": sess["edit_history"] if sess else []})


@app.route("/api/status", methods=["GET"])
def status():
    sess      = _get_session()
    project   = sess["project"] if sess else None
    model_cfg = MODELS.get(_settings["model_key"], {})
    base = {"model_key": _settings["model_key"], "model_label": model_cfg.get("label", _settings["model_key"]),
            "api_key_set": bool(_settings["api_key"]), "projects": [_project_list_item(k) for k in _projects]}
    if project is None:
        return jsonify({**base, "loaded": False, "undo_count": 0, "redo_count": 0})
    return jsonify({**base, "loaded": True, "project_name": project.name, "active_project_id": _active_id[0],
                    "source_name": sess.get("source_name"), "activity_count": len(project.activities),
                    "wbs_count": len(project.wbs_nodes), "relation_count": len(project.relations),
                    "edit_count": len(sess["edit_history"]), "undo_count": len(sess["undo_stack"]),
                    "redo_count": len(sess["redo_stack"]), "data_date": str(project.data_date)[:10] if project.data_date else None})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    model_cfg = MODELS.get(_settings["model_key"], {})
    return jsonify({"model_key": _settings["model_key"], "model_label": model_cfg.get("label", _settings["model_key"]),
                    "api_key_set": bool(_settings["api_key"]),
                    "available_models": [{"key": k, "label": v["label"], "provider": v["provider"]} for k, v in MODELS.items()]})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json() or {}
    if "model_key" in data:
        key = data["model_key"]
        if key not in MODELS:
            return jsonify({"error": f"Unknown model '{key}'."}), 400
        _settings["model_key"] = key
    if "api_key" in data:
        val = data["api_key"].strip() if data["api_key"] else ""
        _settings["api_key"] = val if val else None
    model_cfg = MODELS.get(_settings["model_key"], {})
    return jsonify({"success": True, "model_key": _settings["model_key"],
                    "model_label": model_cfg.get("label", _settings["model_key"]), "api_key_set": bool(_settings["api_key"])})


@app.route("/api/projects", methods=["GET"])
def list_projects():
    return jsonify({"projects": [_project_list_item(k) for k in _projects], "active_id": _active_id[0]})


@app.route("/api/projects/switch", methods=["POST"])
def switch_project():
    data = request.get_json() or {}
    pid  = data.get("project_id")
    if pid not in _projects:
        return jsonify({"error": f"Project '{pid}' not found"}), 404
    _active_id[0] = pid
    sess    = _projects[pid]
    project = sess["project"]
    model_cfg = MODELS.get(_settings["model_key"], {})
    return jsonify({
        "success":        True,
        "project_id":     pid,
        "project_name":   project.name,
        "activity_count": len(project.activities),
        "wbs_count":      len(project.wbs_nodes),
        "relation_count": len(project.relations),
        "data_date":      str(project.data_date)[:10] if project.data_date else None,
        "edit_count":     len(sess["edit_history"]),
        "undo_count":     len(sess["undo_stack"]),
        "redo_count":     len(sess["redo_stack"]),
        "messages":       sess["chat_history"],
        "model_key":      _settings["model_key"],
        "model_label":    model_cfg.get("label", _settings["model_key"]),
        "api_key_set":    bool(_settings["api_key"]),
        "projects":       [_project_list_item(k) for k in _projects],
    })


@app.route("/api/projects/delete", methods=["POST"])
def delete_project():
    data = request.get_json() or {}
    pid  = data.get("project_id")
    if pid not in _projects:
        return jsonify({"error": f"Project '{pid}' not found"}), 404
    del _projects[pid]
    if _active_id[0] == pid:
        _active_id[0] = next(iter(_projects), None)
    return jsonify({"success": True, "active_id": _active_id[0],
                    "projects": [_project_list_item(k) for k in _projects]})


@app.route("/api/schedule", methods=["GET"])
def schedule_view():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    try:
        return _schedule_view_inner()
    except Exception as e:
        return jsonify({"error": f"Schedule build failed: {str(e)}", "trace": traceback.format_exc()}), 500


def _schedule_view_inner():
    project = _get_session()["project"]

    # Build predecessor / successor maps keyed by activity uid
    preds_map: dict = {}   # uid -> list of activity_id strings
    succs_map: dict = {}

    for rel in project.relations:
        pred_act = project.get_activity(uid=rel.predecessor_uid)
        succ_act = project.get_activity(uid=rel.successor_uid)
        if pred_act and succ_act:
            succs_map.setdefault(rel.predecessor_uid, []).append({
                "activity_id": succ_act.activity_id,
                "type": rel.type,
                "lag": rel.lag,
            })
            preds_map.setdefault(rel.successor_uid, []).append({
                "activity_id": pred_act.activity_id,
                "type": rel.type,
                "lag": rel.lag,
            })

    # Determine WBS depth for indentation
    wbs_by_uid = {w.uid: w for w in project.wbs_nodes}

    def wbs_depth(uid):
        depth = 0
        node = wbs_by_uid.get(uid)
        while node and node.parent_uid:
            depth += 1
            node = wbs_by_uid.get(node.parent_uid)
        return depth

    # Group activities by wbs_uid preserving WBS order
    acts_by_wbs: dict = {}
    for a in project.activities:
        acts_by_wbs.setdefault(a.wbs_uid, []).append(a)

    MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}

    def fmt_date(d):
        if not d:
            return None
        # Strip time portion if present (ISO datetime → date)
        return str(d)[:10] if d else None

    wbs_sections = []
    for wbs in project.wbs_nodes:
        activities_out = []
        for a in acts_by_wbs.get(wbs.uid, []):
            is_milestone = a.activity_type in MILESTONE_TYPES
            dur_days = round(a.planned_duration / 8.0, 1) if a.planned_duration else 0.0
            activities_out.append({
                "uid":              a.uid,
                "activity_id":      a.activity_id,
                "name":             a.name,
                "duration_days":    dur_days,
                "planned_start":    fmt_date(a.planned_start),
                "planned_finish":   fmt_date(a.planned_finish),
                "actual_start":     fmt_date(a.actual_start),
                "actual_finish":    fmt_date(a.actual_finish),
                "early_start":      fmt_date(a.early_start),
                "early_finish":     fmt_date(a.early_finish),
                "late_start":       fmt_date(a.late_start),
                "late_finish":      fmt_date(a.late_finish),
                "total_float":      round(a.total_float / 8.0, 1) if a.total_float is not None else None,
                "free_float":       round(a.free_float / 8.0, 1) if a.free_float is not None else None,
                "status":           a.status,
                "percent_complete": a.percent_complete,
                "activity_type":    a.activity_type,
                "is_milestone":     is_milestone,
                "is_critical":      a.is_critical,
                "is_longest_path":  a.is_longest_path,
                "constraint_type":  a.constraint_type,
                "constraint_date":  fmt_date(a.constraint_date),
                "predecessors":     preds_map.get(a.uid, []),
                "successors":       succs_map.get(a.uid, []),
            })
        wbs_sections.append({
            "uid":        wbs.uid,
            "name":       wbs.name,
            "code":       wbs.code,
            "parent_uid": wbs.parent_uid,
            "depth":      wbs_depth(wbs.uid),
            "activities": activities_out,
        })

    return jsonify({
        "project_name":   project.name,
        "data_date":      project.data_date,
        "activity_count": len(project.activities),
        "wbs_sections":   wbs_sections,
    })


@app.route("/api/messages", methods=["GET"])
def get_messages():
    sess = _get_session()
    return jsonify({"messages": sess["chat_history"] if sess else []})


@app.route("/api/clear", methods=["POST"])
def clear_session():
    """Clear all projects and reset state."""
    _projects.clear()
    _active_id[0] = None
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=False)
