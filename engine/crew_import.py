# -*- coding: utf-8 -*-
"""
crew_import.py — bringing a headcount in from the labour loading sheet.

WHY
  The crew counts were worked out once, in a spreadsheet, against a schedule
  whose activity IDs have since been renumbered by hand. Typing 694 of them
  back in is a day's work and a day's worth of chances to fat-finger one.

  Matching on ID alone reaches 176 of the 694, because the renumbering moved
  most of them. Matching on name alone is worse than useless: "Overhead
  Electrical/FA" appears in every generator room, so a name match picks an
  arbitrary one of twenty-eight and looks like it worked.

  What makes it tractable is that the sheet is an OUTLINE. Column A is the
  level and a row with no activity name is a folder, so every row carries the
  folder path it sits under — and folder plus name is unique where name alone
  is not.

WHAT IT WILL NOT DO
  Guess. A row that reaches more than one activity, or none, is reported as
  unmatched rather than applied to a best candidate. The whole value of this
  is that the numbers are real; a headcount written onto the wrong activity is
  worse than a blank one, because a blank one is visibly missing.

  It does not overwrite a count already on an activity unless told to. Where
  the sheet and the schedule disagree, the disagreement is reported so someone
  can decide which is right.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

CREW_FIELD = "Number of Electricians"

# Column layout of the labour loading sheet, 0-based.
_C_LEVEL, _C_ID, _C_NAME, _C_CREW = 0, 1, 2, 6

# Suffixes a folder carries to say WHOSE scope it is. "JER" is this
# contractor; "WBO" is work by others. The sheet names the room without
# either, so they are stripped for matching — but which one a row lands in
# is not arbitrary, and _prefer_own_scope keeps it out of somebody else's.
_SCOPE_SUFFIX = re.compile(r"\s*-\s*(JER|WBO)\s*$", re.I)
_OWN_SCOPE = re.compile(r"\bJER\b", re.I)
_OTHER_SCOPE = re.compile(r"\bWBO\b", re.I)


def _norm(s: Any) -> str:
    """Whitespace-collapsed, upper-cased, for comparing names and folders."""
    return " ".join(str(s or "").split()).upper()


def _folder_key(s: Any) -> str:
    """A folder name with its scope suffix removed: 'Gen 314 - JER' -> 'GEN 314'."""
    return _norm(_SCOPE_SUFFIX.sub("", str(s or "")))


def _count(v: Any) -> Optional[float]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def read_sheet(path: str, sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Every row of the sheet that carries a headcount, with its folder path.

    The outline is rebuilt as it reads: a row with a level and no activity
    name is a folder at that level, and it replaces anything deeper that came
    before it.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]

    stack: Dict[int, str] = {}
    out: List[Dict[str, Any]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) <= _C_CREW:
            continue
        try:
            level = int(str(row[_C_LEVEL]).strip())
        except (TypeError, ValueError):
            continue
        label = str(row[_C_ID]).strip() if row[_C_ID] is not None else ""
        name = str(row[_C_NAME]).strip() if row[_C_NAME] is not None else ""
        if not name:
            # A folder. Anything deeper belonged to the previous one.
            stack[level] = label
            for deeper in [k for k in stack if k > level]:
                del stack[deeper]
            continue
        crew = _count(row[_C_CREW])
        if crew is None:
            continue
        out.append({"activity_id": label, "name": name, "crew": crew,
                    "folder_path": [stack[k] for k in sorted(stack)]})
    wb.close()
    return out


def _prefer_own_scope(cands: List[Any]) -> List[Any]:
    """
    Of several candidates, this contractor's own.

    A generator room is split into the room, its JER folder and its WBO
    folder. A headcount for our electricians belongs in ours; putting it in
    Work By Others loads somebody else's crew into our profile.
    """
    own = [a for a in cands if _OWN_SCOPE.search(getattr(a, "_folder_raw", ""))]
    if own:
        return own
    not_theirs = [a for a in cands
                  if not _OTHER_SCOPE.search(getattr(a, "_folder_raw", ""))]
    return not_theirs or cands


def _index(project) -> Tuple[Dict, Dict, Dict]:
    """By activity id, by (folder, name), and by name alone."""
    wbs = {n.uid: n for n in (project.wbs_nodes or [])}
    by_id, by_folder_name, by_name = {}, {}, {}
    for a in project.activities:
        aid = str(getattr(a, "activity_id", "") or "").strip()
        if aid:
            by_id[aid] = a
        node = wbs.get(getattr(a, "wbs_uid", None))
        raw = (node.name or node.code or "") if node else ""
        # Stashed so scope preference can read it without walking again.
        setattr(a, "_folder_raw", raw)
        by_folder_name.setdefault((_folder_key(raw), _norm(a.name)), []).append(a)
        by_name.setdefault(_norm(a.name), []).append(a)
    return by_id, by_folder_name, by_name


def plan(project, rows: List[Dict[str, Any]],
         overwrite: bool = False) -> Dict[str, Any]:
    """
    What each sheet row reaches, without changing anything.

    Every row lands in exactly one bucket: applied, unchanged (the schedule
    already says the same), conflict (it says something else and overwrite is
    off), or unmatched with the reason.
    """
    by_id, by_folder_name, by_name = _index(project)
    applied, unchanged, conflict, unmatched = [], [], [], []

    for r in rows:
        aid, name, crew = r["activity_id"], r["name"], r["crew"]
        folder = r["folder_path"][-1] if r["folder_path"] else ""
        act, how = None, None

        if aid and aid in by_id:
            act, how = by_id[aid], "activity id"
        else:
            cands = _prefer_own_scope(
                by_folder_name.get((_folder_key(folder), _norm(name)), []))
            if len(cands) == 1:
                act, how = cands[0], "folder + name"
            else:
                loose = _prefer_own_scope(by_name.get(_norm(name), []))
                if len(loose) == 1:
                    act, how = loose[0], "name"
                else:
                    unmatched.append({
                        **r,
                        "why": (f"{len(loose)} activities share this name"
                                if loose else
                                "no activity with this id, folder or name")})
                    continue

        current = str((getattr(act, "udfs", None) or {}).get(CREW_FIELD, "")).strip()
        row = {"activity_id": act.activity_id, "name": act.name,
               "crew": crew, "was": current, "matched_by": how,
               "sheet_id": aid}
        if current and _count(current) == crew:
            unchanged.append(row)
        elif current and not overwrite:
            conflict.append(row)
        else:
            applied.append(row)

    return {"applied": applied, "unchanged": unchanged,
            "conflict": conflict, "unmatched": unmatched,
            "counts": {"applied": len(applied), "unchanged": len(unchanged),
                       "conflict": len(conflict), "unmatched": len(unmatched),
                       "rows": len(rows)}}


def apply(project, rows: List[Dict[str, Any]],
          overwrite: bool = False) -> Dict[str, Any]:
    """Write the headcounts the plan says are safe to write."""
    result = plan(project, rows, overwrite=overwrite)
    by_id = {str(getattr(a, "activity_id", "") or "").strip(): a
             for a in project.activities}
    for row in result["applied"]:
        act = by_id.get(str(row["activity_id"]).strip())
        if act is None:
            continue
        if getattr(act, "udfs", None) is None:
            act.udfs = {}
        crew = row["crew"]
        act.udfs[CREW_FIELD] = str(int(crew)) if crew == int(crew) else str(crew)
    return result


def describe(result: Dict[str, Any], limit: int = 10) -> str:
    """The plan as something a person can act on."""
    c = result["counts"]
    lines = [f"{c['rows']} headcounts in the sheet: "
             f"{c['applied']} to write, {c['unchanged']} already right, "
             f"{c['conflict']} disagree, {c['unmatched']} not found."]
    if result["conflict"]:
        lines.append("\nThe sheet and the schedule disagree "
                     "(nothing written unless you say overwrite):")
        lines += [f"  {r['activity_id']:26} schedule={r['was']}  sheet={r['crew']:.0f}"
                  for r in result["conflict"][:limit]]
    if result["unmatched"]:
        lines.append("\nNot found — these need a person:")
        lines += [f"  {r['activity_id']:26} {r['crew']:.0f}  {r['name'][:38]}"
                  f"  ({r['why']})" for r in result["unmatched"][:limit]]
    return "\n".join(lines)
