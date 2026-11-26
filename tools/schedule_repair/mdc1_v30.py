"""MDC-1 v30: close the admin gate properly, and trim the phase Cx stagger.

v29 cut the direct tie from the admin fit-out buckets to Level 3 Commissioning
Start, but routed the admin fire alarm stage into Fire Alarm Devices Complete
(PH1), and that milestone still feeds Level 3 Cx. So the gate came back by a
different route: admin OH Fire Alarm finishes 2027-05-04 while the data halls
finish their devices 2026-10-16 and the electrical room VESDA racks 2026-12-02,
and the admin date is the one the milestone waits on.

The same reasoning v29 used applies here. Admin fit-out does not gate data
hall commissioning. The admin fire alarm stages are moved onto the route the
rest of the admin chain already takes, out to Substantial Completion, and off
Fire Alarm Devices Complete. The milestone then reflects what it is named
for: the fire alarm devices in the rooms being commissioned.

Second, the phase-to-phase commissioning stagger runs SS+28 and SS+36, which
is 64 days against the contract's 18 and 17. Both are trimmed to contract.

Nothing else is touched: no durations, no hours, no other logic.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v29.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v30.xml"

FA_MILESTONE = "MDC1.PH1.FA.1900"
STAGGER = {("MDC1.MIL.PH1.1000", "MDC1.MIL.PH2.9000"): 18,
           ("MDC1.MIL.PH2.9000", "MDC1.MIL.PH3.9000"): 17}
# Holder's contract targets, from his own export
HOLDER = {"MDC1.MIL.PH1.1060": "2027-05-28",
          "MDC1.MIL.PH2.9040": "2027-07-12",
          "MDC1.MIL.PH3.9040": "2027-08-23"}


def d10(v):
    return str(v)[:10] if v else ""


def days(a, b):
    try:
        return (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days
    except Exception:
        return None


def snapshot(p):
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    byid = {str(a.activity_id): a for a in p.activities}
    fin = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    sc = {k: d10(byid[k].early_finish) for k in HOLDER if k in byid}
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    return fin, sc, neg


def main():
    p = load_xml(SRC)
    f0, sc0, neg0 = snapshot(p)
    A = {a.uid: a for a in p.activities}
    byid = {str(a.activity_id): a for a in p.activities}
    w = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in w:
            o.append(str(w[u].name))
            u = w[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    print(f"v29 in: finish {f0}, negative float {neg0}")
    print("        substantial completion   "
          + "   ".join(f"{k.split('.')[2]} {v}" for k, v in sorted(sc0.items())))

    # ---- 1. admin fire alarm off the Cx milestone ------------------------
    mil = byid.get(FA_MILESTONE)
    if not mil:
        print(f"   ! {FA_MILESTONE} not found")
        return
    feeders = [r for r in p.relations if r.successor_uid == mil.uid]
    admin = [r for r in feeders
             if "Admin Space" in path(A[r.predecessor_uid].wbs_uid)]
    print(f"\n1. {FA_MILESTONE} has {len(feeders)} predecessors, "
          f"{len(admin)} of them admin space:")
    for r in admin:
        q = A[r.predecessor_uid]
        print(f"      {q.activity_id:<24}{str(q.name)[:44]:<46}{d10(q.early_finish)}")
    nonadmin = [d10(A[r.predecessor_uid].early_finish) for r in feeders
                if r not in admin]
    print(f"   latest non-admin predecessor: {max(nonadmin) if nonadmin else '-'}")

    # where the rest of the admin chain already goes
    sc_mil = byid.get("MDC1.MIL.PH1.1060")
    targets = collections.Counter()
    for a in p.activities:
        if "Admin Space" not in path(a.wbs_uid):
            continue
        for r in p.relations:
            if r.predecessor_uid == a.uid and r.successor_uid != mil.uid:
                targets[str(A[r.successor_uid].activity_id)] += 1
    print(f"   where other admin activities already point: "
          f"{targets.most_common(4)}")

    moved = 0
    for r in admin:
        if sc_mil is None:
            break
        r.successor_uid = sc_mil.uid
        moved += 1
    # drop any self or duplicate tie the move created
    seen, keep = set(), []
    for r in p.relations:
        if r.predecessor_uid == r.successor_uid:
            continue
        k = (r.predecessor_uid, r.successor_uid, r.type, r.lag)
        if k in seen:
            continue
        seen.add(k)
        keep.append(r)
    collapsed = len(p.relations) - len(keep)
    p.relations[:] = keep
    print(f"   moved {moved} admin fire alarm ties onto Substantial Completion "
          f"(PH1), {collapsed} duplicates collapsed")

    # ---- 2. phase stagger to contract ------------------------------------
    n = 0
    for r in p.relations:
        k = (str(A[r.predecessor_uid].activity_id) if r.predecessor_uid in A else "",
             str(A[r.successor_uid].activity_id) if r.successor_uid in A else "")
        if k in STAGGER:
            was = float(r.lag or 0) / 8.0
            want = STAGGER[k]
            if was > want:
                print(f"2. {k[0]} -> {k[1]}  lag {was:.0f}d -> {want}d")
                r.lag = want * 8.0
                n += 1
    print(f"   {n} stagger lags trimmed")

    # ---- verify ----------------------------------------------------------
    f1, sc1, neg1 = snapshot(p)
    print(f"\nv30 out: finish {f0} -> {f1}  ({days(f1, f0):+d}d)")
    print(f"   negative float {neg0} -> {neg1}")
    print(f"\n   {'milestone':<12}{'v29':<13}{'v30':<13}{'gain':>6}"
          f"   {'Holder':<13}{'vs Holder':>10}")
    for k in sorted(HOLDER):
        a, b, h = sc0.get(k, ""), sc1.get(k, ""), HOLDER[k]
        print(f"   {k.split('.')[2]:<12}{a:<13}{b:<13}{days(b,a):>5}d"
              f"   {h:<13}{days(b,h):>9}d")
    # nothing else moved
    q = load_xml(SRC)
    assert len(p.activities) == len(q.activities), "activity count changed"
    assert len(p.relations) <= len(q.relations), "relations added"
    hp = sum(float(a.planned_labor_units or 0) for a in p.activities)
    hq = sum(float(a.planned_labor_units or 0) for a in q.activities)
    assert abs(hp - hq) < 1, "hours changed"
    # no cycles
    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)
    colour = {}
    def acyclic(s):
        stack = [(s, iter(adj[s]))]
        colour[s] = 1
        while stack:
            v, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[v] = 2
                stack.pop()
            elif colour.get(nxt, 0) == 1:
                return False
            elif colour.get(nxt, 0) == 0:
                colour[nxt] = 1
                stack.append((nxt, iter(adj[nxt])))
        return True
    ok = all(acyclic(v) for v in list(A) if colour.get(v, 0) == 0)
    print(f"\n   checks: activities and hours unchanged, cycles: "
          f"{'none' if ok else 'PRESENT'}")
    assert ok
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
