# -*- coding: utf-8 -*-
"""
wbs_rebuild.py — giving a parsed schedule its hierarchy back.

WHY
  A schedule that came out of a PDF has no WBS. Exhibit S parses into 3,400
  real activities and 281 folders that are all siblings under one root, mixed
  in with the page furniture the parser could not tell from work: "Data Date:",
  "Printed Date:", "Holder Construction", and the Gantt timescale header, which
  collected 375 activities on its way past.

  The activities themselves are fine. Dates, durations, statuses, and — this is
  the part that matters — activity IDs whose segments already say where each
  one belongs. MDC3.PH1.LLE.1000 is Phase 1, low-level electrical. The folder
  it landed in says nothing; the ID says everything.

  So the tree is rebuilt from the IDs, and the folders are used only for their
  NAMES, which are the real ones a person typed in P6 before it was printed.

WHAT IT WILL NOT DO
  Lose an activity. Every one is placed or reported, and `plan` refuses to
  claim success while any are unaccounted for. A rebuild that silently drops
  forty rows of work is worse than no rebuild, because the total still looks
  about right.

  Invent logic. A parsed schedule has no relationships and this does not make
  any up — it builds the containers, nothing else.
"""

import collections
import re
from typing import Any, Dict, List, Optional, Tuple

# Page furniture the PDF parser could not distinguish from work. Matched on
# the folder NAME, since the codes it derived from those names are worse.
FURNITURE = re.compile(
    r"^(data date|printed date|holder constr|exhibit\b|duration \w+$|"
    r"jan\s+feb|page \d|actual level|remaining level|critical remain|"
    r"actual work|remaining work|milestone$|summary$|baseline\b)", re.I)

# The client calls its buildings Datacenter A, B and C. The schedule is worked
# in phases, and carrying both names for one thing is how "which datacenter is
# phase 2 again" becomes a question asked every week.
DATACENTER_TO_PHASE = {"A": "1", "B": "2", "C": "3"}

# Which build-out phase each generator room belongs to. Not derivable from the
# room number — 324 sits in phase 2 between 323 and 325 which are phases 1 and
# 1 — so it is carried from the job this schedule is a sister to, where the
# same 28 rooms are split the same way. Their date order agrees.
GEN_PHASE = {
    **{r: "1" for r in ("315", "316", "317", "318", "319", "320", "321",
                        "322", "323", "325")},
    **{r: "2" for r in ("302", "304", "306", "308", "310", "312", "314", "324")},
    **{r: "3" for r in ("301", "303", "305", "307", "309", "311", "313",
                        "326", "327", "328")},
}

# The level-1 spine, in the order a schedule reads: what has to be decided,
# then what has to be bought, then the shell, then each fit-out phase.
SPINE = ["Milestones",
         "Procurement / Pre-Construction",
         "Construction (Core & Shell)",
         "Phase 1 (Build-Out)",
         "Phase 2 (Build-Out)",
         "Phase 3 (Build-Out)"]

# Second segment of the activity id -> where it goes under Construction.
SHELL = {
    "EXT": ("Construction (Core & Shell)", "Exterior / Site Utilities"),
    "STR": ("Construction (Core & Shell)", "Structure / Underground"),
    "ST":  ("Construction (Core & Shell)", "Structure"),
    "ROOF": ("Construction (Core & Shell)", "Roof"),
}

# Third-segment names within the shell, where the code is not self-explaining.
SHELL_SUB = {
    "SHF": "Shallow Foundations", "DP": "Deep Foundations",
    "UDG": "Underground", "SUT": "Site Utilities", "PC": "Precast",
    "ELEV": "Elevators", "L1": "Level 1 Areas", "MEP": "Roof MEP",
    "PLAT": "Mechanical Platform", "SYS": "Roofing System",
    "LLE": "Roof LLE", "EH": "Segments E + H", "DG": "Segments D + G",
    "CF": "Segments C + F", "AB": "Segments A + B",
}

# Within a phase, the second segment of the id -> the area folder it belongs
# to. The names are the client's own, with the datacenter letter swapped for
# the phase number.
PHASE_AREA = {
    "LLE": "LLE - Phase {ph}",
    "ER":  "Electrical Rooms - Phase {ph}",
    "MV":  "MV Rooms - Phase {ph}",
    "MVR": "MV Rooms - Phase {ph}",
    "HV":  "HV Rooms - Phase {ph}",
    "HVR": "HV Rooms - Phase {ph}",
    "L":   "CUP - Phase {ph}",
    "L1":  "CUP - Phase {ph}",
    "CHLR": "Dry Coolers - Phase {ph}",
    "ADM": "Admin Space - Phase {ph}",
    "1ST": "Admin Space - Phase {ph}",
    "2ND": "Admin Space - Phase {ph}",
    "3RD": "Admin Space - Phase {ph}",
    "EL":  "Elevators - Phase {ph}",
    "ELEV": "Elevators - Phase {ph}",
    "GAL": "Data Hall & Gallery (DH {dh})",
    "DH":  "Data Hall & Gallery (DH {dh})",
    "GL":  "Data Hall & Gallery (DH {dh})",
}

# A room named in the activity's own text: "Paint - MV 106 - Data Center B".
# The ids are not always right about the room — 48 of them name a different
# one from the activity they are on — and where the two disagree the words a
# person typed win over a code that was renumbered around them.
ROOM_IN_NAME = re.compile(r"\b(MV|HV|ER|GEN)\s*(\d{2,3})\b")

# And the building, which pins the phase: A, B, C -> 1, 2, 3.
DC_IN_NAME = re.compile(r"Data\s*Cent(?:er|re)\s*([ABC])\b", re.I)

# The data hall each phase fits out, for the folder names that carry it.
PHASE_DH = {"1": "202", "2": "201", "3": "101"}


def undouble(s: Any) -> str:
    """
    'Segment ESegment E' -> 'Segment E'.

    The parser emitted every folder name twice, with no separator. Left alone
    it reads as a typo nobody made and sorts wrongly next to its siblings.
    """
    s = " ".join(str(s or "").split())
    n = len(s)
    if n and n % 2 == 0 and s[:n // 2] == s[n // 2:]:
        return s[:n // 2]
    return s


def rename_datacenters(s: Any) -> str:
    """
    'LLE - Datacenter A' -> 'LLE - Phase 1', and the same inside any label.

    Applied to every name rather than a list of known ones, so a folder nobody
    thought to enumerate still comes out consistent with the rest.
    """
    out = undouble(s)
    for letter, phase in DATACENTER_TO_PHASE.items():
        out = re.sub(rf"Datacenter\s+{letter}\b", f"Phase {phase}", out,
                     flags=re.I)
    # 'PH1: DH202 "Phase 1"' is the spine folder saying its own name twice.
    out = re.sub(r'^PH(\d):\s*DH\d+\s*"?Phase \1"?$', r"Phase \1 (Build-Out)",
                 out)
    return out.strip()


def _segments(activity) -> List[str]:
    """The activity id split, with the job prefix dropped."""
    parts = [p for p in str(getattr(activity, "activity_id", "") or "").split(".")
             if p]
    return parts[1:] if len(parts) > 1 else []


def is_furniture(activity) -> bool:
    """
    An activity that is really a scrap of the printed page.

    Two of them: the running header and the page footer, both with no id
    segments, no duration and a name made of the words around them.
    """
    segs = _segments(activity)
    if segs:
        return False
    name = str(getattr(activity, "name", "") or "")
    return bool(re.search(r"page \d+ of|printed:|LIVE", name, re.I)) or not name.strip()


def _phase_of(activity, segs: List[str]) -> Optional[str]:
    """Which build-out phase this activity belongs to, name first then id."""
    d = DC_IN_NAME.search(str(getattr(activity, "name", "") or ""))
    if d:
        return DATACENTER_TO_PHASE.get(d.group(1).upper())
    m = re.match(r"^P(?:H)?([123])$", segs[0].upper()) if segs else None
    if m:
        return m.group(1)
    # A data hall number names its phase as surely as a phase code does.
    for seg in segs:
        n = re.search(r"(202|201|101)", seg)
        if n:
            return {"202": "1", "201": "2", "101": "3"}[n.group(1)]
    return None


def repair_parse_damage(project) -> List[Dict[str, str]]:
    """
    Undo what the PDF parse did to a handful of ids and names.

    Three activities came out as `MDC3.PH1.DH202W.120` named
    `0 Room Ready for Load - DH 202 (West)`: the last digit of the code fell
    off the end of the id and landed at the front of the name. Both are wrong
    and each makes the other look deliberate, so they are put back together
    rather than worked around.

    Returns what it changed, so a repair nobody asked for is still visible.
    """
    fixed = []
    for a in project.activities:
        name = str(getattr(a, "name", "") or "")
        aid = str(getattr(a, "activity_id", "") or "")
        m = re.match(r"^(\d)\s+(\S.*)$", name)
        if not m:
            continue
        digit, rest = m.groups()
        # Only when the id's own tail is short enough to be missing that digit.
        tail = aid.rsplit(".", 1)[-1]
        if not tail.isdigit() or len(tail) >= 4:
            continue
        fixed.append({"activity_id": aid, "was_name": name,
                      "now_id": f"{aid}{digit}", "now_name": rest})
        a.activity_id = f"{aid}{digit}"
        a.name = rest
    if fixed:
        project.build_lookups()
    return fixed


def place(activity) -> Optional[Tuple[str, ...]]:
    """
    The folder path this activity belongs at, from its id and its own name.

    None when neither says anything — reported by `plan` rather than dropped
    into a bucket, because an activity nobody can place is a question, not a
    folder.
    """
    segs = _segments(activity)
    if not segs:
        return None
    head = segs[0].upper()
    name = str(getattr(activity, "name", "") or "")

    # A milestone goes with the other milestones, under the phase it belongs
    # to. Buried in the area folder it was scheduled against, "Room Ready for
    # Load — DH 202 (East)" is one row among two hundred; gathered up, the set
    # of them is the schedule's spine.
    kind = str(getattr(activity, "activity_type", "")
               or getattr(activity, "type", "") or "")
    if "Milestone" in kind:
        ph = _phase_of(activity, segs)
        return ("Milestones", f"Phase {ph}") if ph else ("Milestones",)

    # A phase, in any of the spellings the parse produced (PH1, P2).
    m = re.match(r"^P(?:H)?([123])$", head)
    if m:
        ph = m.group(1)
        # The building named in the activity's own text overrules the id, which
        # is how a row called "MV 106 - Data Center B" ends up in phase 2 even
        # when its code was renumbered into somebody else's range.
        d = DC_IN_NAME.search(name)
        if d:
            ph = DATACENTER_TO_PHASE.get(d.group(1).upper(), ph)
        area = _phase_area(ph, segs[1:], name)
        return tuple(x for x in (f"Phase {ph} (Build-Out)", *area) if x)

    # A generator room: the phase comes from the room, not the id.
    if head == "GEN" and len(segs) > 1:
        room = re.sub(r"\D", "", segs[1])
        ph = GEN_PHASE.get(room)
        if ph:
            return (f"Phase {ph} (Build-Out)", f"Generator Rooms - Phase {ph}",
                    f"Gen {room}")
        if room:
            return ("Construction (Core & Shell)", "Generator Rooms",
                    f"Gen {room}")
        # GEN.RM — the generator rooms generally, with no one room named. Six
        # rows of final finishes. A folder called "Gen " with nothing after it
        # is worse than saying plainly that the room is not specified.
        return ("Construction (Core & Shell)", "Generator Rooms",
                "All Rooms — Final Finishes")

    # Procurement, design, funding, submittals.
    if head in ("SUB", "DSG", "FDG", "PRO"):
        return ("Procurement / Pre-Construction",
                {"SUB": "Submittals / Shop Drawings", "DSG": "Design",
                 "FDG": "Funding", "PRO": "General Procurement"}[head])
    if head == "MIL":
        return ("Milestones",)

    # The shell.
    if head in SHELL:
        top, mid = SHELL[head]
        sub = SHELL_SUB.get(segs[1].upper()) if len(segs) > 1 else None
        if head == "ST" and len(segs) > 1 and segs[1].upper() == "L1":
            area = segs[2].upper() if len(segs) > 2 else ""
            return (top, mid, "Level 1 Areas", f"Area {area.lstrip('A')}" if area else "Level 1 Areas")
        if head == "ST" and len(segs) > 1 and segs[1].upper() == "PC":
            area = segs[2].upper() if len(segs) > 2 else ""
            return (top, mid, "Precast", f"Precast {area}" if area else "Precast")
        return tuple(x for x in (top, mid, sub) if x)

    return None


def _phase_area(ph: str, rest: List[str], name: str = "") -> Tuple[str, ...]:
    """The area folder, and where useful the room under it, inside a phase."""
    if not rest:
        return ()
    seg = rest[0].upper()
    dh = PHASE_DH.get(ph, "")

    # A grid line, data hall or gallery: all of it is the data hall fit-out.
    m = re.match(r"^(GL|DH|GAL)(\d+)([EW])?$", seg)
    if m:
        kind, num, side = m.groups()
        hall = PHASE_AREA["DH"].format(ph=ph, dh=dh)
        if kind == "GAL":
            return (hall, f"Gallery {num}")
        if kind == "DH":
            return (hall, f"Data Hall {num}",
                    {"E": "East", "W": "West"}.get(side or "", "") or None)
        return (hall, f"Data Hall {dh}", f"GL {num}"
                + {"E": " East", "W": " West"}.get(side or "", ""))

    # The room the activity says it is in, which beats the room its code says.
    # "Drywall Framing - HV 102 - Data Center C" sat under MV 103 because that
    # is what its id read, and an area folder full of the wrong rooms is worse
    # than no area folder at all.
    named = ROOM_IN_NAME.search(name)
    if named:
        kind, num = named.group(1).upper(), named.group(2)
        tmpl = PHASE_AREA.get(kind)
        if tmpl and kind in ("MV", "HV", "ER"):
            return (tmpl.format(ph=ph, dh=dh), f"{kind} {num}")

    tmpl = PHASE_AREA.get(seg)
    if tmpl:
        area = tmpl.format(ph=ph, dh=dh)
        room = rest[1].upper() if len(rest) > 1 else ""
        if seg in ("MV", "MVR", "ER", "HV", "HVR") and re.fullmatch(r"\d+", room):
            return (area, f"{seg.rstrip('R') if seg.endswith('R') else seg} {room}")
        if seg in ("L", "L1") and room:
            return (area, room.replace("UP", "Lineup ").replace("R", "Room ").strip())
        return (area,)

    # A code with the room stuck to it: HVR101, MV102.
    m = re.match(r"^([A-Z]+?)(\d{2,3})$", seg)
    if m and PHASE_AREA.get(m.group(1)):
        kind = m.group(1)
        return (PHASE_AREA[kind].format(ph=ph, dh=dh),
                f"{kind.rstrip('R') if kind.endswith('R') and len(kind) > 2 else kind} {m.group(2)}")

    return (f"{seg} - Phase {ph}",)


def plan(project, drop_furniture: bool = True) -> Dict[str, Any]:
    """
    The tree this schedule would get, and where every activity would land.

    Reports rather than asserts: `unplaced` is the number that decides whether
    this is ready to apply, and it is listed with examples so the id grammar
    can be extended instead of guessed at.
    """
    furniture = [a for a in project.activities if is_furniture(a)]
    work = [a for a in project.activities
            if not (drop_furniture and is_furniture(a))]

    placed: Dict[Tuple[str, ...], List[Any]] = collections.defaultdict(list)
    unplaced: List[Any] = []
    for a in work:
        p = place(a)
        if p:
            placed[p].append(a)
        else:
            unplaced.append(a)

    paths = set()
    for p in placed:
        for i in range(1, len(p) + 1):
            paths.add(p[:i])
    junk = [undouble(n.name) for n in project.wbs_nodes
            if FURNITURE.match(undouble(n.name))]

    return {"folders": sorted(paths),
            "placed": placed,
            "unplaced": unplaced,
            "furniture": furniture,
            "source_junk_folders": junk,
            "counts": {"activities": len(project.activities),
                       "work": len(work),
                       "furniture": len(furniture),
                       "placed": sum(len(v) for v in placed.values()),
                       "unplaced": len(unplaced),
                       "folders_before": len(project.wbs_nodes),
                       "folders_after": len(paths)}}


def describe(result: Dict[str, Any], limit: int = 12) -> str:
    c = result["counts"]
    lines = [f"{c['activities']} activities: {c['placed']} placed, "
             f"{c['unplaced']} unplaced, {c['furniture']} page furniture.",
             f"{c['folders_before']} flat folders -> {c['folders_after']} "
             f"in a hierarchy."]
    if result["unplaced"]:
        lines.append("\nNothing in the id says where these go:")
        lines += [f"  {a.activity_id:28} {str(getattr(a, 'name', ''))[:40]}"
                  for a in result["unplaced"][:limit]]
    return "\n".join(lines)


def rebuild(project, drop_furniture: bool = True,
            project_id: Optional[str] = None,
            project_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Replace the WBS with the planned one and re-point every activity at it.

    Refuses while anything is unplaced. A rebuild that quietly leaves forty
    rows behind still shows about the right total, which is exactly how it
    would go unnoticed.
    """
    from .schedule_model import WBSNode

    result = plan(project, drop_furniture=drop_furniture)
    if result["unplaced"]:
        raise ValueError(
            f"{len(result['unplaced'])} activities have no place in the tree; "
            f"extend the id grammar rather than losing them "
            f"(first: {result['unplaced'][0].activity_id})")

    if project_id:
        project.id = project_id
    if project_name:
        project.name = project_name

    root_uid = "WBS-ROOT"
    nodes = [WBSNode(uid=root_uid, name=project.name or project.id or "Project",
                     code=project.id or "ROOT")]
    uid_of: Dict[Tuple[str, ...], str] = {(): root_uid}
    for i, path in enumerate(result["folders"], start=1):
        uid = f"WBS-{i:04d}"
        uid_of[path] = uid
        nodes.append(WBSNode(uid=uid, name=path[-1],
                             code=_code(path), parent_uid=uid_of[path[:-1]]))
    project.wbs_nodes = nodes

    for path, acts in result["placed"].items():
        for a in acts:
            a.wbs_uid = uid_of[path]
    if drop_furniture and result["furniture"]:
        drop = {id(a) for a in result["furniture"]}
        project.activities = [a for a in project.activities if id(a) not in drop]

    project.build_lookups()
    return result


def _code(path: Tuple[str, ...]) -> str:
    """A short code per folder, from its own name rather than the whole path."""
    word = re.sub(r"[^A-Za-z0-9]+", "", path[-1]).upper()
    return (word[:12] or "WBS") + str(len(path))
