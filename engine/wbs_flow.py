# -*- coding: utf-8 -*-
"""
wbs_flow.py — does each WBS folder actually connect to the rest of the job?

WHY THIS EXISTS
  A schedule can pass every "activity has a predecessor" check and still be a
  pile of disconnected islands. Each folder is wired tidily inside itself, and
  nothing leaves it — so a slip in one area never reaches the milestone it
  ought to drive, and the critical path runs through whichever island happens
  to be longest. Counting open ends per ACTIVITY does not show that; the
  question is about the FOLDER.

WHAT "CONNECTED" MEANS HERE
  Deliberately stricter than "some activity in this folder has a link", which
  one stray tie would satisfy while the other forty rows dangle:

    a folder is CONNECTED (green) when
      · no activity in it is floating — every row has a predecessor or a
        successor SOMEWHERE, inside the folder or out, and
      · at least one activity links OUT to another folder, and
      · at least one activity is driven from OUTSIDE

  Work inside a folder is expected to be chained to itself, so requiring every
  activity to reach out would be wrong. Requiring at least one in and one out,
  with nothing left dangling, is the line between "this branch is part of the
  network" and "this branch is decoration".

  A folder holding no activities of its own is a container — judged on its
  children instead, so a parent is not marked broken for being a parent.

BACKWARD FLOW
  A tie is backward when the predecessor's folder is dated later than the
  successor's. That is usually a real mistake — work feeding something that
  already happened — and it is reported per folder rather than only counted,
  because the pair is what you have to look at.
"""

from typing import Any, Dict, List, Optional

# Folder verdicts, worst last — the order the report groups by.
CONNECTED = "connected"       # nothing floating, flows in and out
ONE_WAY = "one_way"           # reaches out or is fed, but not both
DANGLING = "dangling"         # has links, but some activity floats
ISOLATED = "isolated"         # nothing in this folder links anywhere
EMPTY = "empty"               # a container; judged on its children


def _folder_of(project) -> Dict[str, str]:
    """activity uid -> wbs uid."""
    return {a.uid: a.wbs_uid for a in project.activities}


def _earliest(acts) -> Optional[str]:
    dates = [str(a.planned_start)[:10] for a in acts if a.planned_start]
    return min(dates) if dates else None


def analyse(project) -> Dict[str, Any]:
    """
    One verdict per folder, plus the folder-to-folder edges the logic implies.

    Returns {"folders": {uid: {...}}, "edges": [...], "totals": {...}}.
    Pure reading — nothing is modified.
    """
    by_uid = {w.uid: w for w in project.wbs_nodes}
    acts_by_folder: Dict[str, List[Any]] = {}
    for a in project.activities:
        acts_by_folder.setdefault(a.wbs_uid, []).append(a)

    folder_of = _folder_of(project)

    # Per-activity link presence, and per-folder in/out crossings.
    has_pred = {a.uid: False for a in project.activities}
    has_succ = {a.uid: False for a in project.activities}
    out_edges: Dict[str, Dict[str, int]] = {}   # folder -> {folder: count}
    in_count: Dict[str, int] = {}
    out_count: Dict[str, int] = {}

    for r in project.relations:
        p_uid, s_uid = r.predecessor_uid, r.successor_uid
        if p_uid in has_succ:
            has_succ[p_uid] = True
        if s_uid in has_pred:
            has_pred[s_uid] = True
        pf, sf = folder_of.get(p_uid), folder_of.get(s_uid)
        if pf is None or sf is None or pf == sf:
            continue                      # inside one folder: not a crossing
        out_edges.setdefault(pf, {})
        out_edges[pf][sf] = out_edges[pf].get(sf, 0) + 1
        out_count[pf] = out_count.get(pf, 0) + 1
        in_count[sf] = in_count.get(sf, 0) + 1

    folders: Dict[str, Any] = {}
    for w in project.wbs_nodes:
        acts = acts_by_folder.get(w.uid, [])
        floating = [a.activity_id for a in acts
                    if not has_pred.get(a.uid) and not has_succ.get(a.uid)]
        goes_out = out_count.get(w.uid, 0)
        comes_in = in_count.get(w.uid, 0)

        if not acts:
            verdict = EMPTY
        elif not goes_out and not comes_in and len(floating) == len(acts):
            verdict = ISOLATED
        elif floating:
            verdict = DANGLING
        elif goes_out and comes_in:
            verdict = CONNECTED
        elif goes_out or comes_in:
            verdict = ONE_WAY
        else:
            # wired internally, but the whole block touches nothing outside
            verdict = ISOLATED

        folders[w.uid] = {
            "uid": w.uid, "name": w.name, "code": w.code,
            "parent_uid": w.parent_uid,
            "verdict": verdict,
            "activities": len(acts),
            "floating": floating,
            "floating_count": len(floating),
            "links_out": goes_out,
            "links_in": comes_in,
            "start": _earliest(acts),
        }

    # Backward flow: a folder feeding one that starts earlier than it does.
    backward = []
    for pf, targets in out_edges.items():
        ps = folders.get(pf, {}).get("start")
        for sf, n in targets.items():
            ss = folders.get(sf, {}).get("start")
            if ps and ss and ss < ps:
                backward.append({"from": pf, "to": sf, "count": n,
                                 "from_start": ps, "to_start": ss})
    for b in backward:
        folders[b["from"]]["backward_out"] = folders[b["from"]].get("backward_out", 0) + 1

    edges = [{"from": pf, "to": sf, "count": n}
             for pf, targets in out_edges.items() for sf, n in targets.items()]

    counted = [f for f in folders.values() if f["verdict"] != EMPTY]
    totals = {
        "folders": len(folders),
        "with_activities": len(counted),
        "connected": sum(1 for f in counted if f["verdict"] == CONNECTED),
        "one_way": sum(1 for f in counted if f["verdict"] == ONE_WAY),
        "dangling": sum(1 for f in counted if f["verdict"] == DANGLING),
        "isolated": sum(1 for f in counted if f["verdict"] == ISOLATED),
        "backward_edges": len(backward),
        "floating_activities": sum(f["floating_count"] for f in folders.values()),
    }
    return {"folders": folders, "edges": edges, "backward": backward,
            "totals": totals}


def duplicates(project, max_groups: int = 40) -> str:
    """
    Activities that look duplicated — same name, same folder, same dates.

    A schedule assembled from repeated imports grows pairs of identical rows:
    same work, same day, two ids. They double the apparent scope, and wiring
    logic into one of the pair while the other floats is how a branch ends up
    half-connected for reasons nobody can see. Matched on name + folder +
    planned start, so genuinely repeated work in DIFFERENT areas or on
    different dates is left alone.
    """
    groups: Dict[str, List[Any]] = {}
    for a in project.activities:
        key = (f"{(a.name or '').strip().lower()}|{a.wbs_uid}|"
               f"{str(a.planned_start or '')[:10]}")
        groups.setdefault(key, []).append(a)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    if not dupes:
        return "No duplicated activities found (same name, same folder, same start date)."

    by_uid = {w.uid: w for w in project.wbs_nodes}
    total_extra = sum(len(v) - 1 for v in dupes.values())
    out = [f"DUPLICATE ACTIVITIES — {len(dupes)} group(s), {total_extra} extra "
           f"row(s) beyond the first of each:",
           "(same name, same folder, same planned start)"]

    ordered = sorted(dupes.values(), key=lambda v: -len(v))
    for v in ordered[:max_groups]:
        a = v[0]
        folder = by_uid.get(a.wbs_uid)
        fname = folder.name if folder else "?"
        ids = ", ".join(x.activity_id for x in v)
        out.append(f"  {len(v)}x  {a.name}  [{fname}, {str(a.planned_start or '')[:10]}]"
                   f"\n      {ids}")
    if len(ordered) > max_groups:
        out.append(f"  …and {len(ordered) - max_groups} more group(s)")
    out.append("\nNothing has been changed. Deleting one of a pair is safe only "
               "once you have checked which one carries the logic — say which "
               "and I'll do it.")
    return "\n".join(out)


_VERDICT_LABEL = {
    CONNECTED: "CONNECTED",
    ONE_WAY: "ONE-WAY",
    DANGLING: "DANGLING",
    ISOLATED: "ISOLATED",
    EMPTY: "container",
}


def report(project, max_rows: int = 60) -> str:
    """The same analysis as prose, for the chat and for the agent to read."""
    data = analyse(project)
    t = data["totals"]
    if not t["with_activities"]:
        return "No folders hold any activities yet."

    out = [
        f"WBS FLOW — {t['with_activities']} folders holding activities:",
        f"  {t['connected']} connected (flow in AND out, nothing floating)",
        f"  {t['one_way']} one-way (fed but nothing leaves, or leaves but nothing feeds it)",
        f"  {t['dangling']} have loose activities inside",
        f"  {t['isolated']} isolated — nothing in them links anywhere",
    ]
    if t["backward_edges"]:
        out.append(f"  {t['backward_edges']} folder link(s) run BACKWARD "
                   f"(feeding work that starts earlier)")
    if t["floating_activities"]:
        out.append(f"  {t['floating_activities']} activities have no logic at all")

    problems = [f for f in data["folders"].values()
                if f["verdict"] in (ISOLATED, ONE_WAY, DANGLING)]
    order = {ISOLATED: 0, ONE_WAY: 1, DANGLING: 2}
    problems.sort(key=lambda f: (order.get(f["verdict"], 9), -f["activities"]))

    if problems:
        out.append("\nNEEDS LOGIC (worst first):")
        for f in problems[:max_rows]:
            bits = [f"{f['activities']} act"]
            if f["links_in"] or f["links_out"]:
                bits.append(f"in {f['links_in']} / out {f['links_out']}")
            if f["floating_count"]:
                bits.append(f"{f['floating_count']} floating")
            line = f"  [{_VERDICT_LABEL[f['verdict']]}] {f['name']} — {', '.join(bits)}"
            if f["floating_count"]:
                shown = ", ".join(f["floating"][:4])
                more = f" +{f['floating_count'] - 4} more" if f["floating_count"] > 4 else ""
                line += f"\n      floating: {shown}{more}"
            out.append(line)
        if len(problems) > max_rows:
            out.append(f"  …and {len(problems) - max_rows} more folders")
        if any(f["verdict"] == ONE_WAY for f in problems):
            out.append("\n  Note: the job's FIRST folder legitimately has nothing "
                       "feeding it and its LAST has nothing after it, so those two "
                       "show as one-way and are not faults.")
    else:
        out.append("\nEvery folder holding work flows in and out. Nothing floating.")

    if data["backward"]:
        out.append("\nBACKWARD FLOW — predecessor folder starts AFTER the folder "
                   "it feeds:")
        for b in data["backward"][:12]:
            fn = data["folders"][b["from"]]["name"]
            tn = data["folders"][b["to"]]["name"]
            out.append(f"  {fn} ({b['from_start']}) → {tn} ({b['to_start']})"
                       f"  ×{b['count']}")
    return "\n".join(out)
