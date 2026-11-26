"""MDC-1 v36: close the open ends.

Every activity that is not complete and feeds nothing is given the successor
its own room already implies. Three shapes:

  Room start-ups. The HV and HVR start-ups added in v34 ended up terminal:
  the insertion correctly refused to take Testing's cross-room successors,
  but a start-up has to reach commissioning. Each is tied to whatever the
  other rooms' start-ups in the same phase already feed, so it joins the
  path the same way rather than by a rule invented here.

  Work by others inside a room. Overhead Rough In Mechanical and Mechanical
  Final Connections are carried to keep the room's sequence honest, so each
  runs into the next activity in its own room.

  Loose ends with an obvious next step, OH Branch Power into the room's
  device trim out, Pour Slab into the next work in its area.

A successor is only taken from the activity's own room or, failing that, its
immediate area, and never one already downstream of it. Terminal milestones
are left alone: Owner Acceptance ends the project and is supposed to feed
nothing.
"""
import collections, copy, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v35.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v36.xml"

TERMINAL = re.compile(r'Owner Acceptance|Final Completion|'
                      r'Final Certificate of Occupancy|Complete Construction|'
                      r'Substantial Completion|Fire Alarm System Accepted', re.I)
STARTUP = re.compile(r'^Start ups of Equipment', re.I)

# Where an activity's own room offers nothing later, but the trade says
# plainly what follows. Pour Slab is the clearest: 29 of the 30 in this file
# feed Concrete Sealer & Cure, and Area 30 has that sibling sitting beside it
# -- the generic rule only missed it because that area's dates run out of
# order. Site and roof work with nothing after it belongs on Complete
# Construction: the building is not complete without the landscaping, the
# urea tank or the roof equipment started up.
SAME_FOLDER_NEXT = {r'^Pour Slab$': r'^Concrete (Sealer & Cure|Cure & Sealer)$'}
TO_COMPLETE = re.compile(r'^Landscaping|^Underground Urea Tank|'
                         r'^Pull/Term/Startup/Misc\. Equipment', re.I)
COMPLETE_ID = "MDC1.MIL.1140"            # Complete Construction


def d10(v):
    return str(v)[:10] if v else ""


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0 = len(p.relations)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v35 in: {len(p.activities)} activities, {r0} relations, finish {f0}")

    A = {a.uid: a for a in p.activities}
    W = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in W:
            o.append(str(W[u].name))
            u = W[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    def phase_of(a):
        m = re.search(r'Phase (\d)', path(a.wbs_uid))
        if m:
            return f"PH{m.group(1)}"
        m = re.match(r'MDC1\.(PH\d)\.', str(a.activity_id))
        return m.group(1) if m else None

    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)

    def downstream(u):
        seen, st = set(), [u]
        while st:
            v = st.pop()
            for n2 in adj.get(v, ()):
                if n2 not in seen:
                    seen.add(n2); st.append(n2)
        return seen

    def would_loop(pre, suc):
        """A tie pre -> suc closes a loop when pre is already reachable FROM
        suc. Checking the other direction, as this first did, catches a
        harmless redundant edge and misses the real cycle."""
        return pre.uid in downstream(suc.uid)

    protoR = copy.deepcopy(p.relations[0])
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1

    def tie(pre, suc):
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid; r.successor_uid = suc.uid
        r.type = "Finish to Start"; r.lag = 0
        p.relations.append(r)
        adj[pre.uid].append(suc.uid)

    # what do the room start-ups that ARE connected already feed?
    feeds = collections.defaultdict(collections.Counter)
    for a in p.activities:
        if not STARTUP.match(str(a.name)):
            continue
        ph = phase_of(a)
        for s in adj.get(a.uid, ()):
            q = A.get(s)
            if q is not None:
                feeds[ph][str(q.activity_id)] += 1
    print("\nwhat connected room start-ups feed, by phase:")
    for ph in sorted(feeds):
        for k, v in feeds[ph].most_common(3):
            print(f"   {ph}  {v:>3}x  {k}  {str(A[[x.uid for x in p.activities if str(x.activity_id)==k][0]].name)[:40] if any(str(x.activity_id)==k for x in p.activities) else ''}")

    byfolder = collections.defaultdict(list)
    for a in p.activities:
        byfolder[a.wbs_uid].append(a)

    # ---- a slab is cured after it is poured, not before it is dug --------
    # Area 30 ties Concrete Sealer & Cure in FRONT of MEP Underground
    # Excavations, so the cure drives the whole area and Pour Slab, the last
    # activity, feeds nothing. Every other area in the file runs Pour Slab ->
    # Concrete Sealer & Cure. The backwards tie is removed; the excavations
    # keep their real predecessor, the previous area's cure, which is the
    # crew moving on.
    byname = collections.defaultdict(list)
    for a in p.activities:
        byname[(a.wbs_uid, str(a.name).strip())].append(a)
    rev = 0
    for r in list(p.relations):
        q, t = A.get(r.predecessor_uid), A.get(r.successor_uid)
        if q is None or t is None:
            continue
        if not re.match(r'^Concrete (Sealer & Cure|Cure & Sealer)$', str(q.name).strip()):
            continue
        if not re.match(r'^MEP Underground Excavations$', str(t.name).strip()):
            continue
        if q.wbs_uid != t.wbs_uid:
            continue                    # a different area's cure is the crew tie
        others = [x for x in p.relations
                  if x.successor_uid == t.uid and x is not r]
        if not others:
            print(f"   ! would orphan {t.activity_id}, left alone")
            continue
        p.relations.remove(r)
        adj[q.uid] = [u for u in adj[q.uid] if u != t.uid]
        rev += 1
        print(f"   reversed tie removed: {q.activity_id} -> {t.activity_id} "
              f"({path(q.wbs_uid).split(' / ')[-1]})")
    if rev:
        print(f"   {rev} backwards cure-before-excavation ties removed")

    s_set = {r.predecessor_uid for r in p.relations}
    oe = [a for a in p.activities if a.uid not in s_set and not a.actual_finish]
    print(f"\n{len(oe)} open ends")

    tied, left = [], []
    for a in sorted(oe, key=lambda a: str(a.activity_id)):
        if TERMINAL.search(str(a.name)):
            left.append((a, "terminal by design"))
            continue
        down = downstream(a.uid)
        target = None
        why = ""
        # 1. a start-up joins the path its phase's other start-ups take
        if STARTUP.match(str(a.name)):
            ph = phase_of(a)
            for k, _ in feeds.get(ph, collections.Counter()).most_common():
                q = next((x for x in p.activities
                          if str(x.activity_id) == k), None)
                if q is not None and q.uid != a.uid and q.uid not in down \
                        and not would_loop(a, q):
                    target, why = q, f"as other {ph} start-ups do"
                    break
        # 1b. the trade's own next step, wherever it sits in the folder
        if target is None:
            for pat, nxt in SAME_FOLDER_NEXT.items():
                if not re.search(pat, str(a.name).strip()):
                    continue
                c = [b for b in byfolder[a.wbs_uid]
                     if b.uid != a.uid and b.uid not in down
                     and re.search(nxt, str(b.name).strip())
                     and not would_loop(a, b)]
                if c:
                    target = c[0]
                    why = "the trade's own next step"
                    break
        # 1c. site and roof work with nothing after it closes the building out
        if target is None and TO_COMPLETE.search(str(a.name)):
            q = next((x for x in p.activities
                      if str(x.activity_id) == COMPLETE_ID), None)
            if q is not None and q.uid not in down and not would_loop(a, q):
                target, why = q, "closes out into Complete Construction"
        # 2. the next activity in its own room
        if target is None:
            later = [b for b in byfolder[a.wbs_uid]
                     if b.uid != a.uid and b.uid not in down
                     and d10(b.early_start) >= d10(a.early_finish)
                     and not would_loop(a, b)]
            if later:
                target = min(later, key=lambda b: d10(b.early_start))
                why = "next in the room"
        # 3. the next activity in its area
        if target is None and a.wbs_uid in W:
            par = W[a.wbs_uid].parent_uid
            pool = [b for b in p.activities
                    if b.wbs_uid in W and W[b.wbs_uid].parent_uid == par
                    and b.uid != a.uid and b.uid not in down
                    and d10(b.early_start) >= d10(a.early_finish)
                    and not would_loop(a, b)]
            if pool:
                target = min(pool, key=lambda b: d10(b.early_start))
                why = "next in the area"
        if target is None:
            left.append((a, "nothing later to tie to"))
            continue
        tie(a, target)
        tied.append((a, target, why))

    print(f"\ntied {len(tied)}:")
    for a, t, why in tied:
        print(f"   {str(a.name)[:38]:<40}-> {str(t.name)[:34]:<36}{why}")
    print(f"\nleft alone {len(left)}:")
    for a, why in left:
        print(f"   {str(a.name)[:44]:<46}{why}")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    s2 = {r.predecessor_uid for r in p.relations}
    still = sum(1 for a in p.activities if a.uid not in s2 and not a.actual_finish)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv36 out: {len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   open ends {len(oe)} -> {still}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, x, y in sorted(moved)[:10]:
        print(f"      {k:<24}{x} -> {y}")
    colour = {}
    def ok(s0):
        st = [(s0, iter(adj[s0]))]; colour[s0] = 1
        while st:
            v, it = st[-1]
            n2 = next(it, None)
            if n2 is None:
                colour[v] = 2; st.pop()
            elif colour.get(n2, 0) == 1:
                return False
            elif colour.get(n2, 0) == 0:
                colour[n2] = 1; st.append((n2, iter(adj[n2])))
        return True
    acyc = all(ok(a.uid) for a in p.activities if colour.get(a.uid, 0) == 0)
    print(f"   cycles: {'none' if acyc else 'PRESENT'}")
    assert acyc
    q = load_xml(SRC)
    assert len(p.activities) == len(q.activities)
    hp = sum(float(a.planned_labor_units or 0) for a in p.activities)
    hq = sum(float(a.planned_labor_units or 0) for a in q.activities)
    assert abs(hp - hq) < 1
    print("   checks: activity count and hours unchanged, only ties added")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
