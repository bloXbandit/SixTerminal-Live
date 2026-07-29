# -*- coding: utf-8 -*-
"""
paste_parser.py — Turn text copied out of a PDF (or Excel) into schedule rows.

WHY THIS EXISTS
  Parsing a 27-page PDF server-side is slow and memory-hungry — on a small host
  it can exceed the request limit and die. But the user can already *select* the
  rows they want in any PDF viewer and copy them. That clipboard text is a few
  kilobytes, arrives instantly, and needs no Java, no OCR and no upload. So the
  fastest reliable path for "load a block of known activities" is paste, not file
  parsing.

WHAT IT HANDLES
  1. Tab-separated text (Excel / Google Sheets) — split on tabs, trivially.
  2. Space-aligned text (the PDF case) — columns are inferred per line by
     anchoring on the things that have an unambiguous shape:
        · dates    11-Mar-25 / 11-Mar-25 A / 12-Dec-25*
        · duration the integer immediately before the first date
        · id       a leading token like MDC1.FDG.1210 or A1000
     Everything left in the middle is the activity name — which is the only
     free-text field, so it is the one we derive last rather than guess at.

  A line with no id (e.g. a highlighted "Funding" band) is left as a section
  header; the downstream contract builder already treats those as WBS rows.

OUTPUT
  Rows shaped exactly like the Excel/PDF readers produce, so the existing
  _rows_to_contract() does the classification, review flags and confidence
  scoring — one pipeline, one set of behaviours to trust.
"""

import re
from typing import List, Dict, Any, Tuple

# 11-Mar-25 · 11-Mar-25 A · 12-Dec-25* · 05-Mar-2026
# The trailing "A" marks an ACTUAL date and drives imported status, so it must
# stay attached to the cell. The "*" marks a constraint. Note \*? binds directly
# to the year (12-Dec-25*) — letting \s* run first would swallow the space that
# the " A" alternative still needs.
_DATE_RE = re.compile(
    r"\d{1,2}-[A-Za-z]{3}-\d{2,4}\*?(?:\s+A\b)?|"          # 11-Mar-25 A · 12-Dec-25*
    r"\d{1,2}/\d{1,2}/\d{2,4}\*?(?:\s+A\b)?"                # 3/11/25 A
)

# MDC1.FDG.1210 · A1000 · EC-100 · T-1000  (must contain a digit)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*\d[A-Za-z0-9._\-/]*$")

# a bare duration cell: 45 · 45d · 12.5 · 0
_DUR_RE = re.compile(r"^\d+(?:\.\d+)?\s*[dhwDHW]?$")

_HEADER = ["Activity ID", "Activity Name", "Duration", "Start", "Finish"]

# Column-header words that mean the pasted block included its own header line
_HEADER_WORDS = ("activity id", "activity name", "task name", "original duration",
                 "remaining duration", "duration", "start", "finish", "description")


def _looks_like_id(tok: str) -> bool:
    if not tok:
        return False
    t = tok.strip()
    if not _ID_RE.match(t):
        return False
    # a pure number is a duration or a row counter, not an id
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return False
    return True


def _is_header_line(line: str) -> bool:
    low = re.sub(r"\s+", " ", line.lower())
    hits = sum(1 for w in _HEADER_WORDS if w in low)
    return hits >= 2


def _split_line(line: str) -> List[str]:
    """
    Split one space-aligned line into [id, name, duration, start, finish].

    Parsed from the OUTSIDE IN: dates and duration have rigid shapes, so they are
    matched first and the free-text name is whatever survives in the middle. That
    ordering is what keeps names containing digits — "MV Switchgear 38 kV (GIS
    SE)", "Generators (4MW)", "PCCO # 1 Review" — from being eaten as durations.
    """
    raw = line.rstrip()
    if not raw.strip():
        return []

    # ── dates: keep the last two, they are Start and Finish ──────────────────
    dates = [(m.group(0).strip(), m.start()) for m in _DATE_RE.finditer(raw)]
    start = finish = ""
    head_end = len(raw)
    if dates:
        head_end = dates[0][1]
        if len(dates) == 1:
            start = dates[0][0]
        else:
            start, finish = dates[-2][0], dates[-1][0]

    head = raw[:head_end].rstrip()

    # ── duration: the trailing integer of the head, just before the dates ────
    duration = ""
    m = re.search(r"(?:^|\s)(\d+(?:\.\d+)?\s*[dhwDHW]?)$", head)
    if m and dates:
        duration = m.group(1).strip()
        head = head[:m.start()].rstrip()

    # ── id: a leading token with the shape of an activity code ───────────────
    act_id = ""
    parts = head.strip().split(None, 1)
    if parts and _looks_like_id(parts[0]):
        act_id = parts[0]
        head = parts[1] if len(parts) > 1 else ""
    name = re.sub(r"\s{2,}", " ", head.strip())

    if not (act_id or name or duration or start):
        return []
    return [act_id, name, duration, start, finish]


def parse_pasted_text(text: str) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """
    Return (rows, info). `rows` starts with a synthetic header so the standard
    contract builder can map columns; `info` reports what we detected.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [l for l in text.split("\n")]
    nonempty = [l for l in lines if l.strip()]
    info: Dict[str, Any] = {"mode": None, "lines_in": len(nonempty),
                            "rows_parsed": 0, "warnings": []}
    if not nonempty:
        info["warnings"].append("Nothing was pasted.")
        return [_HEADER], info

    tabbed = sum(1 for l in nonempty if "\t" in l)
    rows: List[List[Any]] = [_HEADER]

    if tabbed >= max(1, len(nonempty) // 2):
        # ── Excel / Sheets ───────────────────────────────────────────────────
        info["mode"] = "tab"
        for l in nonempty:
            if _is_header_line(l):
                continue
            cells = [c.strip() for c in l.split("\t")]
            cells = [c for c in cells if c != ""] or [""]
            # normalize into our 5 columns when the paste is wider
            if len(cells) < 5:
                cells = cells + [""] * (5 - len(cells))
            if not _looks_like_id(cells[0]):
                # no id column — shift so the text lands in Name
                cells = [""] + cells[:4]
            rows.append(cells[:5])
    else:
        # ── PDF / plain text ─────────────────────────────────────────────────
        info["mode"] = "text"
        for l in nonempty:
            if _is_header_line(l):
                continue
            cells = _split_line(l)
            if cells:
                rows.append(cells)

    info["rows_parsed"] = len(rows) - 1
    if info["rows_parsed"] == 0:
        info["warnings"].append(
            "No rows could be read from the pasted text. Copy the activity rows "
            "themselves (id, name, duration, dates) rather than a screenshot.")
    else:
        with_id = sum(1 for r in rows[1:] if r[0])
        if with_id == 0:
            info["warnings"].append(
                "No activity IDs were found — every line was read as a WBS "
                "heading. Include the ID column in your selection.")
        no_dates = sum(1 for r in rows[1:] if r[0] and not r[3] and not r[4])
        if with_id and no_dates == with_id:
            info["warnings"].append(
                "No dates were detected — activities will be created without "
                "dates and scheduled from logic.")
    return rows, info


def contract_from_paste(text: str, project_name: str = "Pasted activities") -> Dict[str, Any]:
    """Parse pasted text straight into the standard extraction contract."""
    from engine.importer import _rows_to_contract
    rows, info = parse_pasted_text(text)
    meta = {
        "source_type": "paste",
        "source_name": "clipboard",
        "project_name": project_name,
        "project_id": None,
        "data_date": None,
        "hours_per_day": 8,
        "engine": f"paste:{info.get('mode') or 'none'}",
        "llm_used": False,
    }
    contract = _rows_to_contract(rows, meta)
    contract["meta"]["warnings"] = info["warnings"] + contract["meta"].get("warnings", [])
    contract["meta"]["paste_info"] = info
    return contract
