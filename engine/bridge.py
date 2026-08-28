# -*- coding: utf-8 -*-
"""
bridge.py — connect a folder to the rest of the job, and explain the choice.

WHY THIS IS ITS OWN THING
  Ordering the work inside a folder and attaching that folder to the job are
  different questions with different candidate sets. The first looks only at
  the folder's own rows; the second has to look at the whole schedule. Doing
  the first and calling the folder wired is how a pass can close hundreds of
  open ends while every folder stays an island.

  Choosing the bridge is a combination of three things, and getting any one
  of them wrong produces a tie that looks reasonable and is not:

    WHAT THE FOLDER CONTAINS   the bridge must attach to the row that really
                               STARTS the work, not whichever id sorts first
                               — the earliest-dated real activity, and a
                               milestone in preference when one exists,
                               because that is what a milestone is for
    ITS INTERNAL FLOW          if the folder is already chained, only its head
                               needs feeding and only its tail needs to drive
                               on. Bridging into the middle of a chain adds a
                               tie that changes nothing
    THE BEST PARTNER OUTSIDE   ranked by the same scorer everything else uses,
                               so a stated rule, the scope document and the
                               dates all still decide

BACKWARD FLOW
  A folder tie running backward in time — a predecessor folder dated after
  the folder it feeds — is nearly always one of three things, and they are
  worth telling apart rather than "fixing" blindly:

    reversed   the same two folders are also tied the right way round, or the
               dates say plainly the tie is upside down. Reversing it is safe
               and is what the user means by "clear the backward flag"
    stale      the dates moved after the tie was made. The tie is fine; the
               schedule needs a reflow, not an edit
    real       genuinely out-of-sequence work. Only a human can say

  This reports which, and only ever proposes reversing the first kind.
"""

from typing import Any, Dict, List, Optional

REVERSED = "reversed"
STALE = "stale"
REAL = "real"


def _d(v) -> str:
    return str(v or "")[:10]


def _pdate(v):
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(_d(v))
    except ValueError:
        return None


def _descendants(project, root_uid) -> set:
    kids: Dict[str, List[str]] = {}
    for w in project.wbs_nodes:
        kids.setdefault(w.parent_uid, []).append(w.uid)
    out, stack, guard = {root_uid}, [root_uid], 0
    while stack and guard < 20000:
        guard += 1
        for k in kids.get(stack.pop(), []):
            if k not in out:
                out.add(k)
                stack.append(k)
    return out


def entry_and_exit(project, acts_in) -> Dict[str, Any]:
    """
    Which row really starts this folder's work, and which really ends it.

    Not the first id, and not simply the earliest date: a folder that is
    already chained internally has a HEAD (nothing inside drives it) and a
    TAIL (it drives nothing inside). Those are the rows a bridge belongs on.
    Feeding a row halfway down an existing chain adds a tie that changes no
    date and hides that the folder is still unfed.
    """
    if not acts_in:
        return {"head": None, "tail": None, "internal_chain": False}
    inside = {a.uid for a in acts_in}
    driven_inside, drives_inside = set(), set()
    for r in project.relations:
        if r.predecessor_uid in inside and r.successor_uid in inside:
            driven_inside.add(r.successor_uid)
            drives_inside.add(r.predecessor_uid)

    heads = [a for a in acts_in if a.uid not in driven_inside]
    tails = [a for a in acts_in if a.uid not in drives_inside]

    def _earliest(rows):
        dated = [(a, _pdate(a.planned_start)) for a in rows]
        dated = [(a, d) for a, d in dated if d]
        if not dated:
            return rows[0] if rows else None
        # A start milestone is what a folder is meant to be entered through,
        # so prefer one when it sits at the front.
        ms = [t for t in dated if t[0].activity_type == "Start Milestone"]
        pool = ms or dated
        return min(pool, key=lambda t: t[1])[0]

    def _latest(rows):
        dated = [(a, _pdate(a.planned_finish) or _pdate(a.planned_start))
                 for a in rows]
        dated = [(a, d) for a, d in dated if d]
        if not dated:
            return rows[-1] if rows else None
        ms = [t for t in dated if t[0].activity_type == "Finish Milestone"]
        pool = ms or dated
        return max(pool, key=lambda t: t[1])[0]

    return {
        "head": _earliest(heads or acts_in),
        "tail": _latest(tails or acts_in),
        "internal_chain": bool(driven_inside),
        "open_heads": len(heads),
        "open_tails": len(tails),
    }


def propose(project, folder_uid: str, brain=None, min_confidence: float = 0.5,
            max_gap_days: int = 120) -> Dict[str, Any]:
    """
    The bridge in and the bridge out for one folder, with the reasoning.

    Read-only. Returns the candidates and why each was chosen, so the choice
    can be argued with rather than taken on trust.
    """
    from engine.logic_advisor import _Ctx, _has_link, implied_lag, score_tie

    by_uid = {w.uid: w for w in project.wbs_nodes}
    node = by_uid.get(folder_uid)
    if node is None:
        return {"error": f"No folder {folder_uid}"}

    scope = _descendants(project, folder_uid)
    acts_in = [a for a in project.activities if a.wbs_uid in scope]
    if not acts_in:
        return {"error": f"'{node.name}' holds no activities"}

    ends = entry_and_exit(project, acts_in)
    inside = {a.uid for a in acts_in}
    outside = [a for a in project.activities
               if a.uid not in inside
               and a.status != "Completed"]

    ctx = _Ctx(project,
               getattr(brain, "directives", None) if brain else None,
               getattr(brain, "feedback", None) if brain else None,
               getattr(brain, "scope", None) if brain else None)

    has_in = any(r.successor_uid in inside and r.predecessor_uid not in inside
                 for r in project.relations)
    has_out = any(r.predecessor_uid in inside and r.successor_uid not in inside
                  for r in project.relations)

    folder_of = {a.uid: by_uid.get(a.wbs_uid).name
                 for a in project.activities if by_uid.get(a.wbs_uid)}

    def _rank(target, direction):
        """Best partner outside for the folder's head (in) or tail (out)."""
        out = []
        t_start, t_finish = _pdate(target.planned_start), _pdate(target.planned_finish)
        for other in outside:
            if direction == "in":
                f = _pdate(other.planned_finish)
                if not f or not t_start or f > t_start:
                    continue
                if (t_start - f).days > max_gap_days:
                    continue
                if _has_link(project, other.uid, target.uid):
                    continue
                lag = implied_lag(project, other, target)
                if lag is None:
                    continue
                c, why = score_tie(ctx, other, target, lag)
            else:
                s = _pdate(other.planned_start)
                if not s or not t_finish or s < t_finish:
                    continue
                if (s - t_finish).days > max_gap_days:
                    continue
                if _has_link(project, target.uid, other.uid):
                    continue
                lag = implied_lag(project, target, other)
                if lag is None:
                    continue
                c, why = score_tie(ctx, target, other, lag)
            out.append({"activity_id": other.activity_id, "name": other.name,
                        "folder": folder_of.get(other.uid, ""),
                        "confidence": round(c, 2), "why": why[:3]})
        out.sort(key=lambda r: -r["confidence"])
        return out[:5]

    result: Dict[str, Any] = {
        "folder": node.name, "uid": folder_uid,
        "activities": len(acts_in),
        "already_fed": has_in, "already_drives": has_out,
        "head": {"activity_id": ends["head"].activity_id,
                 "name": ends["head"].name,
                 "start": _d(ends["head"].planned_start)} if ends["head"] else None,
        "tail": {"activity_id": ends["tail"].activity_id,
                 "name": ends["tail"].name,
                 "finish": _d(ends["tail"].planned_finish)} if ends["tail"] else None,
        "internal_chain": ends["internal_chain"],
        "open_heads": ends.get("open_heads", 0),
        "open_tails": ends.get("open_tails", 0),
        "in_candidates": [], "out_candidates": [], "commands": [],
    }

    if not has_in and ends["head"] is not None:
        cands = _rank(ends["head"], "in")
        result["in_candidates"] = cands
        if cands and cands[0]["confidence"] >= min_confidence:
            result["commands"].append({
                "action": "add_relation",
                "predecessor_id": cands[0]["activity_id"],
                "successor_id": ends["head"].activity_id,
                "type": "fs", "lag_days": 0})

    if not has_out and ends["tail"] is not None:
        cands = _rank(ends["tail"], "out")
        result["out_candidates"] = cands
        if cands and cands[0]["confidence"] >= min_confidence:
            result["commands"].append({
                "action": "add_relation",
                "predecessor_id": ends["tail"].activity_id,
                "successor_id": cands[0]["activity_id"],
                "type": "fs", "lag_days": 0})

    return result


def classify_backward(project) -> List[Dict[str, Any]]:
    """
    Every backward folder tie, and which of the three kinds it is.

    Telling them apart is the whole value: reversing a tie whose dates simply
    moved would break correct logic, and reflowing a genuinely reversed tie
    would not fix anything.
    """
    by_uid = {w.uid: w for w in project.wbs_nodes}
    folder_of = {a.uid: a.wbs_uid for a in project.activities}
    act_of = {a.uid: a for a in project.activities}

    starts: Dict[str, Any] = {}
    for a in project.activities:
        d = _pdate(a.planned_start)
        if d and (a.wbs_uid not in starts or d < starts[a.wbs_uid]):
            starts[a.wbs_uid] = d

    pairs = {(r.predecessor_uid, r.successor_uid) for r in project.relations}
    out = []
    for r in project.relations:
        pf, sf = folder_of.get(r.predecessor_uid), folder_of.get(r.successor_uid)
        if not pf or not sf or pf == sf:
            continue
        ps, ss = starts.get(pf), starts.get(sf)
        if not ps or not ss or ss >= ps:
            continue                      # forward, or undated — not our business

        p_act, s_act = act_of.get(r.predecessor_uid), act_of.get(r.successor_uid)
        if not p_act or not s_act:
            continue

        # The activities' OWN dates decide the kind, not the folders'.
        p_fin, s_start = _pdate(p_act.planned_finish), _pdate(s_act.planned_start)
        mirrored = (r.successor_uid, r.predecessor_uid) in pairs
        if mirrored:
            kind = REVERSED
            why = "the same pair is also tied the other way round"
        elif p_fin and s_start and s_start < p_fin:
            kind = REVERSED
            why = (f"{s_act.activity_id} starts {_d(s_act.planned_start)}, before "
                   f"{p_act.activity_id} finishes {_d(p_act.planned_finish)}")
        else:
            kind = STALE
            why = ("these two activities are in order; only the FOLDERS' "
                   "earliest dates disagree, which a reflow settles")

        out.append({
            "kind": kind, "why": why,
            "from_folder": by_uid[pf].name if pf in by_uid else pf,
            "to_folder": by_uid[sf].name if sf in by_uid else sf,
            "predecessor_id": p_act.activity_id, "predecessor": p_act.name,
            "successor_id": s_act.activity_id, "successor": s_act.name,
            "predecessor_finish": _d(p_act.planned_finish),
            "successor_start": _d(s_act.planned_start),
        })
    return out


def backward_report(project) -> str:
    rows = classify_backward(project)
    if not rows:
        return "No folder ties run backward in time."
    rev = [r for r in rows if r["kind"] == REVERSED]
    stale = [r for r in rows if r["kind"] == STALE]
    out = [f"BACKWARD FOLDER TIES — {len(rows)}. Nothing has been changed."]
    if rev:
        out.append(f"\nREVERSED ({len(rev)}) — the tie itself is upside down. "
                   f"Reversing these is safe and is what clears the flag:")
        for r in rev[:12]:
            out.append(f"  {r['from_folder']} → {r['to_folder']}"
                       f"\n     {r['predecessor_id']} {r['predecessor']}"
                       f"  →  {r['successor_id']} {r['successor']}"
                       f"\n     {r['why']}")
    if stale:
        out.append(f"\nSTALE DATES ({len(stale)}) — the ties are fine. The "
                   f"folders' earliest dates disagree because the schedule has "
                   f"not been reflowed. Run Schedule rather than editing logic:")
        for r in stale[:8]:
            out.append(f"  {r['from_folder']} → {r['to_folder']}"
                       f"  ({r['predecessor_id']} → {r['successor_id']})")
    return "\n".join(out)


def fix_backward(project) -> List[Dict[str, Any]]:
    """
    Commands to reverse only the ties that are genuinely upside down.

    Deleting and re-adding is one operation, so a single undo puts it back.
    Stale ties are never touched — they need a reflow, not an edit.
    """
    cmds = []
    for r in classify_backward(project):
        if r["kind"] != REVERSED:
            continue
        cmds.append({"action": "delete_relation",
                     "predecessor_id": r["predecessor_id"],
                     "successor_id": r["successor_id"]})
        cmds.append({"action": "add_relation",
                     "predecessor_id": r["successor_id"],
                     "successor_id": r["predecessor_id"],
                     "type": "fs", "lag_days": 0})
    return cmds


def report(project, folder_uid: str, brain=None) -> str:
    """One folder's bridging options, with the reasoning shown."""
    r = propose(project, folder_uid, brain)
    if r.get("error"):
        return r["error"]

    out = [f"BRIDGING '{r['folder']}' — {r['activities']} activities. "
           f"Nothing has been changed."]
    if r["head"]:
        out.append(f"  Work starts at: {r['head']['activity_id']} "
                   f"{r['head']['name']} ({r['head']['start']})"
                   + (f"  [{r['open_heads']} unfed rows inside]"
                      if r["open_heads"] > 1 else ""))
    if r["tail"]:
        out.append(f"  Work ends at:   {r['tail']['activity_id']} "
                   f"{r['tail']['name']} ({r['tail']['finish']})")
    out.append(f"  Internally chained: {'yes' if r['internal_chain'] else 'no'}"
               f" · fed from outside: {'yes' if r['already_fed'] else 'NO'}"
               f" · drives outside: {'yes' if r['already_drives'] else 'NO'}")

    if r["already_fed"] and r["already_drives"]:
        out.append("\n  This folder is already bridged both ways.")
        return "\n".join(out)

    if not r["already_fed"]:
        out.append("\n  BRIDGE IN — best predecessors for the starting activity:")
        if not r["in_candidates"]:
            out.append("    nothing outside finishes close enough before it. "
                       "Either its dates are wrong or the work that feeds it "
                       "is not in the schedule.")
        for c in r["in_candidates"]:
            out.append(f"    {c['confidence']:.2f}  {c['activity_id']} "
                       f"{c['name']}  [{c['folder']}]")
            if c["why"]:
                out.append(f"          {'; '.join(c['why'])}")

    if not r["already_drives"]:
        out.append("\n  BRIDGE OUT — best successors for the finishing activity:")
        if not r["out_candidates"]:
            out.append("    nothing outside starts close enough after it.")
        for c in r["out_candidates"]:
            out.append(f"    {c['confidence']:.2f}  {c['activity_id']} "
                       f"{c['name']}  [{c['folder']}]")
            if c["why"]:
                out.append(f"          {'; '.join(c['why'])}")

    if r["commands"]:
        out.append(f"\n  {len(r['commands'])} tie(s) would be made. "
                   f"Pass apply=true to make them.")
    return "\n".join(out)
