# -*- coding: utf-8 -*-
"""
procurement_wire.py — tie long-lead delivery to the work it feeds, and copy a
sequence that already works onto the places that need it.

TWO OPERATIONS, ONE IDEA
  Both are about applying something already known rather than guessing. The
  first knows what equipment feeds what installation; the second knows how one
  area was built and puts the same shape on the areas that were not.

DELIVERY -> INSTALL
  procurement_report already matches an LLE line to the installs it feeds by
  equipment kind, and flags installs dated ahead of their own delivery. What
  it could not do was make the tie. Now it can — with one refusal that is the
  whole point:

    an install dated BEFORE its delivery is never tied.

  Forcing that tie would move the install out and quietly "solve" a conflict
  that a human needs to decide about: either the procurement date is wrong,
  or the work genuinely cannot happen as scheduled, and both are decisions
  about the job rather than about the network. Papering over it with a
  relationship is how a schedule becomes confidently wrong. Those are
  reported, loudly, and left alone.

REPLICATING A SEQUENCE
  Areas of the same kind are built the same way. When one has been wired
  properly and the others have not, the shape is already in the schedule —
  copying it is not a guess, it is applying the user's own decision.

  Matched by WORK rather than by exact name, the same normalisation the crew
  fill and the folder fill use, so "Pull Wire MV 101" and "Pull Wire MV 105"
  are the same task. A tie is only laid where BOTH ends exist in the target
  and nothing already connects them, so an area that was partly wired keeps
  what it has.
"""

from typing import Any, Dict, List, Optional


def _d(v) -> str:
    return str(v or "")[:10]


def wire_procurement(project, needle: Optional[str] = None,
                     max_items: int = 60) -> Dict[str, Any]:
    """
    Ties from each delivery to the first install it feeds — except where the
    install is dated before the delivery, which is reported instead.
    """
    from engine.logic_advisor import _has_link, procurement_report

    rep = procurement_report(project, needle, max_items=max_items)
    by_id = {a.activity_id: a for a in project.activities}

    cmds, tied, blocked = [], [], []
    for item in rep["items"]:
        s = by_id.get(item["supply_id"])
        i = by_id.get(item["first_install_id"])
        if s is None or i is None:
            continue
        # Two separate guards, and the second is the one that matters.
        #
        # installed_before_delivery is procurement_report's judgement, which
        # forgives a few install names that legitimately start before the
        # equipment lands (layout, pads, rough-in). That forgiveness is right
        # for a REPORT and wrong for a TIE: the implied lag is still negative,
        # so laying the relationship would push the install out and silently
        # change dates the user entered. Anything that does not already sit
        # after its delivery is reported, never wired — on the reference file
        # that is what stops a generator submittal being tied to steel it was
        # only matched to by the word "Gen".
        lag = item.get("implied_lag_days")
        if item["installed_before_delivery"] or (lag is not None and lag < 0):
            blocked.append(item)
            continue
        if _has_link(project, s.uid, i.uid):
            continue
        cmds.append({"action": "add_relation",
                     "predecessor_id": s.activity_id,
                     "successor_id": i.activity_id,
                     "type": "fs", "lag_days": 0})
        tied.append(item)

    return {"commands": cmds, "tied": tied, "blocked": blocked,
            "scope": rep["scope"], "supply_lines": rep["supply_lines"],
            "matched": rep["matched"]}


def procurement_report_text(project, needle: Optional[str] = None) -> str:
    r = wire_procurement(project, needle)
    if not r["matched"]:
        return (f"No long-lead line in {r['scope']} matches an installation by "
                f"equipment. Either the procurement folder is named differently "
                f"or the equipment names do not line up.")
    out = [f"PROCUREMENT → INSTALL ({r['scope']}) — {r['supply_lines']} supply "
           f"line(s), {r['matched']} matched to work. Nothing applied."]
    if r["tied"]:
        out.append(f"\n  {len(r['tied'])} tie(s) would be made:")
        for i in r["tied"][:15]:
            out.append(f"    {i['supply_id']} {i['supply_name']}"
                       f"\n       → {i['first_install_id']} {i['first_install_name']}"
                       f"  ({i['implied_lag_days']}d gap, feeds {i['installs_fed']})")
        if len(r["tied"]) > 15:
            out.append(f"    …and {len(r['tied']) - 15} more")
    if r["blocked"]:
        out.append(f"\n  ⚠ {len(r['blocked'])} NOT tied — the install is dated "
                   f"BEFORE its own delivery. Tying these would push the work "
                   f"out and hide the conflict; either the procurement date is "
                   f"wrong or the work cannot happen as scheduled:")
        for i in r["blocked"][:12]:
            out.append(f"    {i['first_install_id']} {i['first_install_name']}"
                       f" starts {i['first_install_start']}"
                       f"\n       but {i['supply_id']} {i['supply_name']}"
                       f" arrives {i['supply_finish']}")
        if len(r["blocked"]) > 12:
            out.append(f"    …and {len(r['blocked']) - 12} more")
    return "\n".join(out)


# ── replicating a sequence that already works ────────────────────────────────

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


def replicate_pattern(project, source_ref: str, targets: List[str],
                      brain=None) -> Dict[str, Any]:
    """
    Copy the internal sequence of one folder onto others of the same kind.

    Only ties whose BOTH ends exist in the target are laid, and only where
    nothing already connects them — so a partly-wired area keeps its own
    logic and gains the rest.
    """
    from engine.edit_engine import _find_wbs, _norm_name
    from engine.logic_advisor import _has_link

    src = _find_wbs(project, source_ref, source_ref, source_ref)
    if not src:
        return {"error": f"No folder matching '{source_ref}'"}

    src_scope = _descendants(project, src.uid)
    src_acts = [a for a in project.activities if a.wbs_uid in src_scope]
    if not src_acts:
        return {"error": f"'{src.name}' holds no activities"}

    src_uids = {a.uid for a in src_acts}
    by_uid = {a.uid: a for a in project.activities}
    internal = [(by_uid[r.predecessor_uid], by_uid[r.successor_uid], r)
                for r in project.relations
                if r.predecessor_uid in src_uids and r.successor_uid in src_uids
                and r.predecessor_uid in by_uid and r.successor_uid in by_uid]
    if not internal:
        return {"error": f"'{src.name}' has no internal logic to copy — wire it "
                         f"first, then replicate it."}

    out: Dict[str, Any] = {"source": src.name, "pattern_ties": len(internal),
                           "commands": [], "per_folder": [], "missing": []}

    for ref in targets:
        tgt = _find_wbs(project, ref, ref, ref)
        if not tgt or tgt.uid == src.uid:
            out["missing"].append(ref)
            continue
        t_scope = _descendants(project, tgt.uid)
        t_acts = [a for a in project.activities if a.wbs_uid in t_scope]
        index: Dict[str, Any] = {}
        for a in t_acts:
            index.setdefault(_norm_name(a.name), a)

        made, absent = 0, 0
        for p_act, s_act, rel in internal:
            tp = index.get(_norm_name(p_act.name))
            ts = index.get(_norm_name(s_act.name))
            if tp is None or ts is None:
                absent += 1
                continue
            if tp.uid == ts.uid or _has_link(project, tp.uid, ts.uid):
                continue
            out["commands"].append({
                "action": "add_relation",
                "predecessor_id": tp.activity_id,
                "successor_id": ts.activity_id,
                "type": {"Finish to Start": "fs", "Start to Start": "ss",
                         "Finish to Finish": "ff",
                         "Start to Finish": "sf"}.get(rel.type, "fs"),
                "lag_days": int((rel.lag or 0) / 8)})
            made += 1
        out["per_folder"].append({"folder": tgt.name, "ties": made,
                                  "not_present": absent,
                                  "activities": len(t_acts)})
    return out


def replicate_report(project, source_ref: str, targets: List[str],
                     brain=None) -> str:
    r = replicate_pattern(project, source_ref, targets, brain)
    if r.get("error"):
        return r["error"]
    ties = len(r["commands"])
    out = [f"REPLICATE '{r['source']}' ({r['pattern_ties']} ties in its "
           f"pattern) onto {len(r['per_folder'])} folder(s) — {ties} tie(s) "
           f"would be added. Nothing applied."]
    for f in r["per_folder"]:
        line = f"  {f['folder']}: +{f['ties']} tie(s)"
        if f["not_present"]:
            line += (f"  ({f['not_present']} of the pattern's ties skipped — "
                     f"that work is not in this folder)")
        out.append(line)
    if r["missing"]:
        out.append(f"  Not found: {', '.join(r['missing'])}")
    if not ties:
        out.append("  Nothing to add — these folders already carry the "
                   "pattern, or none of the pattern's work exists in them.")
    return "\n".join(out)
