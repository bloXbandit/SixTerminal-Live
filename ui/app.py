"""
app.py — Local Flask web server for Six Terminal Live desktop UI.

Runs on http://localhost:5100
Opened automatically by main.py on startup.
"""

import os
import sys
import json
import tempfile
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.xer_reader import load_xer
from engine.xml_writer import write_p6_xml
from engine.edit_engine import apply_commands
from interpreter.llm_interpreter import interpret

app = Flask(__name__, static_folder="static", template_folder="templates")

# In-memory session state (single-user local tool)
_session = {
    "project": None,
    "source_path": None,
    "source_name": None,
    "edit_history": [],  # list of (instruction, commands, results)
}


@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/api/upload", methods=["POST"])
def upload_file():
    """Accept an XER or P6 XML file and load it into the session."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    filename = f.filename or "schedule"
    ext = Path(filename).suffix.lower()

    if ext not in (".xer", ".xml"):
        return jsonify({"error": f"Unsupported file type '{ext}'. Upload an XER or P6 XML file."}), 400

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    f.save(tmp.name)
    tmp.close()

    try:
        if ext == ".xer":
            project = load_xer(tmp.name)
        else:
            # XML reader — placeholder until xml_reader.py is built
            # For now, return a clear message
            return jsonify({"error": "P6 XML import coming soon. Please use XER for now."}), 400

        _session["project"] = project
        _session["source_path"] = tmp.name
        _session["source_name"] = filename
        _session["edit_history"] = []

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
    """Accept a natural language instruction, interpret it, and apply edits."""
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded. Upload an XER or XML file first."}), 400

    data = request.get_json()
    if not data or not data.get("instruction"):
        return jsonify({"error": "instruction is required"}), 400

    instruction = data["instruction"].strip()
    project = _session["project"]

    try:
        # Step 1: Interpret natural language → JSON commands
        commands, raw_llm = interpret(instruction, project_summary=project.summary())

        # Check for error command
        if commands and commands[0].get("action") == "error":
            return jsonify({
                "success": False,
                "error": commands[0].get("message", "Could not interpret instruction"),
                "commands": commands,
                "raw_llm": raw_llm,
            })

        # Step 2: Apply commands to project
        results = apply_commands(project, commands)

        # Summarize results
        applied = [(cmd, ok, msg) for (cmd, (ok, msg)) in zip(commands, results)]
        success_count = sum(1 for _, ok, _ in applied if ok)
        fail_count = len(applied) - success_count

        # Record in history
        _session["edit_history"].append({
            "instruction": instruction,
            "commands": commands,
            "results": [{"action": cmd.get("action"), "success": ok, "message": msg}
                        for cmd, ok, msg in applied],
        })

        return jsonify({
            "success": fail_count == 0,
            "commands_applied": success_count,
            "commands_failed": fail_count,
            "results": [
                {"action": cmd.get("action"), "success": ok, "message": msg}
                for cmd, ok, msg in applied
            ],
            "commands": commands,
            "project_summary": project.summary(),
        })

    except Exception as e:
        return jsonify({"error": f"Edit failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/download", methods=["GET"])
def download():
    """Export the current project state as P6 XML."""
    if _session["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400

    project = _session["project"]
    source_name = _session.get("source_name", "schedule")
    stem = Path(source_name).stem
    output_name = f"{stem}_edited.xml"

    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()

    try:
        write_p6_xml(project, tmp.name)
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name=output_name,
            mimetype="application/xml",
        )
    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@app.route("/api/history", methods=["GET"])
def history():
    """Return the edit history for the current session."""
    return jsonify({"history": _session["edit_history"]})


@app.route("/api/status", methods=["GET"])
def status():
    """Return current session status."""
    project = _session["project"]
    if project is None:
        return jsonify({"loaded": False})
    return jsonify({
        "loaded": True,
        "project_name": project.name,
        "source_name": _session.get("source_name"),
        "activity_count": len(project.activities),
        "wbs_count": len(project.wbs_nodes),
        "relation_count": len(project.relations),
        "edit_count": len(_session["edit_history"]),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=False)
