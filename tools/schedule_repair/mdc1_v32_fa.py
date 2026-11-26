"""MDC-1 v32: build the fire alarm sequence, the way MDC3 does it.

Holder carries one lumped "Fire Alarm (Gen NNN)" bar per generator room and
nothing at all for the valve rooms. That bar is not a breakdown: it is a
device install, or a room alarm, with no path into commissioning. This bridges
that gap so a room's fire alarm runs device install, then testing, then joins
the commissioning path through the Fire Alarm Devices Complete milestone that
already exists for its phase.

What goes in:

  Fire Alarm
    Level 1    / Valve Rooms   VAL 101-104
    Level 2    / Valve Rooms   VAL 201-204
    Penthouse  / Scaffolding
               / Generator Rooms - North   Gen 301-313
               / Generator Rooms - South   Gen 315-328
               / Valve Rooms   VAL 301-304

  per generator room   FA Device Install          5d, 4 electricians
  per valve room       Install FA Panel          10d
                       FA Device Install          5d
                       FA Testing & Commissioning 5d
  once                 Start Scaffolding         45d from 2026-11-30

Actuals come from the 11/03 Exhibit S. Gen 315 through 323 carry a complete
Fire Alarm bar there, from June to September 2026, so those device installs
are written as complete on Holder's own dates rather than forecast. The
remaining rooms hang off the scaffolding with a one week lag, which is what
the scaffold is for -- heat detection at structure in a penthouse room.

Valve rooms chain room to room as one crew, and the last one on each level
runs into that level's fire alarm completion.

Gen 314 is not built: the room does not exist anywhere in our schedule, which
is a scope gap to raise separately.
"""
import collections, copy, datetime as dt, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v31.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v32.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")
UDF = "Number of Electricians"

SCAFF_START = "2026-11-30"
SCAFF_DAYS = 45
LAG_DAYS = 5                      # one working week after the scaffold
GEN_MEN, GEN_DAYS = 4, 5
VALVE = [("Install FA Panel", 10), ("FA Device Install", 5),
         ("FA Testing & Commissioning", 5)]
VALVE_MEN = 2
HOURS_PER_MANDAY = 10.0           # MDC1's convention: hours = man-days x 10


def d10(v):
    return str(v)[:10] if v else ""


def holder_gen_fa():
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Schedule"]
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I["Activity Name"]] or "")
        m = re.match(r'\s*Fire Alarm\s*\(Gen (\d{3})', nm)
        if not m:
            continue
        rm = m.group(1)
        rec = {"done": str(r[I["Finish Status"]] or "") == "Actual",
               "start": d10(r[I["Start"]]), "finish": d10(r[I["Finish"]]),
               "dur": r[I["Duration (Days)"]]}
        if rm not in out or rec["done"]:
            out[rm] = rec
    return out


def main():
    H = holder_gen_fa()
    done = {k: v for k, v in H.items() if v["done"]}
    print(f"Exhibit S: {len(H)} generator rooms carry a Fire Alarm bar, "
          f"{len(done)} complete ({', '.join(sorted(done))})")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    a0, r0, w0 = len(p.activities), len(p.relations), len(p.wbs_nodes)
    print(f"v31 in: {a0} activities, {r0} relations, {w0} WBS nodes, finish {f0}")

    byid = {str(a.activity_id): a for a in p.activities}
    wbs = {n.uid: n for n in p.wbs_nodes}
    root = next(n for n in p.wbs_nodes if n.parent_uid not in wbs)
    protoW = copy.deepcopy(p.wbs_nodes[3])
    protoA = copy.deepcopy(next(a for a in p.activities
                                if a.activity_type == "Task Dependent"))
    protoR = copy.deepcopy(p.relations[0])
    nextW = max(int(n.uid) for n in p.wbs_nodes if str(n.uid).isdigit()) + 1
    nextA = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
    cal = collections.Counter(a.calendar_uid for a in p.activities).most_common(1)[0][0]

    def node(name, parent):
        nonlocal nextW
        n = copy.deepcopy(protoW)
        n.uid = str(nextW); nextW += 1
        n.name = name
        n.parent_uid = parent
        n.sequence_num = nextW
        p.wbs_nodes.append(n)
        wbs[n.uid] = n
        return n

    used = set(byid)

    def act(aid, name, wuid, days, men, actual=None):
        nonlocal nextA
        while aid in used:
            aid += "x"
        used.add(aid)
        a = copy.deepcopy(protoA)
        a.uid = str(nextA); nextA += 1
        a.activity_id = aid
        a.name = name
        a.wbs_uid = wuid
        a.calendar_uid = cal
        a.activity_type = "Task Dependent"
        a.planned_duration = days * 8.0
        a.planned_labor_units = round(men * days * HOURS_PER_MANDAY, 1)
        a.udfs = {UDF: str(int(men * days))}      # MDC1 stores man-days here
        a.constraint_type = None
        a.constraint_date = None
        a.early_start = a.early_finish = None
        a.late_start = a.late_finish = None
        a.total_float = a.free_float = None
        if actual:
            a.actual_start, a.actual_finish = actual
            a.status = "Completed"
            a.percent_complete = 100.0
            a.actual_duration = a.planned_duration
            a.remaining_duration = 0.0
            a.actual_labor_units = a.planned_labor_units
            a.remaining_labor_units = 0.0
            a.planned_start, a.planned_finish = actual
        else:
            a.actual_start = a.actual_finish = None
            a.status = "Not Started"
            a.percent_complete = 0.0
            a.actual_duration = 0.0
            a.remaining_duration = a.planned_duration
            a.actual_labor_units = 0.0
            a.remaining_labor_units = a.planned_labor_units
            a.planned_start = a.planned_finish = None
        p.activities.append(a)
        return a

    def tie(pre, suc, kind="Finish to Start", lag=0):
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid
        r.successor_uid = suc.uid
        r.type = kind
        r.lag = lag * 8.0
        p.relations.append(r)
        return r

    # ---- the tree --------------------------------------------------------
    FA = node("Fire Alarm", root.uid)
    L1 = node("Level 1", FA.uid); L1V = node("Valve Rooms", L1.uid)
    L2 = node("Level 2", FA.uid); L2V = node("Valve Rooms", L2.uid)
    PH = node("Penthouse", FA.uid)
    PHS = node("Scaffolding", PH.uid)
    PHN = node("Generator Rooms - North", PH.uid)
    PHSo = node("Generator Rooms - South", PH.uid)
    PHV = node("Valve Rooms", PH.uid)

    # which generator rooms exist, and in which phase
    genroom = {}
    for a in p.activities:
        m = re.search(r'\(Gen (\d{3})\)', str(a.name))
        if not m:
            continue
        ph = re.search(r'MDC1\.(PH\d)\.', str(a.activity_id))
        if ph:
            genroom[m.group(1)] = ph.group(1)
    print(f"generator rooms in our file: {len(genroom)}  "
          f"(missing {sorted(set(f'{i:03d}' for i in range(301,329))-set(genroom))})")

    famil = {ph: byid.get(f"MDC1.{ph}.FA.1900") for ph in ("PH1", "PH2", "PH3")}

    # ---- scaffolding -----------------------------------------------------
    scaf = act("MDC1.FA.PH.SCAF.1000", "Start Scaffolding - Penthouse",
               PHS.uid, SCAFF_DAYS, 4)
    scaf.constraint_type = "Start On or After"
    scaf.constraint_date = f"{SCAFF_START}T00:00:00"
    scaf.planned_start = SCAFF_START
    print(f"scaffolding: {SCAFF_DAYS}d, start on or after {SCAFF_START}")

    # ---- generator room device installs ----------------------------------
    nact = ncomplete = 0
    for rm in sorted(genroom):
        parent = PHN.uid if int(rm) <= 314 else PHSo.uid
        h = H.get(rm)
        actual = None
        if h and h["done"]:
            actual = (h["start"], h["finish"])
            ncomplete += 1
        a = act(f"MDC1.FA.GEN{rm}.1000", f"FA Device Install (Gen {rm})",
                parent, GEN_DAYS, GEN_MEN, actual)
        nact += 1
        if not actual:
            tie(scaf, a, "Finish to Start", LAG_DAYS)
        m = famil.get(genroom[rm])
        if m is not None:
            tie(a, m)
    print(f"generator rooms: {nact} device installs, {ncomplete} written as "
          f"complete on Holder's dates, {nact-ncomplete} fed from the scaffold")

    # ---- valve rooms -----------------------------------------------------
    nv = 0
    for lvl, parent, nums, mil in (("1", L1V, (101, 102, 103, 104), famil["PH1"]),
                                   ("2", L2V, (201, 202, 203, 204), famil["PH2"]),
                                   ("3", PHV, (301, 302, 303, 304), famil["PH3"])):
        prev_last = None
        for n in nums:
            rn = node(f"Valve Room {n}", parent.uid)
            chain = []
            for i, (nm, days) in enumerate(VALVE):
                a = act(f"MDC1.FA.VR{n}.{1000+i*10}", f"{nm} (Valve Rm {n})",
                        rn.uid, days, VALVE_MEN)
                chain.append(a)
                nv += 1
            for i in range(1, len(chain)):
                tie(chain[i-1], chain[i])
            if prev_last is not None:          # one crew, room to room
                tie(prev_last, chain[0])
            prev_last = chain[-1]
        if mil is not None and prev_last is not None:
            tie(prev_last, mil)
    print(f"valve rooms: 12 rooms, {nv} activities, chained room to room")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)]
             if k in mil0 and mil0[k] != d10(a.early_finish)]
    print(f"\nv32 out: {len(p.activities)} activities (+{len(p.activities)-a0}), "
          f"{len(p.relations)} relations (+{len(p.relations)-r0}), "
          f"{len(p.wbs_nodes)} WBS nodes (+{len(p.wbs_nodes)-w0})")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}")
    print(f"   milestones moved: {len(moved)}")
    for k, a, b in sorted(moved):
        print(f"      {k:<24}{a} -> {b}")
    s = byid.get("MDC1.FA.PH.SCAF.1000")
    sc = next(a for a in p.activities if str(a.activity_id) == "MDC1.FA.PH.SCAF.1000")
    print(f"   scaffolding runs {d10(sc.early_start)} -> {d10(sc.early_finish)}")
    gens = [a for a in p.activities
            if re.match(r'FA Device Install \(Gen', str(a.name)) and not a.actual_finish]
    if gens:
        print(f"   forecast gen room FA: {min(d10(a.early_start) for a in gens)}"
              f" -> {max(d10(a.early_finish) for a in gens)}")
    # no loops
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
