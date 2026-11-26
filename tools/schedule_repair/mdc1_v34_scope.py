"""MDC-1 v34: add the JER electrical scope the 11/03 Exhibit S carries and we
do not.

Four stages, all of them electrical work we were missing while carrying only
the work-by-others either side of them:

  Pull from EDP to CRAHs / Term. from EDP to CRAHs    ER and MV rooms, 1d each
  Pull From MP Panel to CRAC / Term From MP Panel     HV and HVR rooms, 5d each
  Wall Close-Ins After Skid Set                       MV rooms, 5d
  Energize MV LineUp - RMU to Pumps                   MV rooms, 5d
  Start ups of Equipment                              HV and HVR rooms, 1d

The cooling unit power is the substantive one. Our rooms set the unit and make
the mechanical connection, but nothing pulled or terminated power to it.

Each pair is INSERTED rather than appended: the new chain takes the anchor's
existing successors, so whatever followed the anchor now follows the new work
and no downstream logic is lost. Durations and the sequence position come from
Exhibit S, where Set CRAHs feeds the pull, the pull feeds the terminate, and
the mechanical connection runs alongside.
"""
import collections, copy, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v33.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v34.xml"
UDF = "Number of Electricians"
HPM = 10.0                                   # MDC1: hours = man-days x 10

# room group -> (anchor regex, [(name, days, men), ...], insert|after)
JOBS = {
    "CRAH_POWER": {
        "groups": ("Electrical Rooms", "MV Rooms"),
        "anchor": r'^Set CRAH',
        "steps": [("Pull from EDP to CRAHs", 1, 3),
                  ("Term. from EDP to CRAHs", 1, 3)]},
    "CRAC_POWER": {
        "groups": ("HV Rooms",),
        "anchor": r'^Set Crahs|^Set CRAC|^Set / Install CRAC',
        "steps": [("Pull From MP Panel to CRAC", 5, 3),
                  ("Term From MP Panel to CRAC", 5, 3)]},
    "SKID_CLOSEIN": {
        "groups": ("MV Rooms",),
        "anchor": r'^Set Electrical Skids',
        "steps": [("Wall Close-Ins After Skid Set", 5, 2)]},
    "MV_ENERGIZE": {
        "groups": ("MV Rooms",),
        "anchor": r'^Testing and Inspections',
        "parallel": True,          # Exhibit S runs it alongside, same dates
        "steps": [("Energize MV LineUp - RMU to Pumps", 5, 3)]},
    "HV_STARTUP": {
        "groups": ("HV Rooms",),
        "anchor": r'^Testing & Inspections',
        "steps": [("Start ups of Equipment", 1, 3)]},
}


def d10(v):
    return str(v)[:10] if v else ""


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    a0, r0 = len(p.activities), len(p.relations)
    h0 = sum(float(a.planned_labor_units or 0) for a in p.activities)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v33 in: {a0} activities, {r0} relations, {h0:,.0f} h, finish {f0}")

    W = {n.uid: n for n in p.wbs_nodes}

    def wpath(u):
        o = []
        while u in W:
            o.append(str(W[u].name))
            u = W[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    def roomfolder(u):
        """the deepest folder that names a room, e.g. 'MV 101'"""
        cur = u
        while cur in W:
            n = str(W[cur].name)
            if re.match(r'^(WBO\s+)?(ER|ERR|MV|HV|HVR)\s*\d{3}$', n):
                return cur, n
            cur = W[cur].parent_uid
        return None, None

    # a room's "WBO MV 101" folder is the same room as "MV 101"
    byroom = collections.defaultdict(list)
    homefolder = {}
    for a in p.activities:
        fu, fn = roomfolder(a.wbs_uid)
        if not fu:
            continue
        key = re.sub(r'^WBO\s+', '', fn)
        byroom[key].append(a)
        if not fn.startswith("WBO "):
            homefolder[key] = (fu, a.wbs_uid)      # where new work belongs

    protoA = copy.deepcopy(next(a for a in p.activities
                                if a.activity_type == "Task Dependent"))
    protoR = copy.deepcopy(p.relations[0])
    nextA = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
    used = {str(a.activity_id) for a in p.activities}
    cal = collections.Counter(a.calendar_uid for a in p.activities).most_common(1)[0][0]
    succ = collections.defaultdict(list)
    for r in p.relations:
        succ[r.predecessor_uid].append(r)

    def mkact(aid, name, wuid, days, men):
        nonlocal nextA
        while aid in used:
            aid += "x"
        used.add(aid)
        a = copy.deepcopy(protoA)
        a.uid = str(nextA); nextA += 1
        a.activity_id = aid; a.name = name; a.wbs_uid = wuid
        a.calendar_uid = cal; a.activity_type = "Task Dependent"
        a.planned_duration = days * 8.0
        a.remaining_duration = days * 8.0
        a.actual_duration = 0.0
        a.planned_labor_units = round(men * days * HPM, 1)
        a.remaining_labor_units = a.planned_labor_units
        a.actual_labor_units = 0.0
        a.udfs = {UDF: str(int(men * days))}
        a.status = "Not Started"; a.percent_complete = 0.0
        a.actual_start = a.actual_finish = None
        a.planned_start = a.planned_finish = None
        a.early_start = a.early_finish = None
        a.late_start = a.late_finish = None
        a.total_float = a.free_float = None
        a.constraint_type = None; a.constraint_date = None
        p.activities.append(a)
        return a

    def tie(pre, suc, kind="Finish to Start", lag=0):
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid; r.successor_uid = suc.uid
        r.type = kind; r.lag = lag * 8.0
        p.relations.append(r)
        return r

    report = collections.Counter()
    noanchor = collections.defaultdict(list)
    for job, spec in JOBS.items():
        anchor_re = re.compile(spec["anchor"], re.I)
        par = spec.get("parallel", False)
        for fn, acts in sorted(byroom.items()):
            if fn not in homefolder:
                continue
            fu, home = homefolder[fn]
            grp = wpath(fu)
            if not any(g in grp for g in spec["groups"]):
                continue
            anchors = [a for a in acts if anchor_re.search(str(a.name))]
            if not anchors:
                noanchor[job].append(fn)
                continue
            anchor = min(anchors, key=lambda a: d10(a.early_start))
            moving = list(succ.get(anchor.uid, []))
            prev = anchor
            chain = []
            base = re.match(r'^(.*?)(\d+)$', str(anchor.activity_id))
            stem = base.group(1) if base else str(anchor.activity_id) + "."
            n0 = int(base.group(2)) if base else 9000
            for i, (nm, days, men) in enumerate(spec["steps"]):
                a = mkact(f"{stem}{n0+900+i}", f"{nm} ({fn})", home, days, men)
                if i == 0 and par:
                    tie(anchor, a, "Start to Start", 0)   # alongside, not after
                else:
                    tie(prev, a)
                prev = a
                chain.append(a)
            # Only successors inside this same room may move onto the new
            # chain. A tie that leaves the room is a crew moving to the next
            # room, and a close-in or a pull must not hold that crew up: v34
            # first time round put Wall Close-Ins After Skid Set (MV 105) in
            # front of Electrical Pulls (HV 101) and pushed the programme.
            uid2room = {}
            for b in p.activities:
                fu2, fn2 = roomfolder(b.wbs_uid)
                if fn2:
                    uid2room[b.uid] = re.sub(r'^WBO\s+', '', fn2)
            same = [r for r in moving if uid2room.get(r.successor_uid) == fn]
            if par:
                for r in same:
                    tgt = next((x for x in p.activities
                                if x.uid == r.successor_uid), None)
                    if tgt is not None:
                        tie(prev, tgt)
            else:
                for r in same:
                    r.predecessor_uid = prev.uid     # insert, do not append
            report[job] += len(chain)
    print()
    for job, n in sorted(report.items()):
        print(f"   {job:<16}{n:>4} activities added")
    for job, rooms in sorted(noanchor.items()):
        if rooms:
            print(f"   {job:<16}no anchor in: {', '.join(sorted(set(rooms)))}")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    h1 = sum(float(a.planned_labor_units or 0) for a in p.activities)
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv34 out: {len(p.activities)} activities (+{len(p.activities)-a0}), "
          f"{len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   hours {h0:,.0f} -> {h1:,.0f} (+{h1-h0:,.0f})")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, a, b in sorted(moved)[:10]:
        print(f"      {k:<24}{a} -> {b}")
    # orphan and loop checks
    s = {r.predecessor_uid for r in p.relations}
    t = {r.successor_uid for r in p.relations}
    print(f"   no successor {sum(1 for a in p.activities if a.uid not in s)}, "
          f"no predecessor {sum(1 for a in p.activities if a.uid not in t)}")
    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)
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
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
