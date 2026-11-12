"""MDC-1 v29: break up the admin-room buckets and take them off the Cx gate.

Two activities were holding the whole commissioning programme:

    MDC1.PH1.1ST.FL.1050  Support / Administrative Rooms  100d  10,250 h
    MDC1.PH1.2ND.FL.1040  Support / Administrative Rooms  185d  31,202 h

Each has no predecessor, so a schedule run drags it to the data date and
counts 100 or 185 days straight out from there. Each has exactly one
successor: Level 3 Commissioning Start. So second-floor admin fit-out was
gating commissioning of the data halls, and because the phase Cx starts are
chained SS+28 and SS+36, it gated all three phases.

Two things are wrong with that and both get fixed here.

THE TIE. Admin fit-out does not gate data hall commissioning. Holder does not
carry these activities at all and nothing in his file ties admin work to Cx.
The tie is replaced: this work now runs out to Substantial Completion, which
is true -- the building is not substantially complete with the offices
unfinished -- and off the commissioning path, which it never belonged on.

THE BUCKET. A 185-day activity is not an activity, it is a folder with a
duration. Holder carries the same scope as 62 electrical activities in six
stages -- OH Electrical, In-Wall Rough-In, OH Fire Alarm, OH Trim-Out
Components, Install Lights, OH Device Trim-Out -- running 08/26/26 to
01/12/27. Each bucket is split into those six, weighted by how many
activities Holder has of each, with the hours and the duration divided on the
same weights so neither total moves. They chain in that order, and the chain
is fed from the shell rather than floating at the data date.

Nothing else is touched. No other logic, no dates elsewhere, no loadings.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Activity, Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v28.xml")
byu = {a.uid: a for a in p.activities}
byi = {str(a.activity_id): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


DD = D(p.data_date)
have = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
out = collections.defaultdict(set)
for r in p.relations:
    out[r.predecessor_uid].add(r.successor_uid)
nru = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
nau = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1


def reaches(s, t, cap=400000):
    seen, stack, n = {s}, [s], 0
    while stack and n < cap:
        n += 1
        for v in out[stack.pop()]:
            if v == t:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def tie(s, t):
    global nru
    if s == t or (s, t) in have or reaches(t, s):
        return False
    p.relations = list(p.relations) + [Relation(
        uid=str(nru), predecessor_uid=s, successor_uid=t,
        type="Finish to Start", lag=0.0)]
    have.add((s, t))
    out[s].add(t)
    nru += 1
    return True


# Holder's six admin electrical stages, weighted by how many he carries
STAGES = [("OH Electrical", 12), ("In-Wall Rough-In", 20),
          ("OH Fire Alarm", 12), ("OH Trim-Out Components", 16),
          ("Install Lights", 1), ("OH Device Trim-Out", 1)]
TOTW = sum(w for _, w in STAGES)

BUCKETS = [("MDC1.PH1.1ST.FL.1050", "1st Floor", "1ST"),
           ("MDC1.PH1.2ND.FL.1040", "2nd Floor", "2ND")]

# what feeds admin fit-out: the shell being dried in
feed = next((a for a in p.activities
             if re.search(r"Temporary Dry In", str(a.name), re.I)), None)
# what it runs out to instead of commissioning
scomp = byi.get("MDC1.PH1.C.OUT.4410") or byi.get("MDC1.MIL.PH1.1060")
facomp = byi.get("MDC1.PH1.FA.1900")
print(f"feed      : {feed.activity_id if feed else '-'} "
      f"{str(feed.name)[:40] if feed else ''} ({feed.status if feed else ''})")
print(f"runs out to: {scomp.activity_id if scomp else '-'} "
      f"{str(scomp.name)[:40] if scomp else ''}")
print(f"FA marker : {facomp.activity_id if facomp else '-'}")

print("\n" + "=" * 92)
print("BREAKING UP THE BUCKETS")
print("=" * 92)
made_all = []
for aid, floor, tag in BUCKETS:
    a = byi.get(aid)
    if a is None:
        continue
    H = a.planned_labor_units or 0
    Dur = (a.planned_duration or 8) / 8
    cal = a.calendar_uid
    wbsu = a.wbs_uid
    print(f"\n{aid}  {a.name}  ({floor})")
    print(f"   was {Dur:,.0f} days / {H:,.0f} h, 0 predecessors, "
          f"1 successor (Level 3 Cx)")
    # what it currently points at
    old = [r for r in p.relations
           if r.predecessor_uid == a.uid or r.successor_uid == a.uid]
    for r in old:
        q, s = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
        print(f"   dropping tie: {str(q.name)[:34] if q else '?'} -> "
              f"{str(s.name)[:40] if s else '?'}")
    dk = {(r.predecessor_uid, r.successor_uid) for r in old}
    p.relations = [r for r in p.relations
                   if (r.predecessor_uid, r.successor_uid) not in dk]
    for s_, t_ in dk:
        have.discard((s_, t_))
        out[s_].discard(t_)
    # the six stages
    made = []
    for i, (nm, w) in enumerate(STAGES):
        hh = round(H * w / TOTW, 1)
        dd_ = max(round(Dur * w / TOTW), 1)
        n = Activity(
            uid=str(nau), activity_id=f"MDC1.PH1.{tag}.FL.{1500 + i * 10}",
            name=f"{nm} - Support / Administrative Rooms ({floor})"[:118],
            wbs_uid=wbsu, calendar_uid=cal, activity_type="Task Dependent",
            status="Not Started", planned_duration=float(dd_ * 8),
            remaining_duration=float(dd_ * 8), actual_duration=0.0,
            percent_complete=0.0, planned_labor_units=hh,
            actual_labor_units=0.0, remaining_labor_units=hh)
        n.udfs["Number of Electricians"] = str(max(1, round(hh / (dd_ * 5.07))))
        n.udfs["COMMENTS"] = (
            f"Split out of {aid} in v29, which was a single {Dur:,.0f}-day "
            f"{H:,.0f}-hour bucket with no predecessor gating all three "
            f"phases of commissioning. Six stages follow Holder's own admin "
            f"scope, weighted {w}/{TOTW} by his activity count.")[:248]
        p.activities = list(p.activities) + [n]
        byu[n.uid] = n
        nau += 1
        made.append(n)
        print(f"   + {n.activity_id:<24}{nm:<26}{dd_:>4}d{hh:>9,.0f}h")
    # chain them
    for i in range(len(made) - 1):
        tie(made[i].uid, made[i + 1].uid)
    # fed from the shell, out to substantial completion (NOT commissioning)
    if feed is not None:
        tie(feed.uid, made[0].uid)
    if facomp is not None:
        fa = next((x for x in made if "Fire Alarm" in str(x.name)), None)
        if fa is not None:
            tie(fa.uid, facomp.uid)
    if scomp is not None:
        tie(made[-1].uid, scomp.uid)
    # retire the bucket
    p.activities = [x for x in p.activities if x.uid != a.uid]
    made_all += made
    print(f"   bucket removed; {len(made)} stages replace it, "
          f"{sum(x.planned_labor_units or 0 for x in made):,.0f} h "
          f"(was {H:,.0f})")

# ===== checks ========================================================
print("\n" + "=" * 92)
print("CHECKS")
print("=" * 92)
live = {a.uid for a in p.activities}
print(f"relations pointing at a missing activity: "
      f"{sum(1 for r in p.relations if r.predecessor_uid not in live or r.successor_uid not in live)}")
o2 = collections.defaultdict(set)
for r in p.relations:
    o2[r.predecessor_uid].add(r.successor_uid)
col = collections.defaultdict(int)
cyc = 0
for s in list(live):
    if col[s]:
        continue
    st = [(s, iter(o2[s]))]
    col[s] = 1
    while st:
        n_, it = st[-1]
        adv = False
        for v in it:
            if col[v] == 1:
                cyc += 1
            elif col[v] == 0:
                col[v] = 1
                st.append((v, iter(o2[v])))
                adv = True
                break
        if not adv:
            col[n_] = 2
            st.pop()
print(f"cycles: {cyc}")
pre = {r.successor_uid for r in p.relations}
suc = {r.predecessor_uid for r in p.relations}
isl = [a for a in made_all if a.uid not in pre or a.uid not in suc]
print(f"new stages with no predecessor or successor: {len(isl)}")
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
oe = [a for a in p.activities if a.status != "Completed"
      and not WBO.search(str(a.name)) and a.uid not in pre]
print(f"open ends in the file: {len(oe)}  (was 88)")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"budget {sum(a.planned_labor_units or 0 for a in p.activities):,.0f} h")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
outp = f"{SCR}/MDC1_logic_repaired_v29.xml"
write_p6_xml(p, outp, include_resources=False, include_udfs=True)
print(f"\nwrote {outp}")
