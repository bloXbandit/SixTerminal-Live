"""
app.py — Local Flask web server for Six Terminal Live desktop UI.

Runs on http://localhost:5100
Opened automatically by main.py on startup.
"""

import os
import sys
import copy
import json
import tempfile
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file

ROOT = Path(__file__).parent.parent
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

app = Flask(__name__, static_folder="static", template_folder="templates")

_MAX_UNDO = 50  # max undo levels kept in memory

_session = {
    "project": None,
    "source_path": None,
    "source_name": None,
    "edit_history": [],
    "undo_stack": [],      # list of (instruction_label, deep-copied Project)
    "model_key": DEFAULT_MODEL,
    "api_key": None,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _push_undo(label: str):
    """Snapshot current project onto the undo stack before an edit."""
    if _session["project"] is None:
        return
    stack = _session["undo_stack"]
    stack.append((label, copy.deepcopy(_session["project"])))
    if len(stack) > _MAX_UNDO:
        stack.pop(0)


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
        _session["project"] = project
        _session["source_path"] = tmp.name
        _session["source_name"] = filename
        _session["edit_history"] = []
        _session["undo_stack"] = []

        return jsonify({
            "success": True,
            "summary": project.summary(),
            "project_name": project.name,
            "activity_count": len(project.activities),
            "wbs_count": len(project.wbs_nodes),
            "relation_count": len(project.relations),
            "data_date": project.data_date,
        })
    except Exception as e:
        return jsonify({"error": f"Failed to parse file: {str(e)}"}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@app.route("/api/edit", methods=["POST"])
def edit():
    """
    Accept a natural language instruction (or pre-resolved force_commands),
    apply edits to the loaded project.

    Body options:
      { "instruction": "..." }
        — runs through LLM then disambiguation check then applies

      { "instruction": "...", "force_commands": [...] }
        — skips LLM, applies the supplied commands directly
          (used after user resolves disambiguation)

      { "instruction": "...", "apply_to_all": true }
        — re-runs the LLM command with apply_to_all=true injected on all cmds
    """
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded. Upload an XER or XML file first."}), 400

    data = request.get_json()
    if not data or not data.get("instruction"):
        return jsonify({"error": "instruction is required"}), 400

    instruction = data["instruction"].strip()
    project = _session["project"]
    force_commands = data.get("force_commands")   # pre-resolved by user
    raw_llm = ""

    try:
        if force_commands is not None:
            # User resolved disambiguation — apply directly
            commands = force_commands
        else:
            # Step 1: LLM → JSON commands
            commands, raw_llm = interpret(
                instruction,
                project_summary=project.summary(),
                model_key=_session["model_key"],
                api_key=_session["api_key"],
            )

            if commands and commands[0].get("action") == "error":
                return jsonify({
                    "success": False,
                    "error": commands[0].get("message", "Could not interpret instruction"),
                    "commands": commands,
                    "raw_llm": raw_llm,
                })

            # Step 2: Pre-check for disambiguation
            ambig = check_disambiguation(project, commands)
            if ambig is not None:
                return jsonify({
                    "type": "disambiguation",
                    "instruction": instruction,
                    "commands": commands,
                    "command_index": ambig["command_index"],
                    "field": ambig["field"],
                    "search_term": ambig["search_term"],
                    "matches": ambig["matches"],
                    "raw_llm": raw_llm,
                })

        # Step 3: Snapshot for undo, then apply
        _push_undo(instruction)
        results = apply_commands(project, commands)

        applied = [(cmd, ok, msg) for (cmd, (ok, msg)) in zip(commands, results)]
        success_count = sum(1 for _, ok, _ in applied if ok)
        fail_count = len(applied) - success_count

        # If nothing succeeded, pop the undo snapshot — no state changed
        if success_count == 0:
            _session["undo_stack"].pop()

        _session["edit_history"].append({
            "instruction": instruction,
            "commands": commands,
            "results": [{"action": cmd.get("action"), "success": ok, "message": msg}
                        for cmd, ok, msg in applied],
        })

        return jsonify({
            "type": "result",
            "success": fail_count == 0,
            "commands_applied": success_count,
            "commands_failed": fail_count,
            "results": [
                {"action": cmd.get("action"), "success": ok, "message": msg}
                for cmd, ok, msg in applied
            ],
            "commands": commands,
            "project_summary": project.summary(),
            "undo_count": len(_session["undo_stack"]),
        })

    except Exception as e:
        return jsonify({"error": f"Edit failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/undo", methods=["POST"])
def undo():
    """Pop the last edit off the undo stack and restore project state."""
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400

    stack = _session["undo_stack"]
    if not stack:
        return jsonify({"error": "Nothing to undo"}), 400

    label, snapshot = stack.pop()

    # Also remove the matching edit_history entry
    if _session["edit_history"]:
        _session["edit_history"].pop()

    _session["project"] = snapshot

    project = snapshot
    return jsonify({
        "success": True,
        "undone_label": label,
        "undo_count": len(stack),
        "project_name": project.name,
        "activity_count": len(project.activities),
        "wbs_count": len(project.wbs_nodes),
        "relation_count": len(project.relations),
        "edit_count": len(_session["edit_history"]),
    })


@app.route("/api/report", methods=["GET"])
def schedule_report():
    """Return a schedule health / pre-export QC report."""
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400

    report = generate_schedule_report(_session["project"])
    return jsonify(report)


@app.route("/api/create", methods=["POST"])
def create_new_project():
    """
    Generate a brand-new P6-compatible project from a plain-English description.

    Body: { "description": "4-story office building, NTP through closeout..." }
    """
    data = request.get_json()
    if not data or not data.get("description"):
        return jsonify({"error": "description is required"}), 400

    description = data["description"].strip()

    try:
        project, raw_llm = create_project(
            description,
            model_key=_session["model_key"],
            api_key=_session["api_key"],
        )

        _session["project"] = project
        _session["source_name"] = f"{project.id}.xml"
        _session["source_path"] = None
        _session["edit_history"] = []
        _session["undo_stack"] = []

        return jsonify({
            "success": True,
            "project_name": project.name,
            "activity_count": len(project.activities),
            "wbs_count": len(project.wbs_nodes),
            "relation_count": len(project.relations),
            "data_date": project.data_date,
            "summary": project.summary(),
            "raw_llm": raw_llm,
        })

    except Exception as e:
        return jsonify({"error": f"Project creation failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/download", methods=["GET"])
def download():
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400

    project = _session["project"]
    stem = Path(_session.get("source_name", "schedule")).stem
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
    return jsonify({"history": _session["edit_history"]})


@app.route("/api/status", methods=["GET"])
def status():
    project = _session["project"]
    model_cfg = MODELS.get(_session["model_key"], {})
    if project is None:
        return jsonify({
            "loaded": False,
            "model_key": _session["model_key"],
            "model_label": model_cfg.get("label", _session["model_key"]),
            "api_key_set": bool(_session["api_key"]),
            "undo_count": 0,
        })
    return jsonify({
        "loaded": True,
        "project_name": project.name,
        "source_name": _session.get("source_name"),
        "activity_count": len(project.activities),
        "wbs_count": len(project.wbs_nodes),
        "relation_count": len(project.relations),
        "edit_count": len(_session["edit_history"]),
        "undo_count": len(_session["undo_stack"]),
        "model_key": _session["model_key"],
        "model_label": model_cfg.get("label", _session["model_key"]),
        "api_key_set": bool(_session["api_key"]),
    })


@app.route("/api/settings", methods=["GET"])
def get_settings():
    model_cfg = MODELS.get(_session["model_key"], {})
    return jsonify({
        "model_key": _session["model_key"],
        "model_label": model_cfg.get("label", _session["model_key"]),
        "api_key_set": bool(_session["api_key"]),
        "available_models": [
            {"key": k, "label": v["label"], "provider": v["provider"]}
            for k, v in MODELS.items()
        ],
    })


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json() or {}

    if "model_key" in data:
        key = data["model_key"]
        if key not in MODELS:
            return jsonify({"error": f"Unknown model '{key}'. Available: {list(MODELS.keys())}"}), 400
        _session["model_key"] = key

    if "api_key" in data:
        val = data["api_key"].strip() if data["api_key"] else ""
        _session["api_key"] = val if val else None

    model_cfg = MODELS.get(_session["model_key"], {})
    return jsonify({
        "success": True,
        "model_key": _session["model_key"],
        "model_label": model_cfg.get("label", _session["model_key"]),
        "api_key_set": bool(_session["api_key"]),
    })


@app.route("/api/clear", methods=["POST"])
def clear_session():
    _session["project"] = None
    _session["source_path"] = None
    _session["source_name"] = None
    _session["edit_history"] = []
    _session["undo_stack"] = []
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=False)
