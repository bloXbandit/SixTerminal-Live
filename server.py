"""
server.py — Local Flask web server for Six Terminal Live.

Lives at the project root so Python can import it directly from main.py
without descending into subdirectories (avoids importlib permission issues
on systems with restrictive Controlled Folder Access policies).

Runs on http://localhost:5100
"""

import os
import re
import sys
import gzip
import copy
import json
import uuid
import time
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
from engine.importer import extract as import_extract, build_project_from_contract, \
    _pdf_page_count, _read_pdf_pages, _rows_to_contract, _text_layer_present, \
    open_pdf_handle, _read_pdf_pages_from_handle, _text_layer_from_handle
from engine.compare import (compare_projects, copy_wbs_branch,
                            replace_wbs_branch, apply_activity_changes)
from engine import cloud_store
from engine.logic_advisor import (milestone_report, milestone_drivers,
                                  commissioning_ladder, to_commands, find_wbs,
                                  area_digest, area_report, procurement_report,
                                  sequence_recommendations)
from engine import logic_advisor as _advisor

TEMPLATE_DIR = str(ROOT / "ui" / "templates")
STATIC_DIR   = str(ROOT / "ui" / "static")


# ── Enriched LLM context (lives here to avoid touching engine/ subdirectory) ──

def build_llm_context(project, max_activities: int = 3000) -> str:
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

def _snapshot_project(project):
    """
    A cheap undo snapshot.

    Every Activity / WBSNode / Relation / Calendar field is an immutable scalar
    (str, float, bool) or an immutable frozenset, so a shallow copy.copy of each
    object is a fully independent copy: a later `a.field = x` on the live project
    reassigns the field on a *different* object and never touches the snapshot.
    This avoids copy.deepcopy's recursion + memo, which cost ~0.9s at 15k rows.
    """
    import copy as _copy
    from engine.schedule_model import Project
    snap = Project(
        uid=project.uid, name=project.name, id=project.id,
        data_date=project.data_date, planned_start=project.planned_start,
        must_finish_by=project.must_finish_by, status_code=project.status_code,
        calendars=[_copy.copy(c) for c in project.calendars],
        wbs_nodes=[_copy.copy(w) for w in project.wbs_nodes],
        activities=[_copy.copy(a) for a in project.activities],
        relations=[_copy.copy(r) for r in project.relations],
    )
    snap.build_lookups()
    return snap


def _push_undo(label: str):
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return
    stack = sess["undo_stack"]
    stack.append((label, _snapshot_project(sess["project"])))
    if len(stack) > _MAX_UNDO:
        stack.pop(0)
    _mark_dirty(_active_id[0])


# ── Cloud persistence (Cloudflare R2, optional) ────────────────────────────────
_dirty_pids: set = set()          # projects edited since the last cloud flush


def _mark_dirty(pid):
    """Queue a project to be saved to cloud after the request finishes."""
    if pid and cloud_store.is_configured():
        _dirty_pids.add(pid)


def _project_to_xml_bytes(project) -> bytes:
    """Serialize a project to P6 XML bytes using the existing writer."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()
    try:
        write_p6_xml(project, tmp.name)
        with open(tmp.name, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _persist(pid):
    """Save one project to cloud now. Non-fatal on failure."""
    sess = _projects.get(pid)
    if not sess or not sess["project"]:
        return False, "no project"
    try:
        xml = _project_to_xml_bytes(sess["project"])
        return cloud_store.save(pid, xml, {
            "source_name": sess.get("source_name"),
            "project_name": sess["project"].name,
            "activity_count": len(sess["project"].activities),
        })
    except Exception as e:
        return False, f"serialize failed: {e}"


# ── Response compression ──────────────────────────────────────────────────────
# The schedule payload for a large project runs to well over a megabyte of
# JSON, which gzips by roughly 10x. That matters most on a corporate network:
# a TLS-inspecting proxy buffers and scans the whole body before the browser
# sees any of it, so payload size turns directly into waiting.
#
# Done by hand rather than with a dependency — it is a dozen lines, and the
# fewer packages the deploy has to install, the fewer ways it can fail.
_COMPRESSIBLE = ("application/json", "text/html", "text/css",
                 "application/javascript", "text/javascript", "text/plain",
                 "application/xml", "text/xml", "image/svg+xml")
_COMPRESS_MIN_BYTES = 1024      # below this the header overhead is not worth it


@app.after_request
def _compress(resp):
    try:
        if resp.direct_passthrough or resp.status_code >= 300:
            return resp
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return resp
        if resp.headers.get("Content-Encoding"):
            return resp
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in _COMPRESSIBLE:
            return resp
        body = resp.get_data()
        if len(body) < _COMPRESS_MIN_BYTES:
            return resp
        packed = gzip.compress(body, compresslevel=6)
        if len(packed) >= len(body):        # already-compressed content
            return resp
        resp.set_data(packed)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(packed))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass    # a compression failure must never cost the user their response
    return resp


@app.after_request
def _flush_dirty_to_cloud(resp):
    """Autosave: after any request, persist projects that were edited."""
    if not _dirty_pids:
        return resp
    pids = list(_dirty_pids)
    _dirty_pids.clear()
    for pid in pids:
        try:
            _persist(pid)
        except Exception:
            pass  # never let a cloud save break the response
    return resp


def _restore_from_cloud():
    """On startup, load every schedule stored in R2 back into memory."""
    if not cloud_store.is_configured():
        return
    try:
        items = cloud_store.load_all()
    except Exception:
        return
    for it in items:
        pid = it["pid"]
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
            tmp.write(it["xml_bytes"])
            tmp.close()
            project = load_xml(tmp.name)
            os.unlink(tmp.name)
        except Exception:
            continue
        meta = it.get("meta") or {}
        sess = _make_session(pid, meta.get("source_name") or f"{pid}.xml")
        sess["project"] = project
        _projects[pid] = sess
        if _active_id[0] is None:
            _active_id[0] = pid


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
    # The whole app is inline in this one file. Without an explicit
    # Cache-Control, browsers cache it heuristically and keep running stale
    # JavaScript long after a deploy — no-cache forces a revalidation (a cheap
    # 304 when nothing changed) so fixes actually reach the user.
    resp = send_from_directory(app.template_folder, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    # File responses stream in passthrough mode, which skips compression. The
    # whole app is this one 300 KB file, so it is worth reading into memory to
    # let the gzip hook see it.
    resp.direct_passthrough = False
    return resp


@app.route("/healthz")
def healthz():
    """
    Liveness probe for uptime pingers (keeps the Render instance from idling).
    Deliberately touches no session state and renders no template, so it stays
    cheap even when hit every few minutes all day.
    """
    return "ok", 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}


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
        # Work out the float, critical path and early / late dates the loaded
        # network implies, WITHOUT touching Start / Finish — the file's dates
        # are shown exactly as P6 exported them. Doing this at load means the
        # drift badge is honest from the first screen: a schedule whose stored
        # dates already disagree with its own logic says so straight away,
        # instead of appearing clean until the first unrelated edit.
        try:
            from engine.schedule_model import compute_dates as _cd
            _cd(project, apply_dates=False)
        except Exception:
            pass
        pid = _unique_pid(Path(filename).stem)
        sess = _make_session(pid, filename)
        sess["project"]     = project
        sess["source_path"] = tmp.name
        _projects[pid]      = sess
        _active_id[0]       = pid
        _mark_dirty(pid)

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


# ── Chunked PDF extraction (avoids gateway timeouts on 20+ page PDFs) ──────────
_extract_sessions: dict = {}   # extract_id -> {path, rows, total_pages, engine, filename}
_CHUNK_SIZE = 3                # pages per request — tuned for Render free tier (~30s gateway timeout)


def _cleanup_extract_session(extract_id: str):
    """Close pdfplumber handle, delete temp file, and remove session."""
    sess = _extract_sessions.pop(extract_id, None)
    if not sess:
        return
    # Close the pdfplumber handle if open
    try:
        h = sess.get("pdf_handle")
        if h:
            h.close()
    except Exception:
        pass
    # Delete the temp file
    try:
        os.unlink(sess["path"])
    except Exception:
        pass


@app.route("/api/import/extract-start", methods=["POST"])
def import_extract_start():
    """Upload a PDF, get page count + extract_id. Does NOT extract yet.
    Kept as fast as possible: save file + count pages only. Text layer
    check is deferred to the first chunk to avoid opening the PDF twice."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    filename = f.filename or "schedule"
    ext = Path(filename).suffix.lower()
    if ext != ".pdf":
        return jsonify({"error": "Chunked extraction is for PDFs only. "
                                 "Use /api/import/extract for Excel."}), 400

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.save(tmp.name)
    tmp.close()

    # Fast page count via pypdf (no pdfplumber overhead)
    total = _pdf_page_count(tmp.name)
    if total == 0:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return jsonify({"error": "Could not read PDF — file may be corrupted or password-protected."}), 400

    extract_id = f"ext_{os.path.basename(tmp.name)}"
    _extract_sessions[extract_id] = {
        "path": tmp.name,
        "rows": [],
        "total_pages": total,
        "engine": "pdfplumber",
        "filename": filename,
        "text_checked": False,     # text layer check deferred to first chunk
        "created_at": time.time(),
    }
    return jsonify({
        "success": True,
        "extract_id": extract_id,
        "total_pages": total,
        "chunk_size": _CHUNK_SIZE,
        "filename": filename,
    })


@app.route("/api/import/extract-chunk", methods=["POST"])
def import_extract_chunk():
    """Process one chunk of pages and accumulate rows server-side.
    Uses a persistent pdfplumber handle stored in the session — avoids
    re-parsing the entire PDF on every chunk (the main cause of OOM kills
    and gateway timeouts on large PDFs)."""
    data = request.get_json() or {}
    extract_id = data.get("extract_id")
    if not extract_id or extract_id not in _extract_sessions:
        return jsonify({"error": "Invalid or expired extract_id"}), 400

    sess = _extract_sessions[extract_id]
    page_start = data.get("page_start", 0)
    page_end = data.get("page_end", page_start + _CHUNK_SIZE)

    # On the first chunk, open the pdfplumber handle and check text layer.
    # The handle stays open across all subsequent chunks — this is the key
    # optimization: we parse the PDF structure once, not N times.
    if page_start == 0 and not sess.get("text_checked"):
        sess["text_checked"] = True
        try:
            sess["pdf_handle"] = open_pdf_handle(sess["path"])
        except Exception as e:
            _cleanup_extract_session(extract_id)
            return jsonify({"error": f"Failed to open PDF: {str(e)}"}), 500
        if not _text_layer_from_handle(sess["pdf_handle"]):
            _cleanup_extract_session(extract_id)
            return jsonify({"error": "This PDF has no text layer (looks scanned/photographed). "
                                     "Chunked extraction only works on digital PDFs with a text layer. "
                                     "Enable AI assist on the regular import, or export to Excel.",
                            "scanned": True}), 400

    try:
        pdf = sess.get("pdf_handle")
        if pdf is None:
            # Handle was lost (server restart?) — re-open as fallback
            sess["pdf_handle"] = open_pdf_handle(sess["path"])
            pdf = sess["pdf_handle"]
        chunk_rows = _read_pdf_pages_from_handle(pdf, page_start, page_end)
        sess["rows"].extend(chunk_rows)
        pages_done = page_end
        return jsonify({
            "success": True,
            "pages_done": pages_done,
            "total_pages": sess["total_pages"],
            "rows_in_chunk": len(chunk_rows),
            "total_rows": len(sess["rows"]),
        })
    except Exception as e:
        return jsonify({"error": f"Chunk extraction failed: {str(e)}"}), 500


@app.route("/api/import/extract-finish", methods=["POST"])
def import_extract_finish():
    """Assemble accumulated rows into a contract, clean up temp file."""
    data = request.get_json() or {}
    extract_id = data.get("extract_id")
    if not extract_id or extract_id not in _extract_sessions:
        return jsonify({"error": "Invalid or expired extract_id"}), 400

    sess = _extract_sessions[extract_id]
    try:
        rows = sess["rows"]
        meta = {
            "source_name": sess["filename"],
            "project_name": Path(sess["filename"]).stem,
            "engine": sess["engine"],
            "file_type": "pdf",
            "warnings": [],
        }
        contract = _rows_to_contract(rows, meta)
        contract["meta"]["source_name"] = sess["filename"]
        contract["meta"]["project_name"] = Path(sess["filename"]).stem
        return jsonify({"success": True, "contract": contract})
    except Exception as e:
        return jsonify({"error": f"Contract assembly failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500
    finally:
        _cleanup_extract_session(extract_id)


@app.route("/api/import/extract-cancel", methods=["POST"])
def import_extract_cancel():
    """Cancel a chunked extraction and clean up the temp file + handle."""
    data = request.get_json() or {}
    extract_id = data.get("extract_id")
    if extract_id and extract_id in _extract_sessions:
        _cleanup_extract_session(extract_id)
    return jsonify({"success": True})


@app.route("/api/import/paste", methods=["POST"])
def import_paste_route():
    """
    Parse a block of text copied out of a PDF (or Excel) into the same review
    contract the file importers produce, then hand it to /api/import/commit.

    This is the fast lane for big schedules: no upload, no PDF engine, no OCR —
    a few KB of text parsed in milliseconds, so it can't hit the request limits
    that kill a 27-page PDF on a small host.
    Body: {"text": "...", "project_name": "..."}
    """
    data = request.get_json() or {}
    text = data.get("text") or ""
    if not text.strip():
        return jsonify({"error": "No text was pasted"}), 400
    try:
        from engine.paste_parser import contract_from_paste
        contract = contract_from_paste(
            text, project_name=data.get("project_name") or "Pasted activities")
        return jsonify({"success": True, "contract": contract})
    except Exception as e:
        return jsonify({"error": f"Could not read the pasted text: {e}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/import/commit", methods=["POST"])
def import_commit_route():
    """Materialize a reviewed extraction contract and load it as the active schedule."""
    data = request.get_json() or {}
    contract = data.get("contract")
    if not contract or not isinstance(contract, dict):
        return jsonify({"error": "contract is required"}), 400
    mode = (data.get("mode") or "replace").lower()   # replace | merge
    name = data.get("project_name")
    # Optional placement for a merge: land the block under an existing folder,
    # or under a brand-new one created on the spot. The grid sends the uid —
    # codes repeat in real schedules (P6 short names are only unique among
    # siblings), so code lookup can resolve to the wrong same-code folder.
    target_wbs_uid  = data.get("target_wbs_uid")      # exact folder (preferred)
    target_wbs_code = data.get("target_wbs_code")     # legacy fallback
    new_wbs_name    = data.get("new_wbs_name")        # create this folder first
    if name:
        contract.setdefault("meta", {})["project_name"] = name

    try:
        project = build_project_from_contract(contract)
        if mode == "merge" and _get_session() and _get_session()["project"]:
            # append imported WBS + activities into the current active project
            base = _get_session()["project"]
            _push_undo(f"Import (merge) {contract['meta'].get('source_name','file')}")

            target_uid = None
            if target_wbs_uid:
                tw = next((w for w in base.wbs_nodes if w.uid == str(target_wbs_uid)), None)
                if not tw:
                    return jsonify({"error": "The folder picked as the destination no longer "
                                             "exists — reopen the paste dialog and pick again"}), 400
                target_uid = tw.uid
            elif target_wbs_code:
                tw = next((w for w in base.wbs_nodes
                           if w.code.lower() == str(target_wbs_code).lower()), None)
                if not tw:
                    return jsonify({"error": f"Target WBS '{target_wbs_code}' not found"}), 400
                target_uid = tw.uid
            if new_wbs_name:
                from engine.schedule_model import WBSNode
                code = re.sub(r"[^A-Za-z0-9]", "", new_wbs_name).upper()[:12] or "PASTED"
                n = 1
                existing = {w.code for w in base.wbs_nodes}
                base_code = code
                while code in existing:
                    n += 1
                    code = f"{base_code}{n}"
                node = WBSNode(uid=str(uuid.uuid4().int)[:10], name=new_wbs_name,
                               code=code, parent_uid=target_uid,
                               sequence_num=len(base.wbs_nodes) * 10)
                base.wbs_nodes.append(node)
                target_uid = node.uid

            # dedupe: what to do when a pasted/imported activity's name matches
            # one already in the SAME destination folder. Default "off" keeps
            # exact prior behaviour for any caller that doesn't pass it.
            dedupe = data.get("dedupe") or None
            if dedupe not in (None, "off", "skip", "replace"):
                return jsonify({"error": f"dedupe must be 'skip', 'replace', or 'off' — got '{dedupe}'"}), 400
            merge_report = _merge_projects(base, project, target_wbs_uid=target_uid,
                                           flatten=bool(data.get("flatten")),
                                           dedupe=None if dedupe == "off" else dedupe)
            base.build_lookups()
            from engine.schedule_model import compute_dates
            try:
                # Merging rows in must not reflow the schedule they land in.
                # The incoming rows carry their own dates and any that arrive
                # without one are seeded; everything already there is left
                # exactly as it was.
                compute_dates(base, apply_dates=False)
            except Exception:
                pass
            project = base
            pid = _active_id[0]
        else:
            merge_report = None
            pid = _unique_pid(project.id or Path(str(name or "import")).stem or "import")
            sess = _make_session(pid, contract["meta"].get("source_name", f"{pid}.xlsx"))
            sess["project"] = project
            _projects[pid] = sess
            _active_id[0] = pid
        _mark_dirty(pid)

        return jsonify({
            "success": True, "project_id": pid, "project_name": project.name,
            "activity_count": len(project.activities), "wbs_count": len(project.wbs_nodes),
            "relation_count": len(project.relations), "data_date": project.data_date,
            "logic_status": contract["meta"].get("logic_status", "absent"),
            "summary": project.summary(),
            "merge_report": merge_report,
            "projects": [_project_list_item(k) for k in _projects],
        })
    except Exception as e:
        return jsonify({"error": f"Import failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/calendar", methods=["GET", "POST"])
def project_calendar():
    """
    Get or set the schedule's working calendar.

    Defaults stay 5-DAY NO HOLIDAY so nothing shifts unless the user opts in.
    Setting a calendar re-runs CPM, and is undoable like any other edit.
    """
    from engine.calendars import (CALENDAR_PRESETS, DEFAULT_CALENDAR_NAME,
                                  preset_for, holiday_dates)
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    project = sess["project"]

    if request.method == "GET":
        # An imported calendar ("Standard", "P5-DAY NO HOL", ...) is reported as
        # the preset it behaves like, so the picker shows a real selection.
        current = DEFAULT_CALENDAR_NAME
        if project.calendars:
            cal = project.calendars[0]
            if cal.name in CALENDAR_PRESETS:
                current = cal.name
            else:
                wd  = frozenset(getattr(cal, "work_days", None) or {0, 1, 2, 3, 4})
                hol = bool(getattr(cal, "holidays", None))
                for nm, (pw, ph) in CALENDAR_PRESETS.items():
                    if frozenset(pw) == wd and ph == hol:
                        current = nm
                        break
        return jsonify({"current": current, "options": list(CALENDAR_PRESETS.keys())})

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if name not in CALENDAR_PRESETS:
        return jsonify({"error": f"Unknown calendar '{name}'. "
                                 f"Options: {', '.join(CALENDAR_PRESETS)}"}), 400
    try:
        from engine.schedule_model import Calendar, compute_dates
        work_days, honors_holidays = preset_for(name)
        hols = frozenset(holiday_dates()) if honors_holidays else frozenset()

        _push_undo(f"Calendar → {name}")
        if not project.calendars:
            project.calendars = [Calendar(uid="1", name=name)]
        cal = project.calendars[0]
        cal.name, cal.work_days, cal.holidays = name, work_days, hols
        # every activity follows the project calendar unless it has its own
        for a in project.activities:
            if not a.calendar_uid:
                a.calendar_uid = cal.uid
        project.build_lookups()
        try:
            # Refresh the derived columns against the new working pattern, but
            # leave Start / Finish alone — changing the calendar is an edit,
            # and edits do not reschedule. The user runs Schedule to reflow.
            compute_dates(project, apply_dates=False)
        except Exception:
            pass

        finishes = [str(a.early_finish or a.planned_finish)[:10]
                    for a in project.activities if (a.early_finish or a.planned_finish)]
        return jsonify({
            "success": True, "calendar": name,
            "work_days": sorted(work_days), "holiday_count": len(hols),
            "project_finish": max(finishes) if finishes else None,
            "undo_count": len(sess["undo_stack"]), "redo_count": len(sess["redo_stack"]),
        })
    except Exception as e:
        return jsonify({"error": f"Calendar change failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/schedule/run", methods=["POST"])
def run_schedule():
    """
    Run the CPM forward/backward pass on demand (the P6 'F9 / Schedule' action)
    and optionally re-order activities into the sequence they actually run.

    Pushes an undo snapshot first, so a schedule run is always revertable.
    """
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400

    from datetime import date as _date
    from engine.schedule_model import compute_dates

    data      = request.get_json() or {}
    reorder   = data.get("reorder", True)
    override  = data.get("data_date")
    project   = sess["project"]

    try:
        _push_undo("Schedule (CPM)")

        # CPM needs an origin. Prefer an explicit data date, then the project
        # start, then the earliest date already on the schedule, then today.
        if override:
            project.data_date = override
        if not (project.planned_start or project.data_date):
            known = [str(a.actual_start or a.planned_start)[:10]
                     for a in project.activities if (a.actual_start or a.planned_start)]
            project.data_date = min(known) if known else _date.today().isoformat()
        if not project.planned_start:
            project.planned_start = str(project.data_date)[:10]

        # Explicit F9 is the one full reflow: unlinked activities are driven
        # from the data date (the confirm dialog warned about exactly this),
        # and this is the only path that rewrites Start / Finish. Every
        # implicit recompute holds both.
        compute_dates(project, hold_unlinked_dates=False, apply_dates=True)

        if reorder:
            # Sequence the rows the way the work runs: earliest start first,
            # ties broken by finish then activity id for a stable order.
            def _key(a):
                s = a.actual_start or a.early_start or a.planned_start or "9999-12-31"
                f = a.early_finish or a.planned_finish or "9999-12-31"
                return (str(s)[:10], str(f)[:10], a.activity_id)
            project.activities.sort(key=_key)

        project.build_lookups()

        finishes = [str(a.early_finish or a.planned_finish)[:10]
                    for a in project.activities if (a.early_finish or a.planned_finish)]
        project_finish = max(finishes) if finishes else None
        critical = [a.activity_id for a in project.activities if a.is_critical]

        # Logic coverage — CPM is only meaningful if activities are linked
        linked = set()
        for r in project.relations:
            linked.add(r.predecessor_uid); linked.add(r.successor_uid)
        unlinked = [a.activity_id for a in project.activities if a.uid not in linked]

        return jsonify({
            "success": True,
            "data_date": str(project.data_date)[:10] if project.data_date else None,
            "project_finish": project_finish,
            "activity_count": len(project.activities),
            "relation_count": len(project.relations),
            "critical_count": len(critical),
            "unlinked_count": len(unlinked),
            "reordered": bool(reorder),
            "undo_count": len(sess["undo_stack"]),
            "redo_count": len(sess["redo_stack"]),
        })
    except Exception as e:
        return jsonify({"error": f"Schedule run failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


def _norm_activity_name(s):
    """Collapse whitespace + casefold, so paste noise doesn't hide a real dup."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _merge_projects(base, incoming, target_wbs_uid=None, flatten=False, dedupe=None):
    """
    Append incoming WBS nodes + activities into base, de-duplicating IDs.

    When target_wbs_uid is given, the incoming branch is nested under that
    folder: any incoming node that has no parent of its own (a root of the
    pasted/imported block) is re-parented to the target, and activities that
    never resolved to a folder land directly in it. That is what lets a pasted
    block drop into an existing section instead of piling up at the root.

    dedupe controls what happens when an incoming activity's NAME matches one
    already sitting in the SAME destination WBS folder. The same name in a
    DIFFERENT folder is not a conflict — "Terminate wire" can exist in ER 209
    and ER 210 without issue.
      None / "off" — always add as a new activity (previous behaviour)
      "skip"       — keep what's already there; the incoming row is dropped
      "replace"    — the incoming row's data overwrites the existing activity
                     (name/duration/dates/status/%/type/constraint — the same
                     field list apply_activity_changes uses)
    Either way, any relationship that touched the incoming (now-skipped or
    -replaced) activity is re-pointed at the kept one, so pasted logic never
    silently dangles. Returns a report dict for the caller to surface.
    """
    from engine.schedule_model import Relation
    from engine.compare import ACTIVITY_DATA_FIELDS

    report = {"added": 0, "skipped_duplicate": 0, "replaced": 0, "relations_added": 0,
             "skipped_names": [], "replaced_names": []}
    _REPORT_CAP = 30    # keep the response small on a huge merge

    # (wbs_uid, normalized name) -> Activity already sitting in base, extended
    # as we place each incoming row so duplicates WITHIN this same paste are
    # caught too, not just against what was already there before it started.
    dup_index = {}
    for a in base.activities:
        dup_index.setdefault((a.wbs_uid, _norm_activity_name(a.name)), a)

    act_uid_map = {}                # every incoming uid -> uid actually used in base
    existing_ids = {a.activity_id for a in base.activities}

    def place(a, final_wbs_uid):
        key = (final_wbs_uid, _norm_activity_name(a.name))
        dup = dup_index.get(key) if dedupe in ("skip", "replace") else None
        if dup is not None:
            act_uid_map[a.uid] = dup.uid
            if dedupe == "replace":
                for attr in ACTIVITY_DATA_FIELDS:
                    setattr(dup, attr, getattr(a, attr))
                report["replaced"] += 1
                if len(report["replaced_names"]) < _REPORT_CAP:
                    report["replaced_names"].append(a.name)
            else:
                report["skipped_duplicate"] += 1
                if len(report["skipped_names"]) < _REPORT_CAP:
                    report["skipped_names"].append(a.name)
            return
        aid = a.activity_id
        while aid in existing_ids:
            aid += "-i"
        a.activity_id = aid
        existing_ids.add(aid)
        a.wbs_uid = final_wbs_uid
        base.activities.append(a)
        act_uid_map[a.uid] = a.uid
        dup_index[key] = a
        report["added"] += 1

    # A paste/import with no real section headers gets exactly one synthetic
    # placeholder folder from build_project_from_contract (code "ROOT", named
    # after the project). There is no real structure there to "keep" — without
    # this check, a plain few-line paste ("just these 3 more activities") would
    # land in a pointless new sub-folder nested under the target instead of the
    # folder the user actually picked, which also means dedupe could never see
    # what's already there. A GENUINE single named section from an indented
    # paste ("Electrical" with two activities under it) does not match this and
    # is still placed as its own folder, same as before.
    only_synthetic_root = (
        len(incoming.wbs_nodes) == 1
        and incoming.wbs_nodes[0].code == "ROOT"
        and incoming.wbs_nodes[0].name == incoming.name
        and incoming.wbs_nodes[0].parent_uid is None
    )

    # flatten: drop the incoming folder structure entirely and drop every
    # activity straight into the target. This is what "load these under
    # Electrical" means — without it, folders inferred from an id prefix
    # (MDC1 > MDC1.FDG) get rebuilt inside the target as noise.
    if (flatten or only_synthetic_root) and target_wbs_uid:
        for a in incoming.activities:
            place(a, target_wbs_uid)
    else:
        by_uid_incoming = {w.uid for w in incoming.wbs_nodes}

        # Reusing an existing folder (so re-pasting the same block twice doesn't
        # duplicate its sections) must respect the chosen destination. Matching
        # by code against the WHOLE schedule hijacked the target: pasting a
        # section called "Sitework" into Phase 2 landed in some other Sitework
        # at the root, because their name-derived codes collided. With a target
        # picked, only folders already under that target are reuse candidates;
        # codes also repeat freely in real schedules (P6 short names are only
        # unique among siblings), so all matches are kept, not just the first.
        code_matches = {}
        for w in base.wbs_nodes:
            code_matches.setdefault(w.code, []).append(w.uid)

        under_target = None
        if target_wbs_uid:
            kids = {}
            for w in base.wbs_nodes:
                kids.setdefault(w.parent_uid, []).append(w.uid)
            under_target = {target_wbs_uid}
            stack = [target_wbs_uid]
            while stack:
                for cuid in kids.get(stack.pop(), []):
                    if cuid not in under_target:
                        under_target.add(cuid)
                        stack.append(cuid)

        remap = {}                  # incoming wbs uid -> wbs uid actually used in base
        for w in incoming.wbs_nodes:
            cands = code_matches.get(w.code, [])
            reuse = (cands[0] if cands and under_target is None
                     else next((u for u in cands if u in under_target), None) if cands
                     else None)
            if reuse:
                remap[w.uid] = reuse
                continue
            if w.parent_uid in remap:
                # keep nesting intact when this folder's parent was reused —
                # its parent_uid must point at the base folder, not the
                # incoming uid that never joins the project
                w.parent_uid = remap[w.parent_uid]
            elif target_wbs_uid and (not w.parent_uid or w.parent_uid not in by_uid_incoming):
                # a root of the incoming block hangs off the target folder
                w.parent_uid = target_wbs_uid
            base.wbs_nodes.append(w)
            code_matches.setdefault(w.code, []).append(w.uid)
            if under_target is not None:
                under_target.add(w.uid)
            remap[w.uid] = w.uid

        base_uids = {w.uid for w in base.wbs_nodes}
        for a in incoming.activities:
            final_wbs = remap.get(a.wbs_uid, a.wbs_uid)
            if final_wbs not in base_uids:
                final_wbs = target_wbs_uid or (base.wbs_nodes[0].uid if base.wbs_nodes else a.wbs_uid)
            place(a, final_wbs)

    # Remap relations through the uid map — covers every incoming activity,
    # whether newly added, skipped, or replaced — and skip one that already
    # exists between the same two final endpoints, so re-pasting the same
    # block twice doesn't pile up duplicate links.
    existing_rel_keys = {(r.predecessor_uid, r.successor_uid, r.type) for r in base.relations}
    for r in incoming.relations:
        p = act_uid_map.get(r.predecessor_uid)
        s = act_uid_map.get(r.successor_uid)
        if not p or not s or p == s:
            continue
        key = (p, s, r.type)
        if key in existing_rel_keys:
            continue
        base.relations.append(Relation(uid=str(uuid.uuid4().int)[:10],
                                       predecessor_uid=p, successor_uid=s,
                                       type=r.type, lag=r.lag))
        existing_rel_keys.add(key)
        report["relations_added"] += 1

    return report


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

    # "What should X connect to?" is answered from the schedule itself, not by
    # the model: the candidates are scored, ranked and returned as buttons the
    # user clicks. Nothing can be invented on this path, and it costs no round
    # trip. Only a QUESTION of that shape takes it — "link A to B" is an
    # instruction and goes to the interpreter as usual.
    if force_commands is None and _advisor.tie_question(instruction):
        matches = _advisor.find_activity_in(project, instruction)
        if len(matches) == 1:
            opts = _advisor.tie_options(project, matches[0])
            if opts["predecessors"] or opts["successors"]:
                _append_chat("assistant",
                             f"Options for {opts['activity_id']} — {opts['name']}")
                return jsonify({"type": "tie_options", "instruction": instruction,
                                **opts})

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
    sess["redo_stack"].append((label, _snapshot_project(sess["project"])))
    sess["last_undone"] = label
    sess["project"] = snapshot
    project = snapshot
    _mark_dirty(_active_id[0])
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
    return _apply_direct(commands, label)


def _apply_direct(commands, label):
    """
    Run edit commands through the engine, undo stack and CPM recompute.

    Shared by /api/direct and any other route that produces commands (the logic
    advisor), so accepted recommendations are undoable and recorded in
    edit_history exactly like a hand edit.
    """
    sess = _get_session()
    project = sess["project"]
    # Every edit is diffed, structural ones included. Rebuilding the grid means
    # refetching well over a megabyte and recreating ~55,000 DOM nodes, which is
    # why adding a row used to feel heavier than editing one — so the response
    # carries what actually changed and the client patches it.
    #
    # A reload is still needed when the SHAPE of the tree changes (folders added,
    # removed or re-parented), because row placement, indentation and the folder
    # headers all depend on it. That is reported separately from row edits.
    tree_changing = any((c.get("action") or "").lower() in _TREE_ACTIONS
                        for c in commands)
    try:
        before = _flat_rows(project)
        before_order = list(before.keys())
        before_wbs = None if tree_changing else _wbs_signature(project)

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

        changed_rows, added_rows, removed_ids = None, None, None
        structural = tree_changing
        if success_count:
            after = _flat_rows(project)
            added_ids = [k for k in after if k not in before]
            removed_ids = [k for k in before if k not in after]
            changed_rows = [row for aid, row in after.items()
                            if aid in before and row != before[aid]]
            added_rows = [after[k] for k in added_ids]
            # Row ORDER matters (the Schedule button re-sequences the grid) and
            # so does the folder tree; either shifting means a patch cannot
            # reproduce the result, so fall back to a reload.
            if not structural:
                kept_before = [k for k in before_order if k in after]
                kept_after = [k for k in after if k in before]
                if kept_before != kept_after or _wbs_signature(project) != before_wbs:
                    structural = True
        if structural:
            changed_rows = added_rows = removed_ids = None

        return jsonify({
            "type":             "result",
            "success":          fail_count == 0,
            "commands_applied": success_count,
            "commands_failed":  fail_count,
            "results":          [{"action": c.get("action"), "success": ok, "message": msg}
                                 for c, (ok, msg) in applied],
            "structural":       structural,
            "changed_rows":     changed_rows,          # None => client full-reloads
            "added_rows":       added_rows,
            "removed_ids":      removed_ids,
            "undo_count":       len(sess["undo_stack"]),
            "redo_count":       len(sess["redo_stack"]),
            "edit_count":       len(sess["edit_history"]),
            "activity_count":   len(project.activities),
            "wbs_count":        len(project.wbs_nodes),
            "relation_count":   len(project.relations),
            # Edits patch the grid in place rather than reloading it, so the
            # drift badge has to travel with the edit or it goes stale.
            "out_of_date_count": _out_of_date_count(project),
        })
    except Exception as e:
        return jsonify({"error": f"Direct edit failed: {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/advise/milestones", methods=["GET"])
def advise_milestones():
    """
    What should drive each milestone, judged against the dates already set.

    Read-only on purpose: it returns recommendations with a verdict for each,
    and the client turns the ones the user accepts into edit commands. A wrong
    relationship costs more than a missing one, so nothing is applied here.
    """
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    try:
        limit = max(1, min(10, int(request.args.get("limit", 3))))
    except (TypeError, ValueError):
        limit = 3
    try:
        return jsonify(milestone_report(sess["project"], limit_per_milestone=limit))
    except Exception as e:
        return jsonify({"error": f"Logic advice failed: {e}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/rules/run", methods=["POST"])
def rules_run():
    """
    Run if/then find-and-change rules, or preview them.

    A preview reports what would change without writing, which matters when a
    mistyped pattern can sweep thousands of activities — an undo stack helps
    afterwards, but seeing the damage first is better.
    """
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    data = request.get_json() or {}
    rules = data.get("rules")
    if not rules or not isinstance(rules, list):
        return jsonify({"error": "rules (a non-empty list) is required"}), 400
    cmd = {"action": "bulk_rules", "rules": rules,
           "preview": bool(data.get("preview"))}
    for k in ("wbs_uid", "wbs_name", "wbs_code"):
        if data.get(k):
            cmd[k] = data[k]

    if cmd["preview"]:
        # a dry run must not touch the undo stack or the session at all
        from engine.edit_engine import apply_command as _apply
        try:
            ok, msg = _apply(sess["project"], cmd)
            return jsonify({"success": ok, "preview": True, "message": msg})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    return _apply_direct([cmd], data.get("label") or "Find and change")


@app.route("/api/advise/area", methods=["GET"])
def advise_area():
    """
    One branch of the schedule: what is in it, what logic it is missing, and
    which long-lead items feed it.

    Scoped on purpose. The full project context runs to tens of thousands of
    tokens, which forces shallow reasoning across everything instead of real
    reasoning about the area that was actually asked about.
    """
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required (e.g. 'Phase 1 MV Rooms')"}), 400
    try:
        rep = area_report(sess["project"], name)
        return jsonify(rep), (404 if "error" in rep else 200)
    except Exception as e:
        return jsonify({"error": f"Area advice failed: {e}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/advise/procurement", methods=["GET"])
def advise_procurement():
    """Long-lead equipment against the work it feeds, incl. anything dated to
    be installed before it can arrive."""
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    try:
        return jsonify(procurement_report(sess["project"],
                                          (request.args.get("name") or "").strip() or None))
    except Exception as e:
        return jsonify({"error": f"Procurement advice failed: {e}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/advise/apply", methods=["POST"])
def advise_apply():
    """Apply the recommendations the user accepted, as ordinary edit commands."""
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    data = request.get_json() or {}
    recs = data.get("recommendations")
    if not recs or not isinstance(recs, list):
        return jsonify({"error": "recommendations (a non-empty list) is required"}), 400
    cmds = to_commands(recs,
                       include_conflicts=bool(data.get("include_conflicts")),
                       drop_constraints=data.get("drop_constraints", True))
    if not cmds:
        return jsonify({"error": "Nothing to apply — every recommendation was a conflict"}), 400
    return _apply_direct(cmds, data.get("label") or f"Apply {len(recs)} logic recommendation(s)")


@app.route("/api/loading", methods=["GET"])
def crew_loading():
    """Crew demand per week — the curve that shows which weeks cannot be staffed."""
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    from engine.loading import crew_load
    scope = request.args.get("wbs_uid") or None
    return jsonify(crew_load(sess["project"], scope_uid=scope,
                             include_completed=request.args.get("completed") == "1",
                             include_past=request.args.get("past") == "1"))


@app.route("/api/lookahead", methods=["GET"])
def lookahead_view():
    """The next N weeks of work, grouped by WBS. ?format=csv for the field copy."""
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    from engine.loading import lookahead
    try:
        weeks = max(1, min(26, int(request.args.get("weeks", 3))))
    except (TypeError, ValueError):
        weeks = 3
    data = lookahead(sess["project"], weeks=weeks,
                     start=request.args.get("start") or None,
                     scope_uid=request.args.get("wbs_uid") or None,
                     include_completed=request.args.get("completed") == "1")
    if request.args.get("format") != "csv":
        return jsonify(data)

    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["WBS", "Activity ID", "Activity Name", "Start", "Finish",
                "Duration (d)", "Status", data.get("crew_field") or "Crew",
                "Critical", "In window"])
    for g in data["groups"]:
        for a in g["activities"]:
            w.writerow([g["wbs_path"], a["activity_id"], a["name"], a["start"],
                        a["finish"], a["duration_days"], a["status"],
                        "" if a["crew"] is None else a["crew"],
                        "Y" if a["critical"] else "",
                        "" if a["starts_in_window"] else "carried in"])
    stem = Path(sess.get("source_name", "schedule")).stem
    resp = app.response_class(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{stem}_{weeks}wk_lookahead_{data["from"]}.csv"')
    return resp


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
    # Which P6 release the file is for. P6 refuses a file built for a newer
    # schema than its own, with no error, so the caller picks the version.
    p6_version = request.args.get("p6_version")
    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()
    try:
        write_p6_xml(project, tmp.name, p6_version=p6_version)
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


@app.route("/api/cloud/status", methods=["GET"])
def cloud_status():
    """Secret-free view of whether R2 persistence is active."""
    st = cloud_store.status()
    st["saved_projects"] = len(_projects) if st.get("configured") else 0
    return jsonify(st)


@app.route("/api/cloud/save", methods=["POST"])
def cloud_save():
    """Manual 'Save to cloud' — flushes every loaded project to R2 now."""
    if not cloud_store.is_configured():
        return jsonify({"error": "Cloud storage isn't configured. Set the R2_* "
                                 "environment variables to enable it."}), 400
    saved, failed = [], []
    for pid in list(_projects):
        ok, _msg = _persist(pid)
        (saved if ok else failed).append(pid)
    _dirty_pids.clear()
    if failed:
        return jsonify({"error": f"Saved {len(saved)}, failed {len(failed)}: "
                                 f"{', '.join(failed)}", "saved": saved}), 502
    return jsonify({"success": True, "saved": saved,
                    "message": f"Saved {len(saved)} schedule(s) to Cloudflare R2"})


@app.route("/api/projects/delete", methods=["POST"])
def delete_project():
    data = request.get_json() or {}
    pid  = data.get("project_id")
    if pid not in _projects:
        return jsonify({"error": f"Project '{pid}' not found"}), 404
    del _projects[pid]
    _dirty_pids.discard(pid)
    if cloud_store.is_configured():
        try:
            cloud_store.delete(pid)
        except Exception:
            pass
    if _active_id[0] == pid:
        _active_id[0] = next(iter(_projects), None)
    return jsonify({"success": True, "active_id": _active_id[0],
                    "projects": [_project_list_item(k) for k in _projects]})


@app.route("/api/compare", methods=["POST"])
def compare_schedules():
    """
    Compare two loaded schedules and return a structured diff.
    Body: {"project_a_id": "...", "project_b_id": "..."}
    """
    data = request.get_json() or {}
    pid_a = data.get("project_a_id")
    pid_b = data.get("project_b_id")
    if not pid_a or not pid_b:
        return jsonify({"error": "project_a_id and project_b_id are required"}), 400
    sess_a = _projects.get(pid_a)
    sess_b = _projects.get(pid_b)
    if not sess_a or not sess_a["project"]:
        return jsonify({"error": f"Project '{pid_a}' not found or not loaded"}), 404
    if not sess_b or not sess_b["project"]:
        return jsonify({"error": f"Project '{pid_b}' not found or not loaded"}), 404
    try:
        diff = compare_projects(sess_a["project"], sess_b["project"])
        return jsonify({
            "success": True,
            "diff": diff,
            "project_a": {"id": pid_a, "name": sess_a["project"].name,
                          "activities": len(sess_a["project"].activities)},
            "project_b": {"id": pid_b, "name": sess_b["project"].name,
                          "activities": len(sess_b["project"].activities)},
        })
    except Exception as e:
        return jsonify({"error": f"Compare failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/copy-branch", methods=["POST"])
def copy_branch():
    """
    Copy a WBS branch from one loaded schedule into another.
    Body: {
      "source_project_id": "...",
      "source_wbs_code": "...",
      "target_project_id": "...",       (optional — defaults to active)
      "target_parent_code": "...",       (optional — nest under this WBS)
      "id_mode": "renumber" | "keep",    (default "renumber")
      "new_wbs_name": "..."              (optional — override root name)
    }
    """
    data = request.get_json() or {}
    src_pid = data.get("source_project_id")
    src_code = data.get("source_wbs_code")
    if not src_pid or not src_code:
        return jsonify({"error": "source_project_id and source_wbs_code are required"}), 400

    tgt_pid = data.get("target_project_id") or _active_id[0]
    if not tgt_pid:
        return jsonify({"error": "No target project — load a schedule first"}), 400

    sess_src = _projects.get(src_pid)
    sess_tgt = _projects.get(tgt_pid)
    if not sess_src or not sess_src["project"]:
        return jsonify({"error": f"Source project '{src_pid}' not found"}), 404
    if not sess_tgt or not sess_tgt["project"]:
        return jsonify({"error": f"Target project '{tgt_pid}' not found"}), 404

    try:
        # Push undo for the TARGET session (not necessarily the active one)
        tgt_stack = sess_tgt["undo_stack"]
        tgt_stack.append((f"Copy branch from {sess_src['project'].name}",
                          _snapshot_project(sess_tgt["project"])))
        if len(tgt_stack) > _MAX_UNDO:
            tgt_stack.pop(0)

        ok, msg, detail = copy_wbs_branch(
            sess_src["project"],
            src_code,
            sess_tgt["project"],
            tgt_parent_code=data.get("target_parent_code"),
            id_mode=data.get("id_mode", "renumber"),
            new_wbs_name=data.get("new_wbs_name"),
        )
        if not ok:
            sess_tgt["undo_stack"].pop()
            return jsonify({"error": msg}), 400

        sess_tgt["redo_stack"].clear()
        sess_tgt["last_undone"] = None
        sess_tgt["edit_history"].append({
            "instruction": f"[copy-branch] {msg}",
            "commands": [],
            "results": [{"action": "copy_wbs_branch", "success": True, "message": msg}],
        })
        _mark_dirty(tgt_pid)

        return jsonify({
            "success": True,
            "message": msg,
            "detail": detail,
            "undo_count": len(sess_tgt["undo_stack"]),
            "redo_count": len(sess_tgt["redo_stack"]),
            "activity_count": len(sess_tgt["project"].activities),
            "wbs_count": len(sess_tgt["project"].wbs_nodes),
            "relation_count": len(sess_tgt["project"].relations),
        })
    except Exception as e:
        return jsonify({"error": f"Copy failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/replace-branch", methods=["POST"])
def replace_branch():
    """
    Feature C — replace a WBS section in the target with the source version,
    reconnecting the surrounding logic (seams) where possible.
    Body: {
      "source_project_id", "source_wbs_code",
      "target_project_id" (optional — defaults to active), "target_wbs_code",
      "id_mode": "keep" | "renumber"   (default "keep"),
      "match":   "id" | "name"          (default "id" — how seams re-attach),
      "new_wbs_name" (optional)
    }
    """
    data = request.get_json() or {}
    src_pid = data.get("source_project_id")
    src_code = data.get("source_wbs_code")
    tgt_code = data.get("target_wbs_code")
    if not src_pid or not src_code or not tgt_code:
        return jsonify({"error": "source_project_id, source_wbs_code and "
                                 "target_wbs_code are required"}), 400
    tgt_pid = data.get("target_project_id") or _active_id[0]
    if not tgt_pid:
        return jsonify({"error": "No target project — load a schedule first"}), 400

    sess_src = _projects.get(src_pid)
    sess_tgt = _projects.get(tgt_pid)
    if not sess_src or not sess_src["project"]:
        return jsonify({"error": f"Source project '{src_pid}' not found"}), 404
    if not sess_tgt or not sess_tgt["project"]:
        return jsonify({"error": f"Target project '{tgt_pid}' not found"}), 404

    try:
        tgt_stack = sess_tgt["undo_stack"]
        tgt_stack.append((f"Replace section from {sess_src['project'].name}",
                          _snapshot_project(sess_tgt["project"])))
        if len(tgt_stack) > _MAX_UNDO:
            tgt_stack.pop(0)

        ok, msg, detail = replace_wbs_branch(
            sess_src["project"], src_code,
            sess_tgt["project"], tgt_code,
            id_mode=data.get("id_mode", "keep"),
            match=data.get("match", "id"),
            new_wbs_name=data.get("new_wbs_name"),
        )
        if not ok:
            sess_tgt["undo_stack"].pop()
            return jsonify({"error": msg}), 400

        sess_tgt["redo_stack"].clear()
        sess_tgt["last_undone"] = None
        sess_tgt["edit_history"].append({
            "instruction": f"[replace-branch] {msg}", "commands": [],
            "results": [{"action": "replace_wbs_branch", "success": True, "message": msg}],
        })
        _mark_dirty(tgt_pid)
        return jsonify({
            "success": True, "message": msg, "detail": detail,
            "undo_count": len(sess_tgt["undo_stack"]),
            "redo_count": len(sess_tgt["redo_stack"]),
            "activity_count": len(sess_tgt["project"].activities),
            "wbs_count": len(sess_tgt["project"].wbs_nodes),
            "relation_count": len(sess_tgt["project"].relations),
        })
    except Exception as e:
        return jsonify({"error": f"Replace failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/apply-changes", methods=["POST"])
def apply_changes():
    """
    Pull individual activity field values from the source schedule into the
    target — the "combine the differences" path.
    Body: {
      "source_project_id", "target_project_id" (optional — defaults to active),
      "changes": [{"activity_id": "A1000", "attrs": ["name", "planned_duration"]}]
                 attrs omitted or ["*"] applies every comparable field.
    }
    """
    data = request.get_json() or {}
    src_pid = data.get("source_project_id")
    changes = data.get("changes") or []
    if not src_pid or not changes:
        return jsonify({"error": "source_project_id and a non-empty changes list "
                                 "are required"}), 400
    tgt_pid = data.get("target_project_id") or _active_id[0]
    sess_src = _projects.get(src_pid)
    sess_tgt = _projects.get(tgt_pid)
    if not sess_src or not sess_src["project"]:
        return jsonify({"error": f"Source project '{src_pid}' not found"}), 404
    if not sess_tgt or not sess_tgt["project"]:
        return jsonify({"error": f"Target project '{tgt_pid}' not found"}), 404

    try:
        tgt_stack = sess_tgt["undo_stack"]
        tgt_stack.append((f"Apply changes from {sess_src['project'].name}",
                          _snapshot_project(sess_tgt["project"])))
        if len(tgt_stack) > _MAX_UNDO:
            tgt_stack.pop(0)

        ok, msg, detail = apply_activity_changes(
            sess_src["project"], sess_tgt["project"], changes)
        if not ok:
            sess_tgt["undo_stack"].pop()
            return jsonify({"error": msg}), 400

        sess_tgt["redo_stack"].clear()
        sess_tgt["last_undone"] = None
        sess_tgt["edit_history"].append({
            "instruction": f"[apply-changes] {msg}", "commands": [],
            "results": [{"action": "apply_activity_changes", "success": True, "message": msg}],
        })
        _mark_dirty(tgt_pid)
        return jsonify({
            "success": True, "message": msg, "detail": detail,
            "undo_count": len(sess_tgt["undo_stack"]),
            "redo_count": len(sess_tgt["redo_stack"]),
            "activity_count": len(sess_tgt["project"].activities),
        })
    except Exception as e:
        return jsonify({"error": f"Apply failed: {str(e)}",
                        "trace": traceback.format_exc()}), 500


@app.route("/api/schedule", methods=["GET"])
def schedule_view():
    sess = _get_session()
    if sess is None or sess["project"] is None:
        return jsonify({"error": "No schedule loaded"}), 400
    try:
        return _schedule_view_inner()
    except Exception as e:
        return jsonify({"error": f"Schedule build failed: {str(e)}", "trace": traceback.format_exc()}), 500


_MILESTONE_TYPES = {"Start Milestone", "Finish Milestone"}

# Edits that add/remove rows, change an activity's id or WBS, or re-key the grid.
# These force a full client reload; all other edits patch just the changed rows.
# Synthetic folder id for activities whose wbs_uid resolves to nothing.
_ORPHAN_WBS_UID = "__unassigned__"

def _wbs_signature(project):
    """Cheap fingerprint of the folder tree — order, nesting and names."""
    return [(w.uid, w.parent_uid, w.name, w.sequence_num)
            for w in _ordered_wbs(project)]


# Actions that reshape the WBS tree itself. Rows can be patched in place for
# everything else, including adding and deleting activities.
def _out_of_date_count(project) -> int:
    """
    How many activities' dates no longer agree with their own logic.

    Edits refresh the early dates but deliberately leave Start / Finish alone —
    P6 does not reschedule until you press F9 either — so the schedule can
    drift from its network with nothing on screen changing. This is the number
    behind the badge on the Schedule button, and it is a plain comparison: the
    early dates were already recomputed by the edit that came before it.

    Started and completed work is excluded: those dates are actuals, and no
    amount of rescheduling moves them.
    """
    return sum(
        1 for a in project.activities
        if a.status == "Not Started" and not a.actual_start
        and a.early_start and a.planned_start
        and str(a.early_start)[:10] != str(a.planned_start)[:10]
    )


_TREE_ACTIONS = {
    "add_wbs", "rename_wbs", "bulk_create_wbs", "add_wbs_for_each",
    "bulk_create_wbs_for_each", "move_wbs", "reorder_wbs",
    "delete_wbs", "duplicate_wbs", "move_activity_wbs", "move_activities",
    "copy_activities", "bulk_rules",
}

_STRUCTURAL_ACTIONS = {
    "add_activity", "delete_activity", "bulk_add_activity",
    "add_wbs", "rename_wbs", "bulk_create_wbs", "add_wbs_for_each",
    "bulk_create_wbs_for_each", "move_activity_wbs", "move_wbs",
    "reorder_wbs", "delete_wbs",
    "duplicate_wbs", "copy_activities", "update_activity_type",
    "update_activity_id", "bulk_update_activity_id",
}


def _fmt_date(d):
    return str(d)[:10] if d else None


def _build_rel_maps(project):
    """uid -> [{activity_id, name, type, lag}] predecessor and successor lists."""
    preds_map: dict = {}
    succs_map: dict = {}
    for rel in project.relations:
        pred_act = project.get_activity(uid=rel.predecessor_uid)
        succ_act = project.get_activity(uid=rel.successor_uid)
        if pred_act and succ_act:
            succs_map.setdefault(rel.predecessor_uid, []).append(
                {"activity_id": succ_act.activity_id, "name": succ_act.name,
                 "type": rel.type, "lag": rel.lag})
            preds_map.setdefault(rel.successor_uid, []).append(
                {"activity_id": pred_act.activity_id, "name": pred_act.name,
                 "type": rel.type, "lag": rel.lag})
    return preds_map, succs_map


def _activity_row(a, preds_map, succs_map):
    """
    One schedule-grid row. Null / False / empty values are omitted to shrink the
    payload (the client already treats missing fields as null/false/[]), which
    roughly halves the JSON for a big schedule.
    """
    is_milestone = a.activity_type in _MILESTONE_TYPES
    row = {
        "uid":           a.uid,
        "activity_id":   a.activity_id,
        # the client needs this to place a newly added row without a reload
        "wbs_uid":       a.wbs_uid,
        "name":          a.name,
        "duration_days": round(a.planned_duration / 8.0, 1) if a.planned_duration else 0.0,
        "status":        a.status,
        "activity_type": a.activity_type,
    }
    opt = {
        "planned_start":  _fmt_date(a.planned_start),
        "planned_finish": _fmt_date(a.planned_finish),
        "actual_start":   _fmt_date(a.actual_start),
        "actual_finish":  _fmt_date(a.actual_finish),
        "early_start":    _fmt_date(a.early_start),
        "early_finish":   _fmt_date(a.early_finish),
        "late_start":     _fmt_date(a.late_start),
        "late_finish":    _fmt_date(a.late_finish),
        "total_float":    round(a.total_float / 8.0, 1) if a.total_float is not None else None,
        "free_float":     round(a.free_float / 8.0, 1) if a.free_float is not None else None,
        "constraint_type": a.constraint_type,
        "constraint_date": _fmt_date(a.constraint_date),
    }
    for k, v in opt.items():
        if v is not None:
            row[k] = v
    if a.percent_complete:
        row["percent_complete"] = a.percent_complete
    if a.planned_labor_units:
        row["planned_labor_units"] = a.planned_labor_units
    if getattr(a, "udfs", None):
        row["udfs"] = dict(a.udfs)
    if is_milestone:
        row["is_milestone"] = True
    if a.is_critical:
        row["is_critical"] = True
    if a.is_longest_path:
        row["is_longest_path"] = True
    preds = preds_map.get(a.uid)
    succs = succs_map.get(a.uid)
    if preds:
        row["predecessors"] = preds
    if succs:
        row["successors"] = succs
    return row


def _ordered_wbs(project):
    """
    WBS nodes in tree order — each parent immediately followed by its children,
    siblings by sequence_num. The stored list is in creation order, which made a
    re-parented folder appear far from its new parent in the grid.
    """
    children = {}
    roots = []
    by_uid = {w.uid: w for w in project.wbs_nodes}
    for w in project.wbs_nodes:
        if w.parent_uid and w.parent_uid in by_uid:
            children.setdefault(w.parent_uid, []).append(w)
        else:
            roots.append(w)
    for lst in children.values():
        lst.sort(key=lambda w: (w.sequence_num, w.name))
    roots.sort(key=lambda w: (w.sequence_num, w.name))

    out, seen = [], set()

    def walk(node):
        if node.uid in seen:          # defensive: never loop on a bad tree
            return
        seen.add(node.uid)
        out.append(node)
        for c in children.get(node.uid, []):
            walk(c)

    for r in roots:
        walk(r)
    for w in project.wbs_nodes:       # anything unreachable still gets shown
        if w.uid not in seen:
            out.append(w)
    return out


def _flat_rows(project):
    """activity_id -> row dict, for diffing before/after an edit."""
    preds_map, succs_map = _build_rel_maps(project)
    return {a.activity_id: _activity_row(a, preds_map, succs_map)
            for a in project.activities}


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
                "name": succ_act.name,
                "type": rel.type,
                "lag": rel.lag,
            })
            preds_map.setdefault(rel.successor_uid, []).append({
                "activity_id": pred_act.activity_id,
                "name": pred_act.name,
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
    for wbs in _ordered_wbs(project):
        activities_out = [_activity_row(a, preds_map, succs_map)
                          for a in acts_by_wbs.get(wbs.uid, [])]
        wbs_sections.append({
            "uid":        wbs.uid,
            "name":       wbs.name,
            "code":       wbs.code,
            "parent_uid": wbs.parent_uid,
            "depth":      wbs_depth(wbs.uid),
            "activities": activities_out,
        })

    # An activity whose wbs_uid matches no folder used to be dropped from the
    # grid entirely while still counting toward activity_count — rows that
    # exist, are edited, and are exported, but cannot be seen. Surface them in
    # a synthetic folder instead: an orphan is a bug worth noticing, and
    # silently hiding it makes an edit look like it did nothing.
    known_uids = {w.uid for w in project.wbs_nodes}
    orphans = [a for a in project.activities if a.wbs_uid not in known_uids]
    if orphans:
        wbs_sections.append({
            "uid":        _ORPHAN_WBS_UID,
            "name":       "Unassigned (no WBS folder)",
            "code":       "—",
            "parent_uid": None,
            "depth":      0,
            "activities": [_activity_row(a, preds_map, succs_map) for a in orphans],
            "is_orphan_bucket": True,
        })

    # Roll activity counts up the tree. A parent folder usually holds no
    # activities directly — they live in its children — so a bare direct count
    # reads as "0 act." on a branch containing hundreds. _ordered_wbs puts
    # parents before children, so summing in reverse accumulates bottom-up.
    totals = {w["uid"]: len(w["activities"]) for w in wbs_sections}
    for w in reversed(wbs_sections):
        parent = w.get("parent_uid")
        if parent in totals:
            totals[parent] += totals[w["uid"]]
    for w in wbs_sections:
        w["activity_count_direct"] = len(w["activities"])
        w["activity_count_total"] = totals[w["uid"]]

    from engine.edit_engine import electricians_field
    return jsonify({
        "project_name":   project.name,
        "data_date":      project.data_date,
        "activity_count": len(project.activities),
        "out_of_date_count": _out_of_date_count(project),
        # Which UDF the grid's Electricians column reads and writes — the grid
        # needs the exact title because it varies between P6 projects.
        "electricians_field": electricians_field(project),
        "udf_titles":     sorted({u.title for u in (project.udf_types or []) if u.title}),
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


# Restore any cloud-persisted schedules at startup (no-op unless R2 is
# configured). Runs at import time so it also applies under gunicorn.
try:
    _restore_from_cloud()
except Exception:
    pass


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=False)
