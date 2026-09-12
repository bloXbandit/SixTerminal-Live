# -*- coding: utf-8 -*-
"""
connect.py — "does every one of these actually tie into that?", and if not, tie it.

THE QUESTION THIS ANSWERS
  A schedule can be fully linked inside each room and still be wrong, because
  nothing carries the room's completion out to the thing that depends on it.
  Each generator room is wired end to end; the room as a whole reaches nothing.
  The dates look fine — every activity has a date — and the milestone that is
  supposed to wait for twenty-eight rooms is waiting for twelve.

  Nobody finds that by reading the Gantt. It is only visible as a question
  about the NETWORK: from this folder, can I get there at all? Asked once per
  folder, over a couple of thousand relations, which is exactly the kind of
  thing that should not be done by eye.

WHAT IS GENERAL HERE
  The shape is not "generators to commissioning". It is:

      for every folder matching X, confirm it reaches its OWN Y, and if it
      does not, connect it — where "its own" is decided by a scope key both
      sides carry.

  The scope key is the part that makes it worth writing. A phase's generator
  rooms must reach THAT phase's commissioning, not phase 1's, and the two
  sides do not live in the same branch — the rooms sit under "Phase 3
  (Build-Out)" and the milestones under "Milestones / Phase 3". Pairing on the
  parent folder fails. Pairing on a key lifted out of the path or the name
  ("PH3", "Phase 3") works, and works equally for areas, levels, buildings or
  anything else the job numbers its work by.

  So the same command covers "every gen room into its phase's commissioning",
  "every room on a level into that level's turnover", "every lineup into its
  own energisation" — without any of them being special-cased here.

WHAT IT WILL NOT DO
  It never removes or rewires existing logic. A folder that already reaches
  its target is reported and left completely alone, including when it gets
  there by a route nobody would have chosen; the request is "ensure they
  connect", and they do. Repair only ever ADDS a relation.

  It will not invent a pairing. A folder whose scope key cannot be read, or
  whose key has no target, is named in the report and skipped — connecting it
  to some other phase's milestone is far worse than leaving the gap visible.

  And it previews. Reaching twenty-eight folders from one sentence is the
  case where a misread pattern should cost a re-read, not an undo.
"""

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from .schedule_model import Project, Relation, WBSNode

# How work is numbered on a job with phases. Deliberately anchored on the word
# so that "Level 3 Commissioning Start (PH3)" yields 3 from the (PH3), not
# from the "Level 3" — those are different numbers and matching the wrong one
# would wire every room to the wrong milestone while looking entirely correct.
DEFAULT_SCOPE = r"\b(?:PH|PHASE)\s*-?\s*(\d+)\b"


def _uid() -> str:
    return str(uuid.uuid4().int)[:10]


def _rx(pattern: str, what: str) -> "re.Pattern":
    try:
        return re.compile(pattern, re.I)
    except re.error as e:
        raise ValueError(f"{what} is not a valid pattern: {e}")


# ── the network ──────────────────────────────────────────────────────────────

def _graph(project: Project) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Forward and backward adjacency over activities that actually exist."""
    live = {a.uid for a in project.activities}
    fwd: Dict[str, List[str]] = {}
    back: Dict[str, List[str]] = {}
    for r in project.relations:
        if r.predecessor_uid in live and r.successor_uid in live:
            fwd.setdefault(r.predecessor_uid, []).append(r.successor_uid)
            back.setdefault(r.successor_uid, []).append(r.predecessor_uid)
    return fwd, back


def reachable(adj: Dict[str, List[str]], starts, include_starts: bool = True) -> Set[str]:
    """Everything reachable from `starts` through `adj`. Cycle-safe."""
    seen: Set[str] = set(starts)
    stack = list(seen)
    while stack:
        for v in adj.get(stack.pop(), ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    if not include_starts:
        seen -= set(starts)
    return seen


def upstream(project: Project, uid: str, include_self: bool = True) -> Set[str]:
    """
    Everything this activity depends on, however far back.

    The mirror of ripple.downstream. It is what "what must already be done if
    this is under way" means as a computation rather than as a judgement call.
    """
    _, back = _graph(project)
    return reachable(back, [uid], include_starts=include_self)


# ── folders ──────────────────────────────────────────────────────────────────

def _children(project: Project) -> Dict[Optional[str], List[WBSNode]]:
    out: Dict[Optional[str], List[WBSNode]] = {}
    for w in project.wbs_nodes:
        out.setdefault(w.parent_uid, []).append(w)
    return out


def _path_of(project: Project, node: WBSNode) -> str:
    by_uid = {w.uid: w for w in project.wbs_nodes}
    seg, cur, guard = [], node, 0
    while cur is not None and guard < 100:
        guard += 1
        seg.append(cur.name or "")
        cur = by_uid.get(cur.parent_uid) if cur.parent_uid else None
    return " / ".join(reversed(seg))


def _subtree_activities(project: Project, root: WBSNode,
                        kids: Dict[Optional[str], List[WBSNode]],
                        acts_in: Dict[Optional[str], List]) -> List:
    out, stack = [], [root]
    while stack:
        cur = stack.pop()
        out.extend(acts_in.get(cur.uid, ()))
        stack.extend(kids.get(cur.uid, ()))
    return out


def _source_folders(project: Project, rx: "re.Pattern",
                    under: Optional[WBSNode]) -> List[WBSNode]:
    """
    The folders to check, each counted ONCE.

    "Gen 315", "Gen 315 - JER" and "Gen 315 - WBO" all match a pattern for a
    generator room, and they are one room — the sub-folders are how its own
    work is split up. Taking all three as separate sources would ask the same
    question three times and, worse, would connect a room's WBO sub-folder to
    commissioning as if it were a room in its own right. So a match nested
    inside another match is folded into its ancestor.
    """
    kids = _children(project)
    matched = [w for w in project.wbs_nodes if rx.search(w.name or "")]
    if under is not None:
        inside = {under.uid}
        stack = [under]
        while stack:
            for c in kids.get(stack.pop().uid, ()):
                inside.add(c.uid)
                stack.append(c)
        matched = [w for w in matched if w.uid in inside]

    hit = {w.uid for w in matched}
    by_uid = {w.uid: w for w in project.wbs_nodes}

    def nested(w: WBSNode) -> bool:
        cur, guard = by_uid.get(w.parent_uid) if w.parent_uid else None, 0
        while cur is not None and guard < 100:
            guard += 1
            if cur.uid in hit:
                return True
            cur = by_uid.get(cur.parent_uid) if cur.parent_uid else None
        return False

    return [w for w in matched if not nested(w)]


# ── pairing ──────────────────────────────────────────────────────────────────

def _key_from(text: str, rx: "re.Pattern") -> str:
    m = rx.search(text or "")
    if not m:
        return ""
    return (m.group(1) if m.groups() else m.group(0)).strip().upper()


def _d(v) -> str:
    return str(v or "")[:10]


def _finish_of(a) -> str:
    return _d(a.actual_finish) or _d(a.planned_finish) or _d(a.planned_start) or ""


def _start_of(a) -> str:
    return _d(a.actual_start) or _d(a.planned_start) or _d(a.planned_finish) or "9999"


# ── the audit ────────────────────────────────────────────────────────────────

def audit(project: Project,
          folder_pattern: str,
          target_pattern: str,
          scope_pattern: str = DEFAULT_SCOPE,
          under: Optional[WBSNode] = None,
          target_pick: str = "earliest",
          tail: str = "last") -> Dict[str, Any]:
    """
    For every folder matching `folder_pattern`, can it reach a target?

    target_pattern   regex over ACTIVITY names, e.g. "commissioning"
    scope_pattern    regex whose capture group is the key both sides must
                     share — by default the phase number
    target_pick      which target in the scope to aim at when several match:
                     "earliest" (the one the work must precede), "latest"
    tail             which activity in the folder carries the tie out:
                     "last"  the latest-finishing activity that nothing inside
                             the folder waits on — the room's termination
                     "open"  every activity with no successor at all
                     "all"   every activity nothing inside the folder waits on

    Nothing is written. The result is the input to `connect`.
    """
    if tail not in ("last", "open", "all"):
        raise ValueError(f"tail must be last, open or all — not {tail!r}")
    if target_pick not in ("earliest", "latest"):
        raise ValueError(f"target_pick must be earliest or latest — not {target_pick!r}")

    f_rx = _rx(folder_pattern, "folder_pattern")
    t_rx = _rx(target_pattern, "target_pattern")
    s_rx = _rx(scope_pattern, "scope_pattern")

    kids = _children(project)
    acts_in: Dict[Optional[str], List] = {}
    for a in project.activities:
        acts_in.setdefault(a.wbs_uid, []).append(a)
    wbs_by_uid = {w.uid: w for w in project.wbs_nodes}
    path_cache: Dict[str, str] = {}

    def act_path(a) -> str:
        w = wbs_by_uid.get(a.wbs_uid)
        if w is None:
            return ""
        if w.uid not in path_cache:
            path_cache[w.uid] = _path_of(project, w)
        return path_cache[w.uid]

    # targets, bucketed by the scope key each one carries
    targets: Dict[str, List] = {}
    target_total = 0
    for a in project.activities:
        if not t_rx.search(a.name or ""):
            continue
        target_total += 1
        key = _key_from(act_path(a) + " / " + (a.name or ""), s_rx)
        if key:
            targets.setdefault(key, []).append(a)
    for key in targets:
        targets[key].sort(key=lambda x: (_start_of(x), x.activity_id or ""))

    fwd, _back = _graph(project)
    folders = _source_folders(project, f_rx, under)

    connected, missing, unresolved = [], [], []
    for w in folders:
        acts = _subtree_activities(project, w, kids, acts_in)
        path = _path_of(project, w)
        if not acts:
            unresolved.append({"folder": w.name, "path": path,
                               "why": "no activities in it"})
            continue

        key = _key_from(path + " / " + (w.name or ""), s_rx)
        if not key:
            unresolved.append({"folder": w.name, "path": path,
                               "why": "no scope key could be read from its path"})
            continue
        pool = targets.get(key) or []
        if not pool:
            unresolved.append({"folder": w.name, "path": path,
                               "why": f"nothing matching the target in scope {key}"})
            continue

        uids = {a.uid for a in acts}
        target_uids = {t.uid for t in pool}
        got = reachable(fwd, uids) & target_uids
        if got:
            hit = [t for t in pool if t.uid in got]
            connected.append({
                "folder": w.name, "path": path, "scope": key,
                "activities": len(acts),
                "reaches": [{"activity_id": t.activity_id, "name": t.name}
                            for t in hit],
            })
            continue

        # It does not get there. Which activity should carry the tie out?
        if tail == "open":
            ends = [a for a in acts if not fwd.get(a.uid)]
        else:
            # Nothing INSIDE the folder waits on it — so it is one of the
            # folder's endpoints. Not "nothing inside precedes it": that is
            # the folder's heads, which is the same set read backwards and
            # would tie commissioning to the first activity in the room.
            ends = [a for a in acts
                    if not any(s in uids for s in fwd.get(a.uid, ()))]
        if not ends:
            unresolved.append({"folder": w.name, "path": path,
                               "why": "every activity in it is a predecessor of "
                                      "another one inside it (a cycle)"})
            continue
        ends.sort(key=lambda x: (_finish_of(x), x.activity_id or ""))
        if tail == "last":
            ends = ends[-1:]

        tgt = pool[0] if target_pick == "earliest" else pool[-1]
        # A tie that the target already leads back to would close a loop. P6
        # will not schedule a loop and the import would be the place it
        # surfaced, so it is refused here with the reason named.
        behind = reachable(fwd, [tgt.uid])
        ends = [a for a in ends if a.uid not in behind]
        if not ends:
            unresolved.append({
                "folder": w.name, "path": path,
                "why": f"{tgt.activity_id} already leads back into it — "
                       f"tying it would make a loop"})
            continue

        missing.append({
            "folder": w.name, "path": path, "scope": key,
            "activities": len(acts),
            "target": {"activity_id": tgt.activity_id, "name": tgt.name},
            "ties": [{"activity_id": a.activity_id, "name": a.name,
                      "finish": _finish_of(a), "uid": a.uid} for a in ends],
            "target_uid": tgt.uid,
        })

    return {
        "action": "connect_folders",
        "applied": False,
        "folder_pattern": folder_pattern,
        "target_pattern": target_pattern,
        "scope_pattern": scope_pattern,
        "tail": tail,
        "folders_checked": len(folders),
        "targets_found": target_total,
        "scopes": sorted(targets),
        "connected": connected,
        "missing": missing,
        "unresolved": unresolved,
        "relations_needed": sum(len(m["ties"]) for m in missing),
    }


def connect(project: Project,
            folder_pattern: str,
            target_pattern: str,
            scope_pattern: str = DEFAULT_SCOPE,
            under: Optional[WBSNode] = None,
            target_pick: str = "earliest",
            tail: str = "last",
            relation_type: str = "Finish to Start",
            lag_days: float = 0.0,
            apply: bool = False) -> Dict[str, Any]:
    """
    Audit, and — with apply=True — add the ties that are missing.

    Only ever adds. A folder already reaching its target keeps whatever route
    it has; nothing existing is rewritten, reordered or removed.
    """
    res = audit(project, folder_pattern, target_pattern, scope_pattern,
                under, target_pick, tail)
    if not apply or not res["missing"]:
        return res

    have = {(r.predecessor_uid, r.successor_uid) for r in project.relations}
    added = 0
    for m in res["missing"]:
        for t in m["ties"]:
            pair = (t["uid"], m["target_uid"])
            if pair in have:
                continue
            project.relations.append(Relation(
                uid=_uid(), predecessor_uid=pair[0], successor_uid=pair[1],
                type=relation_type, lag=float(lag_days) * 8.0,
            ))
            have.add(pair)
            added += 1

    project.build_lookups()
    # The dates the schedule shows must agree with the logic it now has, but a
    # tie is not a reschedule: float and the critical path are refreshed,
    # Start and Finish are not rewritten.
    try:
        from .schedule_model import compute_dates
        compute_dates(project, apply_dates=False)
    except Exception:
        pass

    res["applied"] = True
    res["relations_added"] = added
    return res


def describe(result: Dict[str, Any], limit: int = 14) -> str:
    """The audit as something a person can act on."""
    n_ok = len(result["connected"])
    n_bad = len(result["missing"])
    n_huh = len(result["unresolved"])
    checked = result["folders_checked"]

    if not checked:
        return (f"No folders matched {result['folder_pattern']!r}. Nothing was "
                f"checked — try a looser pattern.")
    if not result["targets_found"]:
        return (f"No activity matched {result['target_pattern']!r}, so there is "
                f"nothing to connect {checked} folder"
                f"{'' if checked == 1 else 's'} to.")

    out = []
    if result["applied"]:
        out.append(f"Tied {result.get('relations_added', 0)} loose end"
                   f"{'' if result.get('relations_added') == 1 else 's'} into "
                   f"{n_bad} folder{'' if n_bad == 1 else 's'} that were not "
                   f"reaching their target.")
    else:
        out.append(f"Checked {checked} folder{'' if checked == 1 else 's'} "
                   f"against {result['targets_found']} matching activit"
                   f"{'y' if result['targets_found'] == 1 else 'ies'}, "
                   f"paired by scope.")
    out.append(f"  {n_ok} already reach their target — left exactly as they are.")
    out.append(f"  {n_bad} do not"
               + (f" ({result['relations_needed']} tie"
                  f"{'' if result['relations_needed'] == 1 else 's'} "
                  f"{'added' if result['applied'] else 'would be added'})."
                  if n_bad else "."))
    if n_huh:
        out.append(f"  {n_huh} could not be paired at all — listed below.")

    if n_bad:
        out.append("")
        out.append("Not reaching:" if not result["applied"] else "Tied:")
        for m in result["missing"][:limit]:
            arrow = ", ".join(t["activity_id"] for t in m["ties"])
            out.append(f"  {m['folder']}  (scope {m['scope']}, "
                       f"{m['activities']} activities)")
            out.append(f"      {arrow}  ->  {m['target']['activity_id']}  "
                       f"{m['target']['name']}")
        if n_bad > limit:
            out.append(f"  …and {n_bad - limit} more")

    if n_huh:
        out.append("")
        out.append("Skipped — nothing was guessed for these:")
        for u in result["unresolved"][:limit]:
            out.append(f"  {u['folder']}: {u['why']}")
        if n_huh > limit:
            out.append(f"  …and {n_huh - limit} more")

    if not result["applied"] and n_bad:
        out.append("")
        out.append("Nothing has been changed. Say go ahead to add these ties.")
    return "\n".join(out)
