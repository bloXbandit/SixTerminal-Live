# -*- coding: utf-8 -*-
"""
procurement_map.py — does every major system arrive before the work that needs it?

WHY THIS, WHEN procurement_report ALREADY EXISTS
  procurement_report works one supply LINE at a time: it takes an LLE row,
  finds the first install it feeds, and compares two dates. That answers "is
  this one line ahead of that one activity". It cannot answer the question a
  scheduler actually asks, which is about a SYSTEM:

      "Are the chillers covered? All of them, everywhere they are needed?"

  A line-at-a-time view misses three things that matter. It stops at the first
  install, so the second and fortieth consumer of the same equipment are never
  checked. It has no notion of phase, so a Phase 1 generator delivery can look
  like it covers Phase 3 work that arrives eight months later. And it says
  nothing about whether a relationship EXISTS — dates that happen to line up
  today are not the same as a schedule that keeps them lined up when something
  moves.

WHAT A SYSTEM IS
  One kind of equipment within one phase: "Generators — Phase 2". That is the
  unit a procurement conversation is actually held in, and it is the unit the
  chart shows one row for.

  Deliveries are the supply lines in the procurement/LLE folders. Consumers
  are the activities elsewhere that handle that equipment — with two
  refinements that decide whether the answer is worth anything:

    Location is not equipment. "OH Lighting (Gen 325)" is lighting in
    generator room 325. It needs the room, not a generator. See
    logic_advisor.strip_location — on the reference schedule this is the
    difference between 159 real generator consumers and 603 apparent ones.

    Some work legitimately precedes delivery. Pads, layout, hangers, high
    steel and rough-in are all done BEFORE the equipment lands; a map that
    demanded the generator arrive before its own housekeeping pad would flag
    the correct sequence as an error. Those rows are counted as consumers but
    excluded from the need-date.

THE FOUR VERDICTS, AND WHY THE MIDDLE ONE MATTERS MOST
  AT RISK    work is dated before its equipment can arrive. A real conflict:
             either the delivery date is wrong or that work cannot happen.
  NO LOGIC   the dates are fine, but nothing in the network connects the
             delivery to the work. This is the one people miss. It is not a
             problem today and becomes one silently the moment either end
             moves, because nothing is holding the gap open.
  READY      dates work AND a logic path carries the delivery into the work.
  NO DELIVERY there is work for this equipment and no supply line feeding it.

  Coverage is REACHABILITY, not a direct link. A delivery that ties to the
  first install, which ties onward to the rest, has covered all of them —
  demanding a direct relationship to each would report a correctly-wired
  schedule as broken and invite a hundred redundant ties.

NOTHING HERE WRITES
  Every function in this module is read-only. The ties it suggests are made
  by procurement_wire, which has its own refusal to tie work dated before its
  own delivery — a conflict is a decision about the job, not a gap in the
  network, and papering over it with a relationship is how a schedule becomes
  confidently wrong.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

READY       = "READY"
NO_LOGIC    = "NO_LOGIC"
AT_RISK     = "AT_RISK"
NO_DELIVERY = "NO_DELIVERY"
NO_CONSUMER = "NO_CONSUMER"

# Worst first — this is the order the chart lists rows in, because the point
# of the chart is the problems.
_RANK = {AT_RISK: 0, NO_DELIVERY: 1, NO_LOGIC: 2, NO_CONSUMER: 3, READY: 4}

_ICON = {READY: "🟢", NO_LOGIC: "🟡", AT_RISK: "🔴",
         NO_DELIVERY: "⚪", NO_CONSUMER: "⚪"}

_LABEL = {READY: "READY", NO_LOGIC: "DATES OK, NO LOGIC", AT_RISK: "AT RISK",
          NO_DELIVERY: "NO DELIVERY LINE", NO_CONSUMER: "NOTHING CONSUMES IT"}


def _d(v) -> str:
    return str(v or "")[:10]


_PHASE_PATH = re.compile(r"\bphase\s*(\d+)", re.I)
_PHASE_ID = re.compile(r"\bPH(\d)\b", re.I)


def _phase_of(project, act) -> str:
    """
    Which phase this activity belongs to, from its folder path or its id.

    Phase matters because equipment is bought per phase: a Phase 1 generator
    delivery says nothing about whether Phase 3 is covered, and a map that
    pooled them would report a phase as ready on the strength of another
    phase's equipment.
    """
    from engine.logic_advisor import wbs_path
    m = _PHASE_PATH.search(wbs_path(project, act) or "")
    if m:
        return f"Phase {m.group(1)}"
    m = _PHASE_ID.search(act.activity_id or "")
    return f"Phase {m.group(1)}" if m else "Unphased"


def _is_procurement_folder(project, act) -> bool:
    from engine.logic_advisor import wbs_path
    p = (wbs_path(project, act) or "").lower()
    return ("lle" in p or "procurement" in p or "long lead" in p
            or "submittal" in p)


# A supply line is the thing ARRIVING, not the paperwork that precedes it.
# The reference schedule carries a full upstream chain per system — selection,
# pricing, OAA approval, submittal development, submittal review — and every
# one of those rows names the equipment. Treating them as deliveries would put
# the "arrival" date months before the truck shows up and report a job as
# comfortably covered when it is not.
_UPSTREAM_WORDS = ("submittal", "shop drawing", "review", "approval", "oaa",
                   "selection", "pricing", "specs", "spec ", "design",
                   "coordination", "award", "funding", "permit", "loi",
                   "pcco", "gmp", "development", "buyout", "bid")


def _is_upstream(name: str) -> bool:
    low = (name or "").lower()
    return any(w in low for w in _UPSTREAM_WORDS)


def _multi_source_reach(project, sources: Set[str],
                        adj: Optional[Dict[str, List[str]]] = None) -> Set[str]:
    """
    Everything reachable forward from any of these. Guarded against cycles.

    The caller passes the adjacency in because this runs once per system per
    phase — fifty-odd times on the reference schedule — and rebuilding it from
    2,106 relations each time is the whole cost of the map.
    """
    if adj is None:
        adj = {}
        for r in project.relations:
            adj.setdefault(r.predecessor_uid, []).append(r.successor_uid)
    seen, stack, guard = set(sources), list(sources), 0
    while stack and guard < 400000:
        guard += 1
        for v in adj.get(stack.pop(), ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def analyse(project, phase: Optional[str] = None,
            system: Optional[str] = None) -> Dict[str, Any]:
    """
    One row per major system per phase: when it arrives, when it is first
    needed, how much room that leaves, and whether anything in the network is
    actually holding the two together.
    """
    from engine.logic_advisor import (_PRE_DELIVERY_OK, _calendar_of,
                                      _equipment_of, strip_location,
                                      wbs_path, working_days_between)

    want_sys = (system or "").strip().lower() or None
    want_ph = (phase or "").strip().lower() or None

    # Bucket every activity by (equipment, phase) once, rather than scanning
    # the job per system — on 2,400 activities across 20-odd systems that is
    # the difference between one pass and fifty.
    deliveries: Dict[Tuple[str, str], List[Any]] = {}
    consumers: Dict[Tuple[str, str], List[Any]] = {}
    for a in project.activities:
        kinds = _equipment_of(a.name)
        if not kinds:
            continue
        ph = _phase_of(project, a)
        proc = _is_procurement_folder(project, a)
        for k in kinds:
            key = (k, ph)
            if proc and not _is_upstream(a.name):
                deliveries.setdefault(key, []).append(a)
            elif not proc:
                consumers.setdefault(key, []).append(a)

    fwd: Dict[str, List[str]] = {}
    for r in project.relations:
        fwd.setdefault(r.predecessor_uid, []).append(r.successor_uid)

    rows: List[Dict[str, Any]] = []
    for key in sorted(set(deliveries) | set(consumers)):
        kind, ph = key
        if want_sys and want_sys not in kind:
            continue
        if want_ph and want_ph not in ph.lower():
            continue

        dels = deliveries.get(key, [])
        cons = consumers.get(key, [])
        if not dels and not cons:
            continue

        # Some equipment is bought once for the whole site and consumed by
        # every phase — on the reference schedule there is exactly one chiller
        # delivery, in the Phase 1 LLE folder, feeding chiller work in all
        # three phases. Insisting on a delivery filed under the same phase
        # reported those as having no supply line at all, which is both wrong
        # and the kind of false alarm that makes people stop reading a report.
        # So fall back to the project's deliveries for this equipment, and say
        # that is what happened rather than passing it off as a phase match.
        cross_phase_from = None
        if not dels:
            other = [(ph2, acts) for (k2, ph2), acts in deliveries.items()
                     if k2 == kind and acts]
            if other:
                dels = [a for _, acts in other for a in acts]
                cross_phase_from = sorted({ph for ph, _ in other})

        # Work that legitimately precedes delivery is a consumer of the system
        # but not a claim on its arrival date — see the module docstring.
        needs_it = [a for a in cons
                    if not any(w in strip_location(a.name).lower()
                               for w in _PRE_DELIVERY_OK)]

        # ALL of it has to land, so the arrival is the LAST delivery finishing;
        # the first claim on it is the EARLIEST start among work that needs it.
        # The arrival is the LAST delivery to finish, because all of it has to
        # land before the system is there. Ties go to a line that names this
        # equipment and nothing else — quoting "MVR Skids 2 (GIS RMU, XFMR,
        # MSG)" as the representative of nineteen "Transformer B01" rows reads
        # as though the finding were about the skid.
        def _arrival_key(a):
            from engine.logic_advisor import _equipment_of as _eq
            return (_d(a.planned_finish), _eq(a.name) == [kind])
        arrival_act = max(dels, key=_arrival_key) if dels else None
        need_act = (min((a for a in needs_it if a.planned_start),
                        key=lambda a: _d(a.planned_start), default=None))

        arrival = _d(arrival_act.planned_finish) if arrival_act else None
        need = _d(need_act.planned_start) if need_act else None

        buffer_days = None
        if arrival and need:
            raw = working_days_between(
                arrival, need, _calendar_of(project, need_act))
            # The delivery's own finish day is worked, so work starting the
            # next working day is back-to-back and reads as zero buffer.
            buffer_days = None if raw is None else raw - 1

        # Coverage is reachability, not a direct link: a delivery tied to the
        # first install, which carries on to the rest, has covered them all.
        covered_ids: List[str] = []
        uncovered: List[Any] = []
        if dels and cons:
            reach = _multi_source_reach(project, {a.uid for a in dels}, fwd)
            for a in cons:
                if a.uid in reach:
                    covered_ids.append(a.activity_id)
                else:
                    uncovered.append(a)

        # A skid arrives as one unit and its name lists what is inside it —
        # "MV Skids 1 (XFMR, PDP 3000A, UPS, Battery Cabinet)". Each of those
        # words matches a system, so the battery and the PDP each get a row
        # with a delivery and nothing consuming it. That is not a finding, it
        # is one delivery being read four ways, and saying so is the
        # difference between a useful ⚪ and noise.
        # But a line that names ONLY this equipment is a dedicated delivery,
        # and if one of those exists the compound story is not the
        # explanation. On the reference schedule Phase 2 has 22 transformer
        # deliveries: three are skids that merely list "XFMR" among their
        # contents, and nineteen are "Transformer B01 (2500KVA)" rows in their
        # own folder. Calling that a skid component would bury the actual
        # finding, which is that nineteen transformers arrive and nothing in
        # the schedule installs them.
        compound_with: List[str] = []
        standalone = 0
        if dels and not cons:
            from engine.logic_advisor import _equipment_of as _eq
            others: Set[str] = set()
            for a in dels:
                kinds_here = _eq(a.name)
                if kinds_here == [kind]:
                    standalone += 1
                others |= {k for k in kinds_here if k != kind}
            if not standalone:
                consuming_kinds = {k for (k, _), v in consumers.items() if v}
                compound_with = sorted(others & consuming_kinds)

        if cons and not dels:
            verdict = NO_DELIVERY
        elif dels and not cons:
            verdict = NO_CONSUMER
        elif buffer_days is not None and buffer_days < 0:
            verdict = AT_RISK
        elif not covered_ids:
            verdict = NO_LOGIC
        elif uncovered:
            verdict = NO_LOGIC
        else:
            verdict = READY

        rows.append({
            "system": kind,
            "phase": ph,
            "verdict": verdict,
            "icon": _ICON[verdict],
            "label": _LABEL[verdict],
            "cross_phase_from": cross_phase_from,
            "compound_with": compound_with,
            "standalone_deliveries": standalone,
            "deliveries": len(dels),
            "consumers": len(cons),
            "needs_delivery": len(needs_it),
            "covered": len(covered_ids),
            "uncovered": len(uncovered),
            "arrival": arrival,
            "need": need,
            "buffer_days": buffer_days,
            "arrival_id": arrival_act.activity_id if arrival_act else None,
            "arrival_name": arrival_act.name if arrival_act else None,
            "need_id": need_act.activity_id if need_act else None,
            "need_name": need_act.name if need_act else None,
            "need_folder": (wbs_path(project, need_act).split(" / ")[-1]
                            if need_act else None),
            "delivery_ids": [a.activity_id for a in dels],
            "uncovered_ids": [a.activity_id for a in uncovered],
            "uncovered_sample": [
                {"activity_id": a.activity_id, "name": a.name,
                 "start": _d(a.planned_start),
                 "folder": wbs_path(project, a).split(" / ")[-1]}
                for a in sorted(uncovered, key=lambda x: _d(x.planned_start))[:8]],
        })

    # A delivery with a dedicated line and nothing installing it is a real
    # finding; the same verdict reached because a skid's name lists its own
    # contents is not. Same verdict, different weight — so the sort separates
    # them rather than letting the components bury the orphans.
    def _sort_key(r):
        rank = _RANK[r["verdict"]]
        if r["verdict"] == NO_CONSUMER and not r["compound_with"]:
            rank = _RANK[NO_DELIVERY] + 0.5
        return (rank,
                r["buffer_days"] if r["buffer_days"] is not None else 9999,
                r["system"], r["phase"])

    rows.sort(key=_sort_key)

    tally: Dict[str, int] = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1

    return {
        "systems": rows,
        "count": len(rows),
        "tally": tally,
        "at_risk": tally.get(AT_RISK, 0),
        "no_logic": tally.get(NO_LOGIC, 0),
        "ready": tally.get(READY, 0),
        "phases": sorted({r["phase"] for r in rows}),
        "scope": (f"{system or 'all systems'}"
                  + (f", {phase}" if phase else "")),
    }


def story(project, system: str, phase: Optional[str] = None) -> str:
    """
    One system, told as the sentence a scheduler would say out loud.

    This is the articulation the chart links to: not "chiller: 🟡" but "the
    chillers land on this date, the first work needing them starts on that
    date, here is the room in between and here is what is not connected to it".
    """
    d = analyse(project, phase=phase, system=system)
    if not d["systems"]:
        return (f"Nothing in the schedule refers to '{system}'"
                + (f" in {phase}" if phase else "")
                + ". Either it is named differently here or it is not "
                  "procured as a separate system.")

    out: List[str] = []
    for r in d["systems"]:
        head = f"{r['icon']} {r['system'].upper()} — {r['phase']}"
        out.append(head)

        if r["verdict"] == NO_DELIVERY:
            out.append(f"  {r['consumers']} activities handle {r['system']} in "
                       f"{r['phase']}, and no supply line feeds them. The "
                       f"earliest is {r['need_id']} {r['need_name']} on "
                       f"{r['need'] or 'no date'}. Nothing in the schedule "
                       f"says when this equipment arrives.")
            out.append("")
            continue

        if r["verdict"] == NO_CONSUMER:
            out.append(f"  {r['arrival_id']} {r['arrival_name']} arrives "
                       f"{r['arrival']}, and no work in {r['phase']} names "
                       f"this equipment.")
            if r["compound_with"]:
                out.append(f"  That line is a combined delivery — it also "
                           f"carries {', '.join(r['compound_with'])}, which do "
                           f"have work. This is most likely a component of "
                           f"that unit rather than something bought and "
                           f"installed on its own.")
            else:
                out.append(f"  ⚠ {r['standalone_deliveries']} line(s) name this "
                           f"equipment and nothing else, so this is a delivery "
                           f"in its own right — and no activity in "
                           f"{r['phase']} installs it. Either that work is "
                           f"named differently or it is missing from the "
                           f"schedule.")
            out.append("")
            continue

        out.append(f"  {r['arrival_id']} {r['arrival_name']} arrives "
                   f"{r['arrival']}"
                   + (f" (last of {r['deliveries']} deliveries)"
                      if r["deliveries"] > 1 else "") + ".")
        if r["cross_phase_from"]:
            out.append(f"  Note: {r['phase']} has no delivery line of its own "
                       f"for this — it is fed from "
                       f"{', '.join(r['cross_phase_from'])}, which is normal "
                       f"for equipment bought once for the whole site. If it "
                       f"is meant to be bought per phase, this phase is "
                       f"missing its line.")
        out.append(f"  The first work needing it is {r['need_id']} "
                   f"{r['need_name']} on {r['need']}"
                   + (f", in {r['need_folder']}" if r["need_folder"] else "") + ".")

        if r["buffer_days"] is None:
            out.append("  One of the two has no date, so the gap cannot be "
                       "measured.")
        elif r["buffer_days"] < 0:
            out.append(f"  ⚠ That is {-r['buffer_days']} working days BEFORE "
                       f"the equipment arrives. Either the delivery date is "
                       f"wrong or this work cannot happen as scheduled — it is "
                       f"a decision about the job, so nothing here will tie it "
                       f"shut.")
        elif r["buffer_days"] == 0:
            out.append("  It starts the working day after delivery — "
                       "back-to-back, with no room at all.")
        else:
            out.append(f"  {r['buffer_days']} working days of room.")

        out.append(f"  {r['covered']} of {r['consumers']} activities using this "
                   f"equipment are downstream of the delivery.")
        if r["uncovered"]:
            out.append(f"  ⚠ {r['uncovered']} are NOT — nothing in the network "
                       f"holds them behind the delivery, so they will not move "
                       f"if it slips:")
            for u in r["uncovered_sample"]:
                out.append(f"      {u['activity_id']}  {u['start'] or '—'}  "
                           f"{u['name']}  ·  {u['folder']}")
            if r["uncovered"] > len(r["uncovered_sample"]):
                out.append(f"      …and {r['uncovered'] - len(r['uncovered_sample'])} more")
        out.append("")
    return "\n".join(out).rstrip()


def report(project, phase: Optional[str] = None, max_rows: int = 40) -> str:
    """The whole map as prose — the chart's contents, for chat and the agent."""
    d = analyse(project, phase=phase)
    if not d["count"]:
        return ("No major equipment system could be matched. Either the "
                "procurement folders are named differently or the equipment "
                "names do not line up with the work.")

    out = [f"PROCUREMENT MAP — {d['count']} system/phase combinations. "
           f"Nothing changed by this check.",
           f"  🔴 {d['tally'].get(AT_RISK, 0)} at risk   "
           f"🟡 {d['tally'].get(NO_LOGIC, 0)} dates ok but unconnected   "
           f"⚪ {d['tally'].get(NO_DELIVERY, 0)} no delivery line   "
           f"🟢 {d['tally'].get(READY, 0)} ready", ""]

    for r in d["systems"][:max_rows]:
        gap = ("—" if r["buffer_days"] is None
               else f"{r['buffer_days']:+d}d")
        out.append(f"{r['icon']} {r['system']:<14} {r['phase']:<10} "
                   f"arrives {r['arrival'] or '—':<10} "
                   f"needed {r['need'] or '—':<10} {gap:>6}  "
                   f"{r['covered']}/{r['consumers']} connected"
                   + ("  (fed from "
                      + ", ".join(r["cross_phase_from"]) + ")"
                      if r["cross_phase_from"] else ""))
        if r["verdict"] == AT_RISK:
            out.append(f"     {r['need_id']} {r['need_name']} starts "
                       f"{-r['buffer_days']} working days before "
                       f"{r['arrival_id']} {r['arrival_name']} arrives")
        elif r["verdict"] == NO_DELIVERY:
            out.append(f"     {r['consumers']} activities need it; no supply "
                       f"line feeds them")
        elif r["verdict"] == NO_CONSUMER and not r["compound_with"]:
            out.append(f"     {r['standalone_deliveries']} dedicated line(s) "
                       f"arrive and no activity installs them")
        elif r["verdict"] == NO_LOGIC and r["uncovered"]:
            out.append(f"     {r['uncovered']} activities are not downstream of "
                       f"the delivery — the dates work today but nothing holds "
                       f"them there")
    if d["count"] > max_rows:
        out.append(f"…and {d['count'] - max_rows} more")

    out.append("\nAsk for one system by name to get the full story — which "
               "rows are unconnected and what feeds them.")
    return "\n".join(out)


def digest(project, max_rows: int = 12) -> str:
    """
    The compact version that rides in the agent's context, so procurement
    awareness informs answers about sequencing generally rather than only when
    someone asks for the map.
    """
    d = analyse(project)
    if not d["count"]:
        return ""
    lines = [f"PROCUREMENT: {d['at_risk']} system(s) at risk, {d['no_logic']} "
             f"with dates that work but no logic holding them, {d['ready']} ready."]
    hot = [r for r in d["systems"] if r["verdict"] in (AT_RISK, NO_DELIVERY)]
    for r in hot[:max_rows]:
        if r["verdict"] == AT_RISK:
            lines.append(f"  {r['system']} {r['phase']}: {r['need_id']} "
                         f"{r['need_name']} ({r['need']}) is dated "
                         f"{-r['buffer_days']}d before {r['arrival_name']} "
                         f"arrives {r['arrival']}")
        else:
            lines.append(f"  {r['system']} {r['phase']}: {r['consumers']} "
                         f"activities need it, no delivery line found")
    if len(hot) > max_rows:
        lines.append(f"  …and {len(hot) - max_rows} more")
    return "\n".join(lines)
