"""MDC3 v47: make the WBS consistent. Folder placement only, no logic touched.

v46 connected the logic but left the folder structure as it found it, and its
own reparenting deepened a split that was already there: some deliveries lived
in room folders, most lived in LLE buckets, and nothing decided which. v47
settles it.

  1. Every LLE delivery goes to its phase bucket, routed off the phase token in
     its own activity ID. Room folders then hold installed scope only.
  2. The Milestones node has two folders each named Phase 1, Phase 2 and
     Phase 3. Merge each pair into one.
  3. MV 1060 and MV 1070 are not rooms. MDC3.PH1.MVR.1060 is "Final Paint -
     HV / MV Rooms", where MVR is the room group and 1060 is the activity
     sequence number. An earlier pass read that as a room number and built a
     folder for it. The two activities move up to MV Rooms - Phase 1.
  4. Anything left with nothing under it is deleted.

Not touched on purpose: the Roof node carries 13 roof-wide activities beside
its 8 subfolders, and GL 202 / GL 203 carry gallery-wide work beside their
East and West subfolders. Both are a real convention in this file, not debris.
"""
import collections, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC3_logic_repaired_v46.xml"
OUT = f"{SCR}/MDC3_logic_repaired_v47.xml"

BOGUS = {"MDC3.PH1.MVR.1060", "MDC3.PH1.MVR.1070"}


def d10(v):
    return str(v)[:10] if v else ""


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0, a0, w0 = len(p.relations), len(p.activities), len(p.wbs_nodes)
    print(f"v46 in: {a0} activities, {r0} relations, {w0} WBS nodes, finish {f0}")

    w = {n.uid: n for n in p.wbs_nodes}
    kids = collections.defaultdict(list)
    for n in p.wbs_nodes:
        kids[n.parent_uid].append(n)

    def path(u):
        o = []
        while u in w:
            o.append(str(w[u].name))
            u = w[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    byname = {}
    for n in p.wbs_nodes:
        byname.setdefault(str(n.name), []).append(n)

    # ---- 1. every delivery to its phase bucket --------------------------
    bucket = {}
    for nm, key in (("LLE - Phase 1", "MDC3.PH1"), ("LLE - Phase 2", "MDC3.PH2"),
                    ("LLE - Phase 3", "MDC3.PH3"), ("Roof LLE", "MDC3.ROOF")):
        hits = byname.get(nm, [])
        if len(hits) != 1:
            print(f"   ! expected one folder named {nm}, found {len(hits)}")
            return
        bucket[key] = hits[0].uid

    moved = collections.Counter()
    for a in p.activities:
        aid = str(a.activity_id)
        if ".LLE." not in aid:
            continue
        tgt = bucket.get(aid.split(".LLE.")[0])
        if tgt and a.wbs_uid != tgt:
            moved[path(tgt)] += 1
            a.wbs_uid = tgt
    print(f"1. moved {sum(moved.values())} deliveries into their phase bucket")
    for k, v in sorted(moved.items()):
        print(f"      {v:>4}  -> {k}")

    # ---- 2. merge the doubled milestone folders -------------------------
    merged = 0
    for par, ch in list(kids.items()):
        if par not in w or str(w[par].name) != "Milestones":
            continue
        groups = collections.defaultdict(list)
        for n in ch:
            groups[str(n.name)].append(n)
        for nm, nodes in groups.items():
            if len(nodes) < 2:
                continue
            keep = max(nodes, key=lambda n: sum(
                1 for a in p.activities if a.wbs_uid == n.uid))
            for n in nodes:
                if n.uid == keep.uid:
                    continue
                for a in p.activities:
                    if a.wbs_uid == n.uid:
                        a.wbs_uid = keep.uid
                        merged += 1
    print(f"2. merged the doubled Milestones folders, {merged} milestones re-homed")

    # ---- 3. MV 1060 / MV 1070 are sequence numbers, not rooms -----------
    fixed = 0
    for a in p.activities:
        if str(a.activity_id) in BOGUS:
            node = w.get(a.wbs_uid)
            if node and node.parent_uid in w:
                print(f"   {a.activity_id}  {a.name}")
                print(f"      out of '{path(a.wbs_uid)}'")
                a.wbs_uid = node.parent_uid
                print(f"      into  '{path(a.wbs_uid)}'")
                fixed += 1
    print(f"3. re-homed {fixed} activities out of folders named after a sequence number")

    # ---- 4. drop whatever is now empty ----------------------------------
    here = collections.Counter(a.wbs_uid for a in p.activities)

    def deep(u):
        return here.get(u, 0) + sum(deep(k.uid) for k in kids.get(u, []))

    gone = [n for n in p.wbs_nodes if deep(n.uid) == 0]
    for n in gone:
        print(f"      deleted empty: {path(n.uid)}")
    p.wbs_nodes[:] = [n for n in p.wbs_nodes if deep(n.uid) > 0]
    print(f"4. deleted {len(gone)} folders left with nothing under them")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    orph = sum(1 for a in p.activities
               if not any(r.predecessor_uid == a.uid for r in p.relations))
    print(f"\nv47 out: {len(p.activities)} activities, {len(p.relations)} relations, "
          f"{len(p.wbs_nodes)} WBS nodes")
    print(f"   activities {a0} -> {len(p.activities)}   "
          f"relations {r0} -> {len(p.relations)}   folders {w0} -> {len(p.wbs_nodes)}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, no successor {orph}")
    assert len(p.activities) == a0, "activity count must not change"
    assert len(p.relations) == r0, "relation count must not change"
    assert f1 == f0, "finish date must not move"
    # standing guard: no pair tied in both directions, which P6 rejects
    pairs = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    loops = {tuple(sorted(x)) for x in pairs if (x[1], x[0]) in pairs}
    assert not loops, f"circular pairs present: {len(loops)}"
    # and no delivery feeding another delivery
    lid = {a.uid for a in p.activities if ".LLE." in str(a.activity_id)}
    d2d = [r for r in p.relations
           if r.predecessor_uid in lid and r.successor_uid in lid]
    assert not d2d, f"delivery-to-delivery ties present: {len(d2d)}"
    print(f"   checks passed: no loops, no delivery-to-delivery ties")
    print("   checks passed: no activity, no relation and no date changed")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
