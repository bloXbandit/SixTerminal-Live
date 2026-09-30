# -*- coding: utf-8 -*-
"""
gantt_pdf.py — a schedule back out of the Gantt chart somebody printed.

WHY
  Sometimes the only copy of a schedule anyone will give you is a PDF of the
  Gantt. It is not a schedule; it is a picture of one. But a P6 print is a
  fixed-column table, and everything except the logic is in it: activity ids,
  names, durations, start and finish, which dates are actual, and — in the
  indentation of the left column — the WBS.

  That last one is the part worth having. A previous attempt at this file read
  the rows and threw the indentation away, so 3,400 activities arrived as
  siblings in one flat list and the hierarchy had to be guessed back out of
  the activity ids afterwards. The indent is right there, and it is what the
  scheduler actually typed.

WHAT IT CANNOT GIVE YOU
  Logic. A Gantt print shows bars, not relationships, so a schedule read this
  way has no predecessors and will collapse to the data date if it is ever
  rescheduled. That is a property of the source, not of this reader, and it is
  reported rather than papered over.

  Resources, calendars, float, constraints. None of them are on the page.
"""

import collections
import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

# Where each column sits on the page, in points. A P6 print is laid out to
# fixed positions, so reading by x is exact where reading the flattened text
# is guesswork: an activity called "Pour 2 Level 1" has numbers in its name,
# and no amount of pattern matching reliably tells those from the duration.
COL_NAME = (120.0, 330.0)
COL_DURATION = (330.0, 367.0)
COL_START = (367.0, 402.0)
COL_FINISH = (402.0, 440.0)
BAR_FROM = 440.0          # the bar's own label, which repeats the name

# Rows that are the page, not the schedule.
FURNITURE = re.compile(
    r"Page \d+ of \d+|^Activity ID|^Duration$|Printed:|"
    r"^(Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug)(\s|$)|"
    r"^\d{4}(\s+\d{4})+$|Actual Level|Remaining Level|Critical Remaining|"
    r"^Milestone$|^summary$", re.I)

DATE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{2})$")
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _date(token: str) -> Optional[str]:
    """'28-Jan-26' -> '2026-01-28'. P6 prints two-digit years."""
    m = DATE.match(token.strip())
    if not m:
        return None
    day, mon, yr = m.groups()
    month = MONTHS.get(mon.lower())
    if not month:
        return None
    year = 2000 + int(yr)
    try:
        return _dt.date(year, month, int(day)).isoformat()
    except ValueError:
        return None


def undouble(text: str) -> str:
    """
    'Milestones Milestones (Phase (Phase 1) 1)' -> 'Milestones (Phase 1)'.

    A WBS row is printed bold, and the renderer emits every word twice —
    overlapping, so it looks right on the page and reads as a stutter once the
    words are pulled out. Done word by word rather than by halving the string,
    because the duplication interleaves: each word is followed by its own copy.
    """
    words = text.split()
    out: List[str] = []
    i = 0
    while i < len(words):
        if i + 1 < len(words) and words[i] == words[i + 1]:
            out.append(words[i])
            i += 2
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _in(word, span: Tuple[float, float]) -> bool:
    return span[0] <= word["x0"] < span[1]


def _rows(page, tol: float = 3.0):
    """The page's words gathered into visual rows, each sorted left to right."""
    buckets: Dict[int, List[Dict]] = collections.defaultdict(list)
    for w in (page.extract_words(use_text_flow=True) or []):
        buckets[int(round(w["top"] / tol))].append(w)
    for key in sorted(buckets):
        yield sorted(buckets[key], key=lambda w: w["x0"])


def _levels(indents: List[float], tol: float = 1.2) -> Dict[float, int]:
    """
    The distinct left edges, in order, mapped to depth.

    Derived from the document rather than assumed, because the indent step is
    a P6 layout setting — about 2.4 points here — and a print made with a
    different one would otherwise come out flat.
    """
    seen: List[float] = []
    for x in sorted(indents):
        if not seen or x - seen[-1] > tol:
            seen.append(x)
    return {x: i for i, x in enumerate(seen)}


def read(path: str, id_prefix: Optional[str] = None,
         max_pages: Optional[int] = None) -> Dict[str, Any]:
    """
    Every row of the printed schedule, with its depth.

    Returns {rows, levels, counts}. Each row is a dict with kind ('wbs' or
    'activity'), depth, activity_id, name, duration_days, start, finish, and
    whether either date was actual.
    """
    import pdfplumber

    raw: List[Dict[str, Any]] = []
    indents: List[float] = []
    prefix = re.compile(rf"^{id_prefix}\.", re.I) if id_prefix else \
        re.compile(r"^[A-Z][A-Z0-9]{1,9}[.-]")

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if max_pages and pno > max_pages:
                break
            for words in _rows(page):
                if not words:
                    continue
                flat = " ".join(w["text"] for w in words)
                if FURNITURE.search(flat):
                    continue

                dur = [w for w in words if _in(w, COL_DURATION)]
                start = [w for w in words if _in(w, COL_START)]
                finish = [w for w in words if _in(w, COL_FINISH)]
                # A milestone has no duration, so P6 prints ONE date for it —
                # a start milestone's start, a finish milestone's finish, in
                # its own column and nothing in the other. Requiring both
                # silently dropped all 62 of them, which on this schedule is
                # every contractual date that matters: NTP, Permanent Power,
                # Building Pad Ready, Substantial Completion.
                if not dur or not (start or finish):
                    continue        # not a schedule row

                head = words[0]
                is_act = bool(prefix.match(head["text"]))
                name_words = [w for w in words
                              if _in(w, COL_NAME) or
                              (not is_act and w["x0"] < COL_NAME[0])]
                name = " ".join(w["text"] for w in name_words)
                if not is_act:
                    name = undouble(name)
                    # A handful of folder rows print no name in the left column
                    # at all — only the bar's label carries it.
                    if not name.strip():
                        name = undouble(" ".join(
                            w["text"] for w in words if w["x0"] >= BAR_FROM))

                indent = round(head["x0"], 1)
                # A few folder rows print no name in the left column at all —
                # only the bar carries it — so the row's first word is its
                # DURATION and its x tells us nothing about its depth. Marked
                # here and given a depth from its neighbours below, because
                # taken at face value they came out as siblings of the project
                # root with the whole branch beneath them hanging off nothing.
                unknown_indent = head["x0"] >= COL_NAME[1]
                if not unknown_indent:
                    indents.append(indent)
                s_txt = next((_date(w["text"]) for w in start
                              if _date(w["text"])), None)
                f_txt = next((_date(w["text"]) for w in finish
                              if _date(w["text"])), None)
                days = _num(dur)
                # Which kind of milestone it is comes from which column its one
                # date was printed in.
                kind = ""
                if is_act and days == 0 and not (s_txt and f_txt):
                    kind = "Start Milestone" if s_txt else "Finish Milestone"
                raw.append({
                    "kind": "activity" if is_act else "wbs",
                    "activity_type": kind,
                    "indent": indent,
                    "unknown_indent": unknown_indent,
                    "page": pno,
                    "activity_id": head["text"] if is_act else None,
                    "name": name.strip(),
                    "duration_days": days,
                    "start": s_txt or f_txt,
                    "finish": f_txt or s_txt,
                    "start_actual": any(w["text"] == "A" for w in start),
                    "finish_actual": any(w["text"] == "A" for w in finish),
                })
            page.flush_cache()
            if hasattr(page, "_objects"):
                page._objects = None

    levels = _levels(indents)
    for row in raw:
        row["depth"] = levels.get(row["indent"], 0)
    # A folder whose name did not render has no indent to read, so it takes the
    # depth its children imply: a folder sits one level above whatever comes
    # directly under it.
    for i, row in enumerate(raw):
        if not row.get("unknown_indent"):
            continue
        nxt = next((r for r in raw[i + 1:] if not r.get("unknown_indent")), None)
        prev = next((r for r in reversed(raw[:i])
                     if not r.get("unknown_indent")), None)
        if nxt is not None:
            row["depth"] = max(0, nxt["depth"] - 1)
        elif prev is not None:
            row["depth"] = prev["depth"]
    return {"rows": raw, "levels": levels,
            "counts": {"rows": len(raw),
                       "activities": sum(1 for r in raw if r["kind"] == "activity"),
                       "folders": sum(1 for r in raw if r["kind"] == "wbs"),
                       "unplaced_folders": sum(1 for r in raw
                                               if r.get("unknown_indent"))}}


def _num(words) -> float:
    for w in words:
        try:
            return float(w["text"].replace(",", ""))
        except ValueError:
            continue
    return 0.0


def to_project(parsed: Dict[str, Any], project_id: str, project_name: str,
               data_date: Optional[str] = None, hours_per_day: float = 8.0):
    """
    The parsed rows as a Project.

    A folder row opens a level and every row deeper than it belongs under it,
    which is exactly what the indentation on the page means — so the tree comes
    out as the scheduler typed it rather than being inferred afterwards.
    """
    from .schedule_model import (Activity, Calendar, Project, WBSNode)

    rows = parsed["rows"]
    starts = [r["start"] for r in rows if r["start"]]
    project = Project(uid="1", name=project_name, id=project_id,
                      data_date=data_date,
                      planned_start=min(starts) if starts else None)
    project.calendars = [Calendar(uid="1", name="Standard")]

    root = WBSNode(uid="WBS-0000", name=project_name, code=project_id)
    project.wbs_nodes = [root]
    project.activities = []
    project.relations = []

    # The print's own outermost row IS the project — it carries the schedule's
    # name and its whole duration. Kept as a folder it produced a root inside a
    # root, which is the shape P6 shows as an extra level everybody then has to
    # expand past.
    base = min((r["depth"] for r in rows), default=0)
    rows = [r for r in rows
            if not (r["kind"] == "wbs" and r["depth"] == base)]

    # The open folder at each depth. A row at depth d hangs off whatever folder
    # is open at d-1, and opening a folder closes everything deeper.
    open_at: Dict[int, str] = {base: root.uid}
    seq = 0
    for row in rows:
        depth = row["depth"]
        parent = open_at.get(depth - 1) or root.uid
        if row["kind"] == "wbs":
            seq += 1
            uid = f"WBS-{seq:04d}"
            project.wbs_nodes.append(WBSNode(
                uid=uid, name=row["name"] or f"Folder {seq}",
                code=re.sub(r"\W+", "", row["name"] or "")[:12].upper()
                     or f"W{seq}",
                parent_uid=parent))
            for deeper in [d for d in open_at if d >= depth]:
                del open_at[deeper]
            open_at[depth] = uid
            continue

        hours = float(row["duration_days"] or 0) * hours_per_day
        done = bool(row["finish_actual"])
        started = bool(row["start_actual"])
        status = "Completed" if done else ("In Progress" if started
                                          else "Not Started")
        act = Activity(
            uid=f"A-{len(project.activities) + 1:05d}",
            activity_id=row["activity_id"],
            name=row["name"] or row["activity_id"],
            wbs_uid=parent,
            calendar_uid="1",
            status=status,
            percent_complete=100.0 if done else (50.0 if started else 0.0),
            planned_duration=hours,
            remaining_duration=0.0 if done else hours,
            planned_start=row["start"],
            planned_finish=row["finish"],
            actual_start=row["start"] if started else None,
            actual_finish=row["finish"] if done else None,
        )
        if row.get("activity_type"):
            act.activity_type = row["activity_type"]
        project.activities.append(act)
    project.build_lookups()
    return project


def describe(parsed: Dict[str, Any]) -> str:
    c = parsed["counts"]
    depths = collections.Counter(r["depth"] for r in parsed["rows"])
    return (f"{c['rows']} rows read: {c['activities']} activities, "
            f"{c['folders']} folders.\n"
            f"indent levels found: {len(parsed['levels'])}\n"
            f"rows by depth: {dict(sorted(depths.items()))}\n"
            f"NOTE: a Gantt print carries no relationships, so this schedule "
            f"has no logic.")
