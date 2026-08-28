# -*- coding: utf-8 -*-
"""
normalize.py — read the whole schedule, work out what is wrong, and say what
to fix in what order.

WHY THIS EXISTS
  Every piece needed to repair a schedule already exists here: the folder
  flow analysis knows which branches are islands, the tie ranker knows which
  predecessor a row should have and scores it against the dates and against
  what the user has taught, the scope graph knows the MEP lifecycle, and the
  schedule preview knows what a reflow would do. What was missing was
  something that reads all of it AT ONCE and produces a plan, the way an
  engineer looks at a job rather than at one activity.

WHY IT IS A PLAN AND NOT A BUTTON
  The obvious ask is one button that wires everything green. On a real
  half-built schedule that is the wrong shape, and it is worth being explicit
  about why rather than quietly doing something safer:

    · ORDER MATTERS AND IS NOT OPTIONAL. Wiring logic into a folder that
      contains duplicated activities ties one twin and leaves the other
      floating — it bakes the duplication into the network, and unpicking it
      afterwards is worse than the original mess. Duplicates come first, and
      this refuses to auto-wire a folder that has them.
    · EACH TIE CHANGES THE NEXT. Six hundred ties emitted in one pass are
      six hundred decisions where all but the first are made against a
      network that is already partly the tool's own guesswork.
    · SIX HUNDRED TIES CANNOT BE REVIEWED. A batch nobody can check is a
      batch nobody can trust, and one bad tie repeated across a branch is
      worse than the open end it replaced.

  So the work is staged: fix what is unambiguous, propose what is not, and
  MEASURE after each pass. A pass that does not improve the numbers says so.

WHAT "BETTER" MEANS, MEASURED
  Not opinion — the same numbers before and after: folders connected, folders
  isolated, activities floating, folder ties running backward, and what a
  reflow would move. Every run reports the delta, so a change that made the
  schedule worse is visible immediately rather than discovered later.
"""

from typing import Any, Dict, List, Optional

# Severity order — this is the order the work should actually be done in, and
# it is not arbitrary. Each step is cheaper and safer once the one above it
# is done.
BLOCKER = "blocker"      # fix before wiring anything, or the wiring is wrong
STRUCTURAL = "structural"  # whole branches not attached to the job
GAPS = "gaps"            # individual rows with no logic
QUALITY = "quality"      # backward flow, constraints masking missing logic


def measure(project) -> Dict[str, Any]:
    """The numbers that say whether the schedule got better. Cheap enough to
    run before and after every pass."""
    from engine import wbs_flow
    flow = wbs_flow.analyse(project)["totals"]
    linked = set()
    for r in project.relations:
        linked.add(r.predecessor_uid)
        linked.add(r.successor_uid)
    return {
        "connected": flow["connected"],
        "one_way": flow["one_way"],
        "dangling": flow["dangling"],
        "isolated": flow["isolated"],
        "floating_activities": flow["floating_activities"],
        "backward_edges": flow["backward_edges"],
        "relations": len(project.relations),
        "unlinked": sum(1 for a in project.activities if a.uid not in linked),
    }


def _duplicate_folders(project) -> Dict[str, int]:
    """folder uid -> how many duplicated rows it holds."""
    groups: Dict[str, List[Any]] = {}
    for a in project.activities:
        key = (f"{(a.name or '').strip().lower()}|{a.wbs_uid}|"
               f"{str(a.planned_start or '')[:10]}")
        groups.setdefault(key, []).append(a)
    out: Dict[str, int] = {}
    for v in groups.values():
        if len(v) > 1:
            out[v[0].wbs_uid] = out.get(v[0].wbs_uid, 0) + (len(v) - 1)
    return out


def diagnose(project, brain=None) -> Dict[str, Any]:
    """
    Everything wrong with this schedule, in the order it should be fixed.

    Reads the folder flow, the duplicate scan, the constraint load and what a
    reflow would do, and turns them into findings that each say what to do
    and what fixing it buys. Nothing is modified.
    """
    from engine import wbs_flow
    flow = wbs_flow.analyse(project)
    dupes = _duplicate_folders(project)
    by_uid = {w.uid: w for w in project.wbs_nodes}

    findings: List[Dict[str, Any]] = []

    if dupes:
        n_rows = sum(dupes.values())
        names = [by_uid[u].name for u in list(dupes)[:6] if u in by_uid]
        findings.append({
            "severity": BLOCKER, "kind": "duplicates",
            "count": n_rows, "folders": len(dupes),
            "headline": f"{n_rows} duplicated activities across {len(dupes)} folder(s)",
            "why": ("Wiring logic into a folder that holds duplicated rows ties "
                    "one twin and leaves the other floating, baking the "
                    "duplication into the network. Clear these first."),
            "do": "Run find_duplicates, decide which of each pair to keep, delete the other.",
            "examples": names,
        })

    isolated = [f for f in flow["folders"].values()
                if f["verdict"] == wbs_flow.ISOLATED]
    if isolated:
        isolated.sort(key=lambda f: -f["activities"])
        findings.append({
            "severity": STRUCTURAL, "kind": "isolated_folders",
            "count": len(isolated),
            "headline": f"{len(isolated)} folder(s) touch nothing outside themselves",
            "why": ("A branch wired only to itself is not part of the job — a "
                    "slip in it never reaches the milestone it should drive, "
                    "and the critical path cannot run through it."),
            "do": "Tie the first real activity in each to what finishes before it, "
                  "and its last to what it feeds.",
            "examples": [f"{f['name']} ({f['activities']} act)" for f in isolated[:8]],
        })

    dangling = [f for f in flow["folders"].values()
                if f["verdict"] == wbs_flow.DANGLING]
    if dangling:
        dangling.sort(key=lambda f: -f["floating_count"])
        findings.append({
            "severity": GAPS, "kind": "floating_activities",
            "count": sum(f["floating_count"] for f in dangling),
            "headline": (f"{sum(f['floating_count'] for f in dangling)} activities "
                         f"have no logic at all, across {len(dangling)} folder(s)"),
            "why": ("An explicit Schedule drives work with no predecessor to the "
                    "data date. Until these are wired, a reflow will move them "
                    "on top of dates that were entered deliberately."),
            "do": "Wire each folder's open ends — the ranker proposes a "
                  "predecessor per row, scored against the dates and your rules.",
            "examples": [f"{f['name']} ({f['floating_count']} floating)"
                         for f in dangling[:8]],
        })

    one_way = [f for f in flow["folders"].values()
               if f["verdict"] == wbs_flow.ONE_WAY]
    if len(one_way) > 2:          # the job's first and last are legitimately one-way
        findings.append({
            "severity": STRUCTURAL, "kind": "dead_ends",
            "count": len(one_way),
            "headline": f"{len(one_way)} folder(s) are fed but drive nothing, or drive but are not fed",
            "why": ("A dead end absorbs delay silently: work slips and nothing "
                    "downstream notices. The job's first and last folders are "
                    "legitimately one-way; the rest are gaps."),
            "do": "Give each an outbound tie to whatever it actually enables.",
            "examples": [f["name"] for f in one_way[:8]],
        })

    if flow["backward"]:
        findings.append({
            "severity": QUALITY, "kind": "backward_flow",
            "count": len(flow["backward"]),
            "headline": f"{len(flow['backward'])} folder link(s) run backward in time",
            "why": ("A predecessor folder dated after the folder it feeds is "
                    "almost always a mistake — work feeding something that has "
                    "already happened."),
            "do": "Check each pair; usually the tie is reversed or the dates are wrong.",
            "examples": [f"{flow['folders'][b['from']]['name']} → "
                         f"{flow['folders'][b['to']]['name']}"
                         for b in flow["backward"][:6]],
        })

    pinned = [a for a in project.activities
              if (a.constraint_type or "").strip()
              and "or before" not in (a.constraint_type or "").lower()]
    if len(pinned) > max(10, len(project.activities) // 20):
        findings.append({
            "severity": QUALITY, "kind": "constraint_load",
            "count": len(pinned),
            "headline": f"{len(pinned)} activities are pinned by a constraint",
            "why": ("A pin holds a date the logic cannot explain. Where the "
                    "logic would produce that date anyway, the pin is doing "
                    "nothing but hiding whether the network is right."),
            "do": "Wire the logic first, then clear the pins that the logic now reproduces.",
            "examples": [],
        })

    order = {BLOCKER: 0, STRUCTURAL: 1, GAPS: 2, QUALITY: 3}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), -f["count"]))
    return {"findings": findings, "measure": measure(project),
            "duplicate_folders": dupes}


def plan(project, brain=None) -> str:
    """The diagnosis as an ordered plan, for the chat and for the agent."""
    d = diagnose(project, brain)
    m = d["measure"]
    if not d["findings"]:
        return ("This schedule is in good shape: every folder holding work is "
                "connected, nothing is floating, and no folder tie runs "
                "backward. Nothing to normalize.")

    out = ["SCHEDULE NORMALIZATION PLAN — nothing has been changed.",
           f"  Now: {m['connected']} folders connected, {m['isolated']} isolated, "
           f"{m['dangling']} with loose work, {m['floating_activities']} activities "
           f"with no logic, {m['backward_edges']} backward tie(s).",
           ""]

    for i, f in enumerate(d["findings"], 1):
        out.append(f"{i}. [{f['severity'].upper()}] {f['headline']}")
        out.append(f"   Why it matters: {f['why']}")
        out.append(f"   Fix: {f['do']}")
        if f["examples"]:
            out.append(f"   e.g. {', '.join(str(x) for x in f['examples'][:6])}")
        out.append("")

    if d["duplicate_folders"]:
        out.append("ORDER MATTERS: the duplicates come first. Wiring a folder "
                   "that holds duplicated rows ties one twin and leaves the "
                   "other floating, which is harder to unpick than the mess it "
                   "replaces. normalize_logic will refuse those folders until "
                   "they are cleaned.")
    else:
        out.append("Run normalize_logic to apply the wiring passes. It only "
                   "closes ends that are already open, never deletes anything, "
                   "and reports the before/after numbers so a pass that did not "
                   "help is visible.")
    return "\n".join(out)


_MAX_BOUNDARY_GAP = 120        # days; beyond this two pieces of work are unrelated


def _descendants_of(project, root_uid):
    """A folder uid and every folder under it."""
    kids: Dict[str, List[str]] = {}
    for w in project.wbs_nodes:
        kids.setdefault(w.parent_uid, []).append(w.uid)
    out, stack = {root_uid}, [root_uid]
    guard = 0
    while stack and guard < 20000:
        guard += 1
        for k in kids.get(stack.pop(), []):
            if k not in out:
                out.add(k)
                stack.append(k)
    return out


def _boundary_ties(project, folder, acts_in, ctx, min_confidence) -> List[Dict]:
    """
    The tie that brings work INTO a folder, and the one that carries it OUT.

    wire_folder only ever looks inside a branch, so on a leaf folder it chains
    the rows to each other and the folder stays isolated — the floating count
    drops while the folder is still attached to nothing. That is why a wiring
    pass could remove hundreds of open ends without a single folder reaching
    connected. Closing a folder's boundary is a different question from
    ordering its contents, and it needs the whole schedule as candidates.

    Ranked by the same scorer as everything else, so a stated rule or the
    scope document still decides where there is a choice.
    """
    import datetime as _d

    from engine.logic_advisor import _has_link, implied_lag, score_tie

    def _pd(v):
        try:
            return _d.date.fromisoformat(str(v or "")[:10])
        except ValueError:
            return None

    inside = {a.uid for a in acts_in}
    outside = [a for a in project.activities
               if a.uid not in inside
               and a.activity_type not in ("Start Milestone", "Finish Milestone")]

    dated = [(a, _pd(a.planned_start)) for a in acts_in]
    dated = [(a, d) for a, d in dated if d]
    if not dated:
        return []
    first = min(dated, key=lambda t: t[1])[0]
    last = max(dated, key=lambda t: (_pd(t[0].planned_finish) or t[1]))[0]

    out: List[Dict] = []

    # IN — the best thing that finishes before this folder's first work starts
    if not folder["links_in"]:
        s_date = _pd(first.planned_start)
        best = None
        for pred in outside:
            f = _pd(pred.planned_finish)
            if not f or not s_date or f > s_date:
                continue
            if (s_date - f).days > _MAX_BOUNDARY_GAP:
                continue
            if _has_link(project, pred.uid, first.uid):
                continue
            lag = implied_lag(project, pred, first)
            if lag is None:
                continue
            c, why = score_tie(ctx, pred, first, lag)
            if best is None or c > best[0]:
                best = (c, pred, why)
        if best and best[0] >= min_confidence:
            out.append({"action": "add_relation",
                        "predecessor_id": best[1].activity_id,
                        "successor_id": first.activity_id, "type": "fs",
                        "lag_days": 0})

    # OUT — the best thing that starts after this folder's last work finishes
    if not folder["links_out"]:
        f_date = _pd(last.planned_finish) or _pd(last.planned_start)
        best = None
        for succ in outside:
            s = _pd(succ.planned_start)
            if not s or not f_date or s < f_date:
                continue
            if (s - f_date).days > _MAX_BOUNDARY_GAP:
                continue
            if _has_link(project, last.uid, succ.uid):
                continue
            lag = implied_lag(project, last, succ)
            if lag is None:
                continue
            c, why = score_tie(ctx, last, succ, lag)
            if best is None or c > best[0]:
                best = (c, succ, why)
        if best and best[0] >= min_confidence:
            out.append({"action": "add_relation",
                        "predecessor_id": last.activity_id,
                        "successor_id": best[1].activity_id, "type": "fs",
                        "lag_days": 0})
    return out


def normalize_logic(project, brain=None, min_confidence: float = 0.55,
                    limit: int = 150, folders: Optional[List[str]] = None,
                    preview: bool = False) -> Dict[str, Any]:
    """
    Close the open ends the ranker is confident about, folder by folder.

    The rails, all deliberate:
      · only ends that are ALREADY OPEN — logic somebody set is never touched
      · nothing is ever deleted
      · a folder holding duplicated rows is SKIPPED, not wired
      · ties that contradict the dates are dropped, not applied
      · a confidence floor higher than the single-activity view, because this
        applies in bulk and a wrong tie here is wrong many times over
      · a per-run cap, so the result stays reviewable
      · the before/after numbers travel with the result

    Returns the proposed commands and the measurement. Applying them is the
    caller's job — this never writes.
    """
    from engine import wbs_flow
    from engine.logic_advisor import _Ctx, to_commands, wire_folder

    directives = getattr(brain, "directives", None) if brain else None
    feedback = getattr(brain, "feedback", None) if brain else None
    scope = getattr(brain, "scope", None) if brain else None
    ctx = _Ctx(project, directives, feedback, scope)

    flow = wbs_flow.analyse(project)
    dupes = _duplicate_folders(project)
    by_uid = {w.uid: w for w in project.wbs_nodes}

    # Worst first: a folder attached to nothing matters more than one with a
    # couple of loose rows.
    rank = {wbs_flow.ISOLATED: 0, wbs_flow.DANGLING: 1, wbs_flow.ONE_WAY: 2}
    targets = [f for f in flow["folders"].values() if f["verdict"] in rank]
    if folders:
        want = set(folders)
        targets = [f for f in targets
                   if f["uid"] in want or f["name"] in want]
    targets.sort(key=lambda f: (rank[f["verdict"]], -f["activities"]))

    before = measure(project)
    cmds: List[Dict[str, Any]] = []
    touched, skipped_dupes, unresolved = [], [], 0
    # One tie is two folders' business: it is folder A's way out and folder B's
    # way in, so both passes find it. Proposing it twice would apply it once
    # and report the second as a failure, making a clean batch look broken.
    seen_ties = set()

    for f in targets:
        if len(cmds) >= limit:
            break
        if f["uid"] in dupes:
            skipped_dupes.append(f["name"])
            continue
        res = wire_folder(project, f["uid"], min_confidence=min_confidence,
                          limit=max(0, limit - len(cmds)),
                          directives=directives, feedback=feedback,
                          scope_graph=scope)
        # CONFLICT proposals are dropped by to_commands unless asked for —
        # a tie that contradicts the dates is exactly what should not be
        # applied in bulk.
        new = to_commands(res["proposals"], include_conflicts=False)
        unresolved += res.get("unresolved", 0)

        # Ordering the rows inside a folder never attaches the folder to the
        # job. The boundary ties are what move it off isolated, so they are
        # found separately and against the whole schedule.
        acts_in = [a for a in project.activities
                   if a.wbs_uid in _descendants_of(project, f["uid"])]
        bt = _boundary_ties(project, f, acts_in, ctx, min_confidence)

        fresh = []
        for c in bt + new:
            if c["action"] == "add_relation":
                key = (c["predecessor_id"], c["successor_id"])
                if key in seen_ties:
                    continue
                seen_ties.add(key)
            fresh.append(c)

        if fresh:
            cmds.extend(fresh)
            touched.append({"folder": f["name"], "was": f["verdict"],
                            "ties": len([c for c in fresh
                                         if c["action"] == "add_relation"]),
                            "boundary": len([c for c in bt if c in fresh])})

    return {
        "commands": cmds[:limit],
        "folders_touched": touched,
        "skipped_for_duplicates": skipped_dupes,
        "unresolved": unresolved,
        "before": before,
        "min_confidence": min_confidence,
        "capped": len(cmds) >= limit,
    }


# How good a folder's state is, so a change can be called progress or not.
# Moving from isolated to dangling looks like "dangling went UP" in the raw
# counts, but it is a folder going from touching nothing to being partly
# wired — real progress. Ranking the verdicts is what tells the difference.
_RANK = {"isolated": 0, "dangling": 1, "one_way": 2, "connected": 3, "empty": 3}


def verify(project, commands, brain=None) -> Dict[str, Any]:
    """
    Apply the proposed commands to a COPY and report what they actually did
    to the numbers. The live project is untouched.

    This is the difference between "227 ties were added" and "227 ties took
    270 activities off the floor and moved 5 folders off isolated" — and it
    is the only way to notice a pass that made things worse.
    """
    from engine import wbs_flow
    from engine.edit_engine import apply_commands
    from engine.schedule_preview import _snapshot

    trial = _snapshot(project)
    before = measure(project)
    before_v = {u: f["verdict"]
                for u, f in wbs_flow.analyse(project)["folders"].items()}

    results = apply_commands(trial, commands)
    applied = sum(1 for ok, _ in results if ok)
    failed = len(results) - applied

    after = measure(trial)
    after_v = {u: f["verdict"]
               for u, f in wbs_flow.analyse(trial)["folders"].items()}

    improved = worsened = 0
    for u, was in before_v.items():
        now = after_v.get(u, was)
        if _RANK.get(now, 0) > _RANK.get(was, 0):
            improved += 1
        elif _RANK.get(now, 0) < _RANK.get(was, 0):
            worsened += 1

    return {
        "applied": applied, "failed": failed,
        "before": before, "after": after,
        "folders_improved": improved, "folders_worsened": worsened,
        "floating_removed": before["floating_activities"] - after["floating_activities"],
        "relations_added": after["relations"] - before["relations"],
        "backward_added": after["backward_edges"] - before["backward_edges"],
    }


def normalize_report(project, brain=None, min_confidence: float = 0.55,
                     limit: int = 150) -> str:
    """What normalize_logic would do, as prose. Changes nothing."""
    r = normalize_logic(project, brain, min_confidence, limit)
    ties = [c for c in r["commands"] if c["action"] == "add_relation"]
    if not ties:
        bits = ["No ties met the confidence bar "
                f"({r['min_confidence']}), so nothing is proposed."]
        if r["skipped_for_duplicates"]:
            bits.append(f"{len(r['skipped_for_duplicates'])} folder(s) were "
                        f"skipped because they hold duplicated rows.")
        if r["unresolved"]:
            bits.append(f"{r['unresolved']} open row(s) had no candidate good "
                        f"enough — those need a human.")
        return " ".join(bits)

    out = [f"NORMALIZE — {len(ties)} tie(s) proposed across "
           f"{len(r['folders_touched'])} folder(s), at confidence "
           f"≥ {r['min_confidence']}. Nothing has been applied.",
           ""]
    for t in r["folders_touched"][:20]:
        out.append(f"  {t['folder']}  ({t['was']}) → +{t['ties']} tie(s)")
    if len(r["folders_touched"]) > 20:
        out.append(f"  …and {len(r['folders_touched']) - 20} more folders")

    if r["skipped_for_duplicates"]:
        out.append("")
        out.append(f"  SKIPPED — {len(r['skipped_for_duplicates'])} folder(s) hold "
                   f"duplicated rows and were not wired: "
                   f"{', '.join(r['skipped_for_duplicates'][:6])}. "
                   f"Wiring those would tie one twin and leave the other "
                   f"floating. Clean them first.")
    if r["unresolved"]:
        out.append("")
        out.append(f"  {r['unresolved']} open row(s) had no candidate above the "
                   f"bar. Those are left alone rather than guessed at.")
    if r["capped"]:
        out.append("")
        out.append(f"  Capped at {limit} commands so the batch stays reviewable "
                   f"— run again after applying to continue.")

    # What it would actually buy, measured on a copy. A proposal that cannot
    # show an improvement is not worth applying, and saying so is the point.
    v = verify(project, r["commands"], brain)
    out.append("")
    out.append("WHAT THIS WOULD ACTUALLY DO (measured on a copy, nothing applied):")
    out.append(f"  {v['floating_removed']} activities would stop floating "
               f"({v['before']['floating_activities']} → "
               f"{v['after']['floating_activities']})")
    out.append(f"  {v['folders_improved']} folder(s) improve, "
               f"{v['folders_worsened']} get worse")
    out.append(f"  folders fully connected: {v['before']['connected']} → "
               f"{v['after']['connected']}")
    if v["backward_added"] > 0:
        out.append(f"  ⚠ it would ADD {v['backward_added']} backward tie(s) — "
                   f"worth looking at before applying")
    if v["failed"]:
        out.append(f"  {v['failed']} command(s) failed on the trial run")
    if v["folders_improved"] == 0 and v["floating_removed"] == 0:
        out.append("  → This pass would not measurably improve the schedule. "
                   "Do not apply it; the open ends left need a human decision.")
    elif v["after"]["connected"] == v["before"]["connected"]:
        out.append("  → Real progress, but no folder reaches fully connected: "
                   "green needs NOTHING floating plus a tie in and out, and a "
                   "single pass rarely gets there. Expect several rounds.")
    return "\n".join(out)
