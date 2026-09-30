# -*- coding: utf-8 -*-
"""
room_template.py — one room's flow, taken from the job that has it right.

WHY
  A contractor's printed schedule says what a generator room needs in fifteen
  lines: overhead rough-in, curb, epoxy, louvers, set engine, pull, terminate,
  start up. That is the GC's view, and it is fine for a GC.

  It is not a schedule an electrical foreman can work to. The same room on the
  sister job is twenty-three lines, because that is how many separable pieces
  of electrical work there are in it — high steel, hangers, ladder tray, branch
  raceway, LBB to LBB, LBB to MSB, LBB to generator, MEG/QC, terminate — and
  which of them are somebody else's (`**WBO`) so nobody loads crew against
  them.

  So the flow is lifted off the schedule that has the detail and laid into the
  one that does not, room by room.

HOW THE DATES ARE HANDLED
  Each room is anchored at the start it already has, and the reference
  durations and gaps are kept exactly. The contractor's window for a room runs
  about 2.2x longer than the work in it actually takes, so the transplanted
  flow finishes partway through that window and the rest reads as float —
  which is what it is.

  The alternative was stretching durations to fill the window, which would have
  turned a three-day pull into a seven-day one. Hours that are not real work
  content are worse than an early finish, because the early finish is visible
  and the inflated hours are not.

WHAT IT WILL NOT DO
  Replace a room that has progress on it. An activity somebody has started is
  a fact about the job, and the flow it belongs to cannot be swapped out from
  under it without throwing that away.
"""

import collections
import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

# A day's work, in the hours P6 stores durations in.
HOURS_PER_DAY = 8.0

# Work somebody else does. Named in the folder on some room types and in the
# activity name on others, so both are checked — a `**WBO` line that gets
# crew loaded against it is the reason this distinction is carried at all.
WBO = re.compile(r"\*+\s*WBO|\bWBO\b", re.I)


class RoomKind:
    """
    How one type of room is recognised and named, in both schedules.

    Held as data because the three types genuinely differ — the reference job
    puts a generator room's scope split in sibling folders suffixed `- JER` and
    `- WBO`, an MV room's in folders PREFIXED `WBO `, and an HV room's only in
    the activity names. Mirroring it means mirroring that too, inconsistencies
    and all, rather than imposing a tidiness the job does not have.
    """

    def __init__(self, name, source_folder, target_id, folder_own, folder_wbo,
                 name_suffix=None, split=True):
        self.name = name
        self.source_folder = re.compile(source_folder, re.I)
        self.target_id = re.compile(target_id)
        self.folder_own = folder_own
        self.folder_wbo = folder_wbo
        self.name_suffix = name_suffix
        self.split = split


# The job code at the front of every activity id — MDC1, MDC2, MDC3. Matched
# rather than hardcoded, so the same three templates serve every sister job
# instead of needing a copy of this table per project.
_JOB = r"[A-Z][A-Z0-9]{1,9}"

KINDS = {
    "GEN": RoomKind(
        "GEN", r"\bGen\s*(\d{3})\b", rf"^{_JOB}\.GEN\.(\d+)\.",
        "Gen {room} - JER", "Gen {room} - WBO", " (Gen {room})"),
    "MV": RoomKind(
        "MV", r"\bMV\s*(\d{3})\b", rf"^{_JOB}\.PH\d\.MVR?\.(\d+)\.",
        "MV {room}", "WBO MV {room}"),
    "HV": RoomKind(
        # The reference job keeps an HV room in one folder and marks the other
        # trades in the name, so there is no split to mirror.
        "HV", r"\bHV\s*(\d{3})\b", rf"^{_JOB}\.PH\d\.HVR?\.?(\d+)",
        "HV {room}", "HV {room}", split=False),
}


def _d(value) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _is_work(day: _dt.date, weekdays) -> bool:
    return day.weekday() in weekdays


def _snap(day: _dt.date, weekdays) -> _dt.date:
    while not _is_work(day, weekdays):
        day += _dt.timedelta(days=1)
    return day


def _add_work_days(start: _dt.date, days: int, weekdays) -> _dt.date:
    """Add whole working days. Day 0 is the start day itself."""
    day = _snap(start, weekdays)
    added = 0
    while added < days:
        day += _dt.timedelta(days=1)
        if _is_work(day, weekdays):
            added += 1
    return day


def _work_days_between(a: _dt.date, b: _dt.date, weekdays) -> int:
    """Working days from a to b, a itself counting as zero."""
    if b <= a:
        return 0
    n, day = 0, _snap(a, weekdays)
    while day < b:
        day += _dt.timedelta(days=1)
        if _is_work(day, weekdays):
            n += 1
    return n


def _folder_of(project, activity) -> str:
    node = {n.uid: n for n in (project.wbs_nodes or [])}.get(
        getattr(activity, "wbs_uid", None))
    return (node.name or node.code or "") if node else ""


def extract(source, kind: str, weekdays=frozenset({0, 1, 2, 3, 4})) -> Dict[str, Any]:
    """
    The flow of one kind of room, as the reference schedule has it.

    Taken from the room with the most activities on it, since a room that was
    scheduled in less detail than its siblings is not the template anybody
    means. Each step keeps its duration, whose scope it is, and how many
    working days after the room's start it begins.
    """
    k = KINDS[kind]
    rooms: Dict[str, List[Any]] = collections.defaultdict(list)
    for a in source.activities:
        folder = _folder_of(source, a)
        m = k.source_folder.search(folder)
        if m:
            rooms[m.group(1)].append(a)
    if not rooms:
        raise ValueError(f"no {kind} rooms found in the reference schedule")

    room = max(rooms, key=lambda r: len(rooms[r]))
    acts = [a for a in rooms[room] if _d(getattr(a, "planned_start", None))]
    anchor = min(_d(a.planned_start) for a in acts)

    steps = []
    for a in sorted(acts, key=lambda x: (_d(x.planned_start), x.activity_id or "")):
        folder = _folder_of(source, a)
        label = re.sub(re.escape(f"(Gen {room})"), "", str(a.name or "")).strip()
        label = re.sub(rf"\b(?:MV|HV|GEN)\s*{room}\b", "", label)
        label = re.sub(r"\s*-\s*$|^\s*-\s*", "", label).strip()
        steps.append({
            "label": label or str(a.name or ""),
            "hours": float(getattr(a, "planned_duration", 0) or 0),
            "wbo": bool(WBO.search(folder) or WBO.search(str(a.name or ""))),
            "offset": _work_days_between(anchor, _d(a.planned_start), weekdays),
        })
    return {"kind": kind, "from_room": room, "steps": steps,
            "span_days": max(s["offset"] for s in steps) if steps else 0}


def rooms_in(target, kind: str) -> Dict[str, List[Any]]:
    """Every room of this kind in the schedule to be rewritten, by number."""
    k = KINDS[kind]
    out: Dict[str, List[Any]] = collections.defaultdict(list)
    for a in target.activities:
        m = k.target_id.match(str(getattr(a, "activity_id", "") or ""))
        if m:
            out[m.group(1)].append(a)
    return out


def plan(target, template: Dict[str, Any]) -> Dict[str, Any]:
    """
    What each room would become, without changing anything.

    `blocked` is the number that decides whether this is safe to apply: a room
    carrying progress is not rewritten, because an activity somebody started is
    a fact about the job and the flow it sits in cannot be swapped out from
    under it.
    """
    kind = template["kind"]
    rooms = rooms_in(target, kind)
    ready, blocked = {}, {}
    for room, acts in sorted(rooms.items()):
        started = [a for a in acts
                   if (str(getattr(a, "status", "") or "") != "Not Started")]
        starts = [_d(getattr(a, "planned_start", None)) for a in acts]
        starts = [s for s in starts if s]
        if not starts:
            blocked[room] = "no dates to anchor to"
            continue
        if started:
            blocked[room] = (f"{len(started)} activit"
                             f"{'y' if len(started) == 1 else 'ies'} already "
                             f"started")
            continue
        finishes = [_d(getattr(a, "planned_finish", None)) for a in acts]
        finishes = [f for f in finishes if f]
        ready[room] = {"anchor": min(starts), "was": len(acts),
                       "will_be": len(template["steps"]),
                       "old_finish": max(finishes) if finishes else None}
    return {"kind": kind, "ready": ready, "blocked": blocked,
            "counts": {"rooms": len(rooms), "ready": len(ready),
                       "blocked": len(blocked),
                       "activities_before": sum(len(v) for v in rooms.values()),
                       "activities_after": len(ready) * len(template["steps"])}}


def apply_template(target, template: Dict[str, Any],
                   weekdays=frozenset({0, 1, 2, 3, 4}),
                   id_prefix: Optional[str] = None,
                   fit: str = "window") -> Dict[str, Any]:
    """
    Rewrite every ready room to the template, anchored where it already starts.

    `fit` decides what happens to the GAPS between steps. Durations are never
    touched either way — hours that are not real work content are worse than a
    date that moved, because the date is visible and the hours are not.

      "window"  scale the gaps so the flow also FINISHES about where the room
                already finishes. This is the default, and it is the default
                because of what the alternative measured: the reference job's
                Gen 315 carries a three-month wait in the middle of it for
                somebody else's epoxy and IMP wall, so its span is 104 working
                days while the work in it is about forty. Laid in unscaled,
                25 of 28 generator rooms finished LATER than the contractor's
                own dates — a median of 44 days late, up to 72 — which is the
                opposite of keeping close to the schedule being matched.

      "natural" keep the reference gaps exactly. Honest about the reference
                job's own rhythm, and right when the two schedules are paced
                alike, but it moves finishes wherever they are not.

    Returns the plan it acted on, with each room's new finish and how far that
    is from the old one, so a date that moved is stated rather than implied.
    """
    from .schedule_model import Activity, WBSNode

    k = KINDS[template["kind"]]
    result = plan(target, template)
    rooms = rooms_in(target, template["kind"])
    by_uid = {n.uid: n for n in target.wbs_nodes}

    # The job code this schedule already uses, rather than one passed in and
    # got wrong: a room full of MDC3 ids inside MDC-2 imports as a set of
    # activities nobody can find.
    if not id_prefix:
        heads = [str(getattr(a, "activity_id", "") or "").split(".")[0]
                 for a in target.activities]
        heads = [h for h in heads if h]
        id_prefix = (collections.Counter(heads).most_common(1)[0][0]
                     if heads else "JOB")

    # Keep each room where it already sits in the tree; only its contents
    # change. A transplant that also moves the room loses the one thing the
    # rebuilt hierarchy got right.
    parent_of: Dict[str, Optional[str]] = {}
    for room in result["ready"]:
        node = by_uid.get(getattr(rooms[room][0], "wbs_uid", None))
        parent_of[room] = node.parent_uid if node else None

    drop = {id(a) for room in result["ready"] for a in rooms[room]}
    target.activities = [a for a in target.activities if id(a) not in drop]

    cal = next((c.uid for c in (target.calendars or [])), None)
    span = max(1, int(template.get("span_days") or 1))
    new_nodes: List[Any] = []
    for room, info in sorted(result["ready"].items()):
        folders = {}
        for role, tmpl in (("own", k.folder_own),
                           ("wbo", k.folder_wbo if k.split else k.folder_own)):
            label = tmpl.format(room=room)
            existing = next((n for n in target.wbs_nodes + new_nodes
                             if (n.name or "") == label), None)
            if existing is None:
                existing = WBSNode(uid=f"RT-{template['kind']}-{room}-{role}",
                                   name=label,
                                   code=re.sub(r"\W+", "", label).upper()[:12],
                                   parent_uid=parent_of[room])
                new_nodes.append(existing)
            folders[role] = existing.uid

        # How much the gaps have to give to land on this room's own finish.
        scale = 1.0
        if fit == "window" and info["old_finish"]:
            room_span = _work_days_between(info["anchor"], info["old_finish"],
                                           weekdays)
            if room_span > 0:
                scale = room_span / span
        info["gap_scale"] = round(scale, 3)

        finishes = []
        for i, step in enumerate(template["steps"]):
            offset = int(round(step["offset"] * scale))
            start = _add_work_days(info["anchor"], offset, weekdays)
            days = max(1, int(round(step["hours"] / HOURS_PER_DAY)))
            finish = _add_work_days(start, days - 1, weekdays)
            finishes.append(finish)
            label = step["label"]
            if k.name_suffix:
                label += k.name_suffix.format(room=room)
            elif not re.search(rf"\b{room}\b", label):
                label = f"{label} - {k.name.upper()} {room}"
            target.activities.append(Activity(
                uid=f"RT-{template['kind']}-{room}-{i:03d}",
                activity_id=f"{id_prefix}.{k.name}.{room}.{(i + 1) * 10 + 1000}",
                name=label,
                wbs_uid=folders["wbo" if step["wbo"] else "own"],
                calendar_uid=cal,
                status="Not Started",
                planned_duration=step["hours"],
                remaining_duration=step["hours"],
                planned_start=start.isoformat(),
                planned_finish=finish.isoformat()))
        info["new_finish"] = max(finishes) if finishes else None
        info["finish_moved_days"] = (
            (info["new_finish"] - info["old_finish"]).days
            if info["old_finish"] and info["new_finish"] else None)

    target.wbs_nodes = list(target.wbs_nodes) + new_nodes
    target.build_lookups()
    return result


def describe(result: Dict[str, Any], limit: int = 10) -> str:
    c = result["counts"]
    lines = [f"{result['kind']} rooms: {c['ready']} rewritten, "
             f"{c['blocked']} left alone.",
             f"{c['activities_before']} activities -> {c['activities_after']}."]
    if result["blocked"]:
        lines.append("\nLeft alone:")
        lines += [f"  {room}: {why}"
                  for room, why in list(result["blocked"].items())[:limit]]
    return "\n".join(lines)
