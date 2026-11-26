"""MDC-1 v33: add Gen 314.

The room is absent from our schedule while Exhibit S carries 40 activities
for it, Phase 2, actualised through 2026-09-25. It is built by cloning Gen
312, its Phase 2 sibling, so the scope, durations, hours and internal logic
match the rooms either side of it rather than being invented. Relationships
that leave the room are mirrored onto the same outside activities Gen 312
uses.

Actual progress is taken from Exhibit S's own Gen 314 rows: the stages Holder
shows complete there are marked complete here, on his dates, mapped through
the naming differences between the two files (his OH Electrical Steel is our
Install High Steel, his Cable Tray Install our OH Ladder Tray Install, and so
on). Everything after that stays forecast.

The fire alarm device install for the room is added too, on the same footing
as the other northside rooms: fed from the penthouse scaffold with a one week
lag and running into Fire Alarm Devices Complete (PH2).
"""
import collections, copy, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v32.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v33.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")
TEMPLATE, NEW = "312", "314"

# our activity name -> the Exhibit S stage that reports its progress
MAP = {"Install High Steel": "OH Electrical Steel",
       "Install Hangers": "Yoke Hanger Core Drill",
       "OH Ladder Tray Install": "Cable Tray Install",
       "Hang SCR **WBO": "SCR Install",
       "Epoxy Floor **WBO": "Floor Epoxy",
       "OH Branch Raceway": "OH Electrical Steel",
       "Equipment Layout/Isolator Install": "Generator Isolator",
       "Set Generator": "Set Engine (3MW)",
       "Set LBB": "LBB Install",
       "IMP Wall Install **WBO": "IMP Wall"}


def d10(v):
    return str(v)[:10] if v else ""


def holder_room(rm):
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Schedule"]
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I["Activity Name"]] or "")
        if f"(Gen {rm})" not in nm:
            continue
        stage = re.sub(r'\s*\(Gen \d{3}\).*$', '', nm).strip()
        out[stage] = {"done": str(r[I["Finish Status"]] or "") == "Actual",
                      "start": d10(r[I["Start"]]), "finish": d10(r[I["Finish"]])}
    return out


def main():
    H = holder_room(NEW)
    hdone = {k: v for k, v in H.items() if v["done"]}
    print(f"Exhibit S Gen {NEW}: {len(H)} activities, {len(hdone)} complete")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    a0, r0, w0 = len(p.activities), len(p.relations), len(p.wbs_nodes)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v32 in: {a0} activities, {r0} relations, finish {f0}")

    wbs = {n.uid: n for n in p.wbs_nodes}
    byid = {str(a.activity_id): a for a in p.activities}
    src = [a for a in p.activities if f"(Gen {TEMPLATE})" in str(a.name)]
    if not src:
        print("template room not found"); return
    print(f"template Gen {TEMPLATE}: {len(src)} activities")

    # clone the room's WBS folders
    srcnodes = {a.wbs_uid for a in src}
    nextW = max(int(n.uid) for n in p.wbs_nodes if str(n.uid).isdigit()) + 1
    nodemap = {}
    for u in sorted(srcnodes, key=lambda u: str(wbs[u].name)):
        old = wbs[u]
        parent = old.parent_uid
        if parent in wbs and TEMPLATE in str(wbs[parent].name):
            gp = wbs[parent]
            if gp.uid not in nodemap:
                n2 = copy.deepcopy(gp)
                n2.uid = str(nextW); nextW += 1
                n2.name = str(gp.name).replace(TEMPLATE, NEW)
                p.wbs_nodes.append(n2); wbs[n2.uid] = n2
                nodemap[gp.uid] = n2.uid
            parent = nodemap[gp.uid]
        n = copy.deepcopy(old)
        n.uid = str(nextW); nextW += 1
        n.name = str(old.name).replace(TEMPLATE, NEW)
        n.parent_uid = parent
        p.wbs_nodes.append(n); wbs[n.uid] = n
        nodemap[u] = n.uid
    print(f"   cloned {len(nodemap)} WBS folders")

    # clone the activities
    nextA = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
    used = set(byid)
    amap = {}
    ncomp = 0
    for a in src:
        b = copy.deepcopy(a)
        b.uid = str(nextA); nextA += 1
        aid = str(a.activity_id).replace(TEMPLATE, NEW)
        while aid in used:
            aid += "x"
        used.add(aid)
        b.activity_id = aid
        b.name = str(a.name).replace(f"(Gen {TEMPLATE})", f"(Gen {NEW})")
        b.wbs_uid = nodemap.get(a.wbs_uid, a.wbs_uid)
        b.early_start = b.early_finish = None
        b.late_start = b.late_finish = None
        b.total_float = b.free_float = None
        b.constraint_type = None
        b.constraint_date = None
        stage_name = re.sub(r'\s*\(Gen \d{3}\).*$', '', str(b.name)).strip()
        h = H.get(MAP.get(stage_name, stage_name))
        if h and h["done"]:
            b.actual_start, b.actual_finish = h["start"], h["finish"]
            b.planned_start, b.planned_finish = h["start"], h["finish"]
            b.status = "Completed"; b.percent_complete = 100.0
            b.actual_duration = b.planned_duration; b.remaining_duration = 0.0
            b.actual_labor_units = b.planned_labor_units
            b.remaining_labor_units = 0.0
            ncomp += 1
        else:
            b.actual_start = b.actual_finish = None
            b.planned_start = b.planned_finish = None
            b.status = "Not Started"; b.percent_complete = 0.0
            b.actual_duration = 0.0; b.remaining_duration = b.planned_duration
            b.actual_labor_units = 0.0
            b.remaining_labor_units = b.planned_labor_units
        p.activities.append(b)
        amap[a.uid] = b
    print(f"   cloned {len(amap)} activities, {ncomp} marked complete from Exhibit S")

    # clone the logic: inside the room, and mirrored where it leaves it
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
    inside = outside = 0
    for r in list(p.relations):
        pi, si = r.predecessor_uid in amap, r.successor_uid in amap
        if not pi and not si:
            continue
        n = copy.deepcopy(r)
        n.uid = str(nextR); nextR += 1
        n.predecessor_uid = amap[r.predecessor_uid].uid if pi else r.predecessor_uid
        n.successor_uid = amap[r.successor_uid].uid if si else r.successor_uid
        p.relations.append(n)
        if pi and si:
            inside += 1
        else:
            outside += 1
    print(f"   cloned {inside} ties inside the room, {outside} to the rest of the file")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)]
             if k in mil0 and mil0[k] != d10(a.early_finish)]
    new = [a for a in p.activities if f"(Gen {NEW})" in str(a.name)]
    print(f"\nv33 out: {len(p.activities)} activities (+{len(p.activities)-a0}), "
          f"{len(p.relations)} relations (+{len(p.relations)-r0}), "
          f"{len(p.wbs_nodes)} nodes (+{len(p.wbs_nodes)-w0})")
    print(f"   Gen {NEW}: {len(new)} activities, "
          f"{sum(1 for a in new if a.actual_finish)} complete, "
          f"runs {min(d10(a.early_start) for a in new)} -> "
          f"{max(d10(a.early_finish) for a in new)}")
    print(f"   hours added: "
          f"{sum(float(a.planned_labor_units or 0) for a in new):,.0f}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, a, b in sorted(moved):
        print(f"      {k:<24}{a} -> {b}")
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
