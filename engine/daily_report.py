# -*- coding: utf-8 -*-
"""
daily_report.py — progressing a schedule from what the foremen wrote down.

WHY
  A schedule's actuals are usually typed by whoever updates P6, from memory or
  from a meeting. Meanwhile the foremen have already written down what they did
  and how many people did it, every day, in their own words: "hanging high
  steel", "setting isolators for gens", "2nd Fl North Rack building for Bus
  Duct segment C". That is the same vocabulary the activities are named in,
  because the same people named both.

  So the notes are matched to activities and the schedule is progressed from
  them. Nothing is invented: an activity only moves if a foreman wrote
  something that names it.

WHY IT REPORTS BEFORE IT WRITES
  Matching prose to activity names is inherently uncertain, and being wrong is
  expensive in a specific way: an activity marked complete stops asking to be
  done. So every proposal carries its score, the line that produced it and the
  runner-up it beat, and nothing is applied until somebody has read them.

  An ambiguous line is reported unmatched rather than given to its best
  candidate. "Install conduit" is true of four hundred activities and is not
  evidence about any one of them.

THE DATA HAS TO BE CLEANED FIRST
  These reports arrive with duplicate submissions, ten-times-too-large hour
  counts and test rows in them. On the job this was built for, 59% of one
  project's reported hours were in three bad rows. Progressing a schedule from
  uncleaned reports means booking that error into the labour.
"""

import collections
import datetime as _dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

# Columns, by the header text the reporting tool writes.
NEEDED = ("Report Date", "Foreman", "Project", "Number of Workers",
          "Total Labor Hours", "Work Status", "Tasks")

# More than this in a day, per person, is a keying slip rather than a shift.
# Twelve-hour days and weekend doubles are normal here; a hundred is not.
MAX_HOURS_PER_WORKER = 16.0

# The reporting tool appends "(2)" to a resubmitted report and keeps both.
RESUBMITTED = re.compile(r"\(\d+\)\s*$")

# Words too common in construction prose to be evidence about one activity.
STOP = {
    "the", "and", "for", "with", "from", "this", "that", "was", "были",
    "all", "any", "out", "off", "per", "via", "into", "onto", "over",
    "work", "works", "working", "worked", "continue", "continued",
    "complete", "completed", "completing", "start", "started", "finish",
    "finished", "install", "installed", "installing", "installation",
    "area", "areas", "side", "level", "floor", "building", "job", "site",
    "misc", "various", "etc", "prep", "prepped", "run", "running", "ran",
}

# A term that names a kind of work. Weighted up, because "isolator" tells you
# what was done and "north" tells you only where.
TRADE = {
    "steel", "hanger", "hangers", "tray", "conduit", "raceway", "isolator",
    "isolators", "epoxy", "lbb", "msb", "msg", "gear", "panel", "panelboard",
    "switchgear", "transformer", "xfmr", "generator", "engine", "wire",
    "wiring", "pull", "pulls", "terminate", "termination", "terminations",
    "lighting", "lights", "sprinkler", "drywall", "framing", "paint",
    "caulking", "caulk", "grounding", "ground", "duct", "busduct", "bus",
    "rack", "racks", "vesda", "crah", "crahs", "skid", "skids", "curb",
    "louver", "louvers", "penetration", "penetrations", "rough", "trim",
    "tie", "ties", "feeder", "feeders", "emt", "pvc", "fiberglass", "ladder",
    "megger", "meg", "startup", "burn", "burnin", "inspection", "inspections",
}

_WORD = re.compile(r"[A-Za-z]{3,}")


def terms(text: Any) -> set:
    """The words in a phrase that could identify a piece of work."""
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(str(text or "")))
            if w not in STOP}


def _weights(project) -> Dict[str, float]:
    """
    How much each word narrows things down.

    Inverse document frequency over the activity names: "conduit" appears on
    hundreds of activities and barely distinguishes them, "isolator" on a
    handful and almost identifies one. Without this, a line saying "install
    conduit" scored against four hundred activities equally and the tie was
    broken by whichever came first in the file.
    """
    n = max(1, len(project.activities))
    freq: Dict[str, int] = collections.Counter()
    for a in project.activities:
        for t in terms(a.name):
            freq[t] += 1
    out = {}
    for t, f in freq.items():
        w = math.log(n / f)
        out[t] = w * (1.6 if t in TRADE else 1.0)
    return out


def _paths(project) -> Dict[str, set]:
    """
    Every activity's folder path, as words.

    This is where the schedule keeps LOCATION, and location is not optional:
    seventeen activities are named exactly "Install High Steel", so the work
    name alone cannot identify one of them. The foremen write the location in
    prose — "Middle Roof", "2nd Fl North", "in the MV Room" — and matching that
    against the folder is what tells those seventeen apart.

    Scored without this, a note about ladder tray at the middle roof landed on
    a generator room, and one about relocating a tower crane's generator landed
    on an electrical room skid. Both were the lexically best answer and both
    were wrong.
    """
    nodes = {n.uid: n for n in (project.wbs_nodes or [])}
    out: Dict[str, set] = {}
    for a in project.activities:
        words: set = set()
        node = nodes.get(getattr(a, "wbs_uid", None))
        while node is not None:
            words |= terms(node.name)
            words |= {w for w in re.findall(r"\d{2,3}", str(node.name or ""))}
            node = nodes.get(node.parent_uid) if node.parent_uid else None
        out[id(a)] = words
    return out


def _place(text: str) -> set:
    """The words and numbers in a note that say WHERE, not what."""
    low = str(text or "").lower()
    got = {w for w in _WORD.findall(low)} - TRADE
    got |= set(re.findall(r"\b\d{2,3}\b", low))
    # Written many ways: "2nd Fl North", "2nd floor", "middle roof", "MV room".
    for pat, word in ((r"\bmid(dle)?\b", "middle"), (r"\broof\b", "roof"),
                      (r"\bpent\s*house\b", "penthouse"),
                      (r"\bgallery\b", "gallery"), (r"\bcorridor\b", "corridor"),
                      (r"\bdata\s*hall\b", "hall"), (r"\bmv\b", "mv"),
                      (r"\bhv\b", "hv"), (r"\bgen(erator)?\b", "gen"),
                      (r"\bcup\b", "cup"), (r"\bslab\b", "slab")):
        if re.search(pat, low):
            got.add(word)
    return got - STOP


def read(path: str, project: Optional[str] = None,
         sheet: str = "Daily Reports") -> Dict[str, Any]:
    """
    The reports, cleaned, with what was removed and why.

    Cleaning is not optional and not silent: `removed` lists every row dropped
    or corrected, because a schedule progressed from uncleaned reports books
    their errors into the labour and nothing downstream says so.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    header = [("" if c.value is None else str(c.value).strip()) for c in ws[1]]
    idx = {h: i for i, h in enumerate(header) if h}
    missing = [c for c in NEEDED if c not in idx]
    if missing:
        raise ValueError(f"the report sheet has no {', '.join(missing)} column")

    def cell(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    seen: set = set()
    reported_hours = 0.0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        proj = str(cell(row, "Project") or "").strip()
        if project and proj.replace(" ", "").upper() != project.replace(" ", "").upper():
            continue
        name = str(cell(row, "Report Name") or "")
        date = _date(cell(row, "Report Date"))
        foreman = str(cell(row, "Foreman") or "").strip()
        workers = _float(cell(row, "Number of Workers"))
        hours = _float(cell(row, "Total Labor Hours"))
        tasks = str(cell(row, "Tasks") or "")
        reported_hours += hours

        if RESUBMITTED.search(name):
            removed.append({**_stub(date, foreman, workers, hours),
                            "why": "the tool's own resubmission of an earlier "
                                   "report, kept alongside it"})
            continue
        key = (foreman, date, workers, hours)
        if key in seen:
            removed.append({**_stub(date, foreman, workers, hours),
                            "why": "identical to a report already counted"})
            continue
        seen.add(key)
        if not workers and not hours:
            removed.append({**_stub(date, foreman, workers, hours),
                            "why": "no workers and no hours — a test row"})
            continue
        fixed = None
        if workers and hours / workers > MAX_HOURS_PER_WORKER:
            fixed = hours / 10.0
            removed.append({**_stub(date, foreman, workers, hours),
                            "why": f"{hours / workers:.0f} hours per worker in "
                                   f"one day; read as {fixed:.0f}",
                            "corrected_to": fixed})
            hours = fixed
        if not date:
            removed.append({**_stub(date, foreman, workers, hours),
                            "why": "no report date"})
            continue
        kept.append({"date": date, "foreman": foreman, "project": proj,
                     "workers": workers, "hours": hours,
                     "status": str(cell(row, "Work Status") or "").strip(),
                     "lines": [ln.strip() for ln in tasks.splitlines()
                               if ln.strip()]})

    days = sorted({r["date"] for r in kept})
    return {"reports": kept, "removed": removed,
            "counts": {"kept": len(kept), "removed": len(removed),
                       "hours_reported": reported_hours,
                       "hours_clean": sum(r["hours"] for r in kept),
                       "lines": sum(len(r["lines"]) for r in kept),
                       "first_day": days[0] if days else None,
                       "last_day": days[-1] if days else None}}


def _stub(date, foreman, workers, hours):
    return {"date": date, "foreman": foreman, "workers": workers,
            "hours": hours}


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _date(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _window(activity) -> Tuple[Optional[_dt.date], Optional[_dt.date]]:
    return (_date(getattr(activity, "planned_start", None)),
            _date(getattr(activity, "planned_finish", None)))


def match(project, reports: Dict[str, Any], near_days: int = 45,
          min_score: float = 2.5, margin: float = 1.25) -> Dict[str, Any]:
    """
    Which activity each note is about, where that can be said at all.

    Three things have to hold before a line is taken as evidence:

      the words have to narrow it down    — scored by how rare each shared
                                            term is across the activity names
      the activity has to be plausibly    — a note written in September is not
      current                               evidence about work scheduled for
                                            the following summer
      the winner has to beat the runner-up — "install conduit" fits four
                                            hundred activities; a near-tie
                                            means the line does not identify
                                            one, and it is reported unmatched
                                            rather than given to whichever
                                            came first in the file
    """
    weights = _weights(project)
    folders = _paths(project)
    # Every location word the schedule actually uses. A note saying "roof" is
    # only usable as a filter if this schedule HAS roof folders; saying
    # "corner" is not, because no folder is named for a corner.
    vocabulary: set = set()
    for words in folders.values():
        vocabulary |= words
    candidates = [a for a in project.activities
                  if (str(getattr(a, "status", "") or "") != "Completed")]

    matched: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for rep in reports["reports"]:
        for line in rep["lines"]:
            tl = terms(line)
            if not tl:
                unmatched.append({**_line_stub(rep, line),
                                  "why": "no identifying words"})
                continue
            where = _place(line)
            # The places this note names that the schedule also files work
            # under. Where the note says one, it is a REQUIREMENT and not a
            # bonus: "Run Ladder Tray at Middle Roof" scored 12.6 against a
            # generator room on its work words alone and beat the roof activity
            # at 9.4, because a missing location cost nothing. Work in the roof
            # is not work in Gen 326, however well the verbs line up.
            required = where & vocabulary
            scored = []
            for a in candidates:
                s, f = _window(a)
                if s and f:
                    gap = min(abs((rep["date"] - s).days),
                              abs((rep["date"] - f).days))
                    if not (s - _dt.timedelta(days=near_days) <= rep["date"]
                            <= f + _dt.timedelta(days=near_days)) and gap > near_days:
                        continue
                shared = tl & terms(a.name)
                if not shared:
                    continue
                place = where & folders.get(id(a), set())
                if required and not (place & required):
                    continue
                score = sum(weights.get(t, 0.0) for t in shared) + 3.0 * len(place)
                scored.append((score, a, shared, place))
            if not scored:
                unmatched.append({
                    **_line_stub(rep, line),
                    "why": (f"nothing current is both this work and in "
                            f"{'/'.join(sorted(required))}" if required else
                            "nothing current shares its words")})
                continue
            scored.sort(key=lambda x: -x[0])
            best = scored[0]
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if best[0] < min_score:
                unmatched.append({**_line_stub(rep, line),
                                  "why": f"best match too weak ({best[0]:.1f})"})
                continue
            if runner and best[0] < runner * margin:
                unmatched.append({
                    **_line_stub(rep, line),
                    "why": f"{sum(1 for s, _, _, _ in scored if s >= runner)} "
                           f"activities fit about as well"})
                continue
            matched.append({**_line_stub(rep, line),
                            "activity_id": best[1].activity_id,
                            "activity_name": best[1].name,
                            "folder": " / ".join(
                                sorted(folders.get(id(best[1]), set()))[:6]),
                            "score": round(best[0], 2),
                            "runner_up": round(runner, 2),
                            "on": sorted(best[2]),
                            "place": sorted(best[3])})
    return {"matched": matched, "unmatched": unmatched,
            "counts": {"lines": len(matched) + len(unmatched),
                       "matched": len(matched),
                       "unmatched": len(unmatched),
                       "activities": len({m["activity_id"] for m in matched})}}


def _line_stub(rep, line):
    return {"date": rep["date"], "foreman": rep["foreman"],
            "status": rep["status"], "hours": rep["hours"], "line": line}


def progress(project, matches: Dict[str, Any], apply: bool = False,
             data_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Move the matched activities, reading the foreman's own status.

    "Work Completed as Planned" on a line means that piece finished, so the
    activity is completed on the day it was reported. "Work Partially
    Completed" means it is under way, and an activity already under way is not
    pushed backwards by a later partial note.

    A note dated before an activity's own start does not make it start earlier;
    the report says work happened, not that the plan was wrong.
    """
    by_id = {str(getattr(a, "activity_id", "") or ""): a
             for a in project.activities}
    per_act: Dict[str, List[Dict]] = collections.defaultdict(list)
    for m in matches["matched"]:
        per_act[m["activity_id"]].append(m)

    changes = []
    for aid, notes in sorted(per_act.items()):
        act = by_id.get(aid)
        if act is None:
            continue
        notes.sort(key=lambda m: m["date"])
        last = notes[-1]
        done = any("completed as planned" in (m["status"] or "").lower()
                   for m in notes)
        was = str(getattr(act, "status", "") or "")
        change = {"activity_id": aid, "name": act.name, "was": was,
                  "notes": len(notes),
                  "first_seen": notes[0]["date"], "last_seen": last["date"],
                  "now": "Completed" if done else "In Progress"}
        if was == "Completed":
            change["now"] = "Completed"
            change["skipped"] = "already complete"
        changes.append(change)
        if not apply or was == "Completed":
            continue
        start = _date(getattr(act, "planned_start", None))
        act.actual_start = (min(start, notes[0]["date"]) if start
                            else notes[0]["date"]).isoformat()
        if done:
            act.status = "Completed"
            act.percent_complete = 100.0
            act.actual_finish = last["date"].isoformat()
            act.remaining_duration = 0.0
        else:
            act.status = "In Progress"
            if not (0 < float(getattr(act, "percent_complete", 0) or 0) < 100):
                act.percent_complete = 50.0
            act.remaining_duration = (float(getattr(act, "planned_duration", 0) or 0)
                                      * (1 - act.percent_complete / 100.0))
    if apply:
        if data_date:
            project.data_date = data_date
        project.build_lookups()
    return {"changes": changes,
            "counts": {"activities": len(changes),
                       "completed": sum(1 for c in changes
                                        if c["now"] == "Completed"
                                        and "skipped" not in c),
                       "in_progress": sum(1 for c in changes
                                          if c["now"] == "In Progress"),
                       "already_complete": sum(1 for c in changes
                                               if "skipped" in c)}}


def describe(reports: Dict[str, Any], matches: Optional[Dict] = None,
             limit: int = 10) -> str:
    c = reports["counts"]
    lines = [f"{c['kept']} reports kept, {c['removed']} removed or corrected.",
             f"hours as reported {c['hours_reported']:,.0f} -> "
             f"{c['hours_clean']:,.0f} once cleaned.",
             f"{c['lines']} task lines, {c['first_day']} .. {c['last_day']}."]
    if reports["removed"]:
        lines.append("\nRemoved or corrected:")
        lines += [f"  {r['date']} {r['foreman'][:18]:20} "
                  f"{r['workers']:.0f}w {r['hours']:.0f}h — {r['why']}"
                  for r in reports["removed"][:limit]]
    if matches:
        m = matches["counts"]
        lines.append(f"\n{m['matched']} of {m['lines']} lines name an activity "
                     f"({m['activities']} distinct). {m['unmatched']} do not.")
    return "\n".join(lines)
