"""
sheet_sync.py — reconcile rows read off a screenshot against the real schedule.

Somebody sends a lookahead, an owner's status report, a subcontractor's bar
chart. The question is always one of two: "how are we tracking against this?"
or "make mine match this." Both need the same first step — work out which of
MY activities each printed row is, and exactly what differs.

Nothing here calls a model and nothing here mutates. A reading is transcribed
text; this turns it into a diff a human can look at line by line before a
single date moves. That separation is the whole point: an OCR misread of one
digit should cost a glance, not a corrupted actual date.

Matching is deliberately conservative:
  - an exact activity id is trusted outright
  - an exact name is trusted
  - a close name is offered but flagged, never auto-applied
  - anything weaker is reported as unmatched rather than guessed at

Actual dates are treated as the dangerous field they are. Writing one records
that work really happened on a day; getting it wrong from a blurry screenshot
rewrites history. So an actual date is only ever proposed when the source
marked it as actual, and overwriting an actual the schedule already holds is
flagged as the serious change it is.
"""

import datetime as _dt
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

EXACT_ID = "id"
EXACT_NAME = "name"
CLOSE_NAME = "close"

# Below this, two names are different work that happens to share a word.
_NEAR = 0.86

# Fields the sync can change, in the order a scheduler reads them.
_FIELDS = ("actual_start", "actual_finish", "start", "finish",
           "percent_complete", "status")

_FIELD_LABEL = {
    "start": "Start", "finish": "Finish",
    "actual_start": "Actual start", "actual_finish": "Actual finish",
    "percent_complete": "% complete", "status": "Status",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ",
                                      (s or "").lower())).strip()


def _d(v) -> Optional[str]:
    s = str(v or "").strip()[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def _act_value(act, field) -> Any:
    if field == "percent_complete":
        v = getattr(act, "percent_complete", None)
        return None if v is None else round(float(v))
    if field == "status":
        return getattr(act, "status", None)
    if field in ("start", "finish"):
        return _d(getattr(act, f"planned_{field}", None))
    return _d(getattr(act, field, None))


def match_rows(project, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pair each printed row with an activity, and say what differs.

    Returns matched rows carrying a `changes` list (possibly empty — that is
    the "we agree" case and is worth seeing), plus the rows nothing answered.
    """
    acts = list(getattr(project, "activities", None) or [])
    by_id = {(a.activity_id or "").strip().lower(): a for a in acts}
    by_name: Dict[str, List] = {}
    for a in acts:
        by_name.setdefault(_norm(a.name), []).append(a)

    matched, unmatched = [], []
    for i, row in enumerate(rows or []):
        act, how = None, None
        rid = (row.get("activity_id") or "").strip().lower()
        if rid and rid in by_id:
            act, how = by_id[rid], EXACT_ID
        if act is None:
            key = _norm(row.get("name"))
            if key and len(by_name.get(key, [])) == 1:
                act, how = by_name[key][0], EXACT_NAME
            elif key:
                best, score = None, 0.0
                for a in acts:
                    r = SequenceMatcher(None, key, _norm(a.name)).ratio()
                    if r > score:
                        best, score = a, r
                if best is not None and score >= _NEAR:
                    act, how = best, CLOSE_NAME
        if act is None:
            unmatched.append({"index": i, "row": row,
                              "why": "no activity with that id or name"})
            continue
        matched.append({
            "index": i,
            "activity_id": act.activity_id,
            "activity_name": act.name,
            "match": how,
            "changes": _diff(act, row),
        })
    return {
        "matched": matched,
        "unmatched": unmatched,
        "rows_read": len(rows or []),
        "rows_matched": len(matched),
        "rows_with_changes": sum(1 for m in matched if m["changes"]),
    }


def _diff(act, row) -> List[Dict[str, Any]]:
    """Every field the printed row disagrees with, with its weight."""
    out = []
    for field in _FIELDS:
        new = row.get(field)
        if field == "percent_complete":
            if new is None:
                continue
            try:
                new = round(float(new))
            except (TypeError, ValueError):
                continue
            if not 0 <= new <= 100:
                continue
        else:
            new = (str(new).strip() or None) if new is not None else None
            if field != "status":
                new = _d(new)
            if not new:
                continue

        old = _act_value(act, field)
        if old == new:
            continue

        # Writing an actual says the work really happened that day. Replacing
        # one the schedule already carries is a rewrite of history, not an
        # update, and must never ride along unnoticed inside a bulk apply.
        severity = "normal"
        note = ""
        if field in ("actual_start", "actual_finish"):
            if old:
                severity, note = "high", "overwrites an actual date already recorded"
            else:
                severity, note = "actual", "records work as having actually happened"
        elif field == "status":
            # A status change IS an actualization, not a label change. P6
            # defines the states by which actual dates exist, so "In Progress"
            # writes an actual start and "Completed" writes both. Grading this
            # as an ordinary edit let a ticked status quietly write an actual
            # the user had deliberately left unticked — the exact thing the
            # tick was there to prevent.
            if (act.status or "") == "Completed" and new != "Completed":
                severity, note = "high", "reopens a completed activity and clears its actuals"
            elif new == "Completed":
                severity = "high" if act.actual_finish else "actual"
                note = ("records this as finished — writes an actual start and "
                        "finish" + (" over the ones already recorded" if act.actual_finish else ""))
            elif new == "In Progress":
                severity = "high" if act.actual_start else "actual"
                note = ("records this as started — writes an actual start"
                        + (" over the one already recorded" if act.actual_start else ""))

        out.append({"field": field, "label": _FIELD_LABEL[field],
                    "from": old, "to": new,
                    "severity": severity, "note": note})
    return out


def to_commands(project, matched: List[Dict[str, Any]],
                only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Turn accepted differences into edit commands.

    `only` limits which fields may move — the whole point of "only match the
    dates and actualization status" is that nothing else does.
    """
    allow = set(only) if only else set(_FIELDS)
    cmds = []
    for m in matched:
        aid = m["activity_id"]
        dates = {c["field"]: c["to"] for c in m["changes"]
                 if c["field"] in allow and c["field"] in
                 ("start", "finish", "actual_start", "actual_finish")}
        # update_planned_date takes ONE field at a time, and start must land
        # before finish — typing a finish adjusts the duration against whatever
        # start is current, so doing them out of order gives the right dates by
        # luck and the wrong duration.
        for f in ("start", "finish"):
            if f in dates:
                cmds.append({"action": "update_planned_date", "activity_id": aid,
                             "field": f, "date": dates[f]})
        # set_actual_date names the field 'start'/'finish', not 'actual_start'.
        for f, short in (("actual_start", "start"), ("actual_finish", "finish")):
            if f in dates:
                cmds.append({"action": "set_actual_date", "activity_id": aid,
                             "field": short, "date": dates[f]})
        prog = {c["field"]: c["to"] for c in m["changes"] if c["field"] in allow}
        if "percent_complete" in prog or "status" in prog:
            cmd = {"action": "set_progress", "activity_id": aid}
            if "percent_complete" in prog:
                cmd["percent_complete"] = prog["percent_complete"]
            if "status" in prog:
                cmd["status"] = prog["status"]
            cmds.append(cmd)
    return cmds


def summarize(result: Dict[str, Any]) -> str:
    """One readable paragraph — the answer to 'how are we tracking?'."""
    agree = result["rows_matched"] - result["rows_with_changes"]
    bits = [f"Read {result['rows_read']} row(s); matched {result['rows_matched']} "
            f"to activities in this schedule."]
    if agree:
        bits.append(f"{agree} already agree.")
    if result["rows_with_changes"]:
        bits.append(f"{result['rows_with_changes']} differ.")
    if result["unmatched"]:
        bits.append(f"{len(result['unmatched'])} row(s) matched nothing here.")
    return " ".join(bits)
