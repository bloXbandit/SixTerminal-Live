"""MDC-1 v26: fix the three things the conflict review turned up.

Reviewing the 19 date/logic conflicts in the new rooms, our logic is right in
14 of them -- terminations really do precede testing, testing really does
precede start-up, and Holder's date is simply optimistic. F9 pushing those out
is the schedule doing its job. But three of the nineteen were mine.

1  PARALLEL WORK CHAINED IN SERIES. v25 ordered each room off Holder's dates
   and chained consecutive rows. Sixteen activities share a start date with a
   sibling -- he starts MEP OH Hangers and In-wall Rough-In together, FanWal
   Feeders and the EDP/CDU feeders together, Floor Finishes and Device Trim
   together -- and chaining them invented 11 finish-to-start constraints on
   work that runs side by side.

   They become Start-to-Start with no lag rather than being deleted. That says
   what is true, the two release together, and it keeps the network connected:
   dropping them outright would leave rows with no predecessor for F9 to drag
   to the data date.

2  DATES TAKEN FROM HALF THE PREDECESSORS. The 14 rows Holder names per cable
   rather than per room were dated from their predecessors' finishes, but the
   pass dated whatever it could on each sweep, so a row could be dated off the
   predecessors that happened to have dates already and ignore the ones that
   did not. That is how Set HVS Gear came out starting 11/02 behind a
   predecessor finishing 11/18. They are re-dated here, and a row is only
   dated once EVERY predecessor has a date.

3  TWO TIES THAT RUN AGAINST BOTH TESTS. A tie is only wrong if it contradicts
   Holder's dates AND the order the trade normally runs in. Mechanical Final
   Connections sitting behind Testing & Inspections fails both, and so does
   Install Vesda driving In-Wall Inspections -- overhead smoke detection does
   not gate an in-wall inspection in either direction. Those are dropped.

   The two that looked backwards but are not: FA Permit Approved driving
   Install Vesda in the galleries. The stage table had no entry for a permit,
   so it scored as unknown. A fire alarm permit absolutely precedes a VESDA
   install, F9 should push the Vesda out to 11/16, and the tie stays.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v25.xml")
byu = {a.uid: a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
NEW = re.compile(r"^MDC1\.PH\d\.(ERR20[12]|HVR101|GAL(101|102|202))\.")
WORKDAYS = frozenset({0, 1, 2, 3, 4})


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
    return " / ".join(reversed(o))


DD = D(p.data_date)
rooms = collections.defaultdict(list)
for a in p.activities:
    m = NEW.search(str(a.activity_id))
    if m:
        rooms[m.group(1)].append(a)
NEWU = {a.uid for acts in rooms.values() for a in acts}
B = dict(rels=len(p.relations),
         ss=sum(1 for r in p.relations if r.type == "Start to Start"))


def anchor(a):
    return D(a.actual_start) or D(a.early_start) or D(a.planned_start)


# ===== 1. parallel work: FS -> SS ====================================
print("=" * 86)
print("1. WORK HOLDER STARTS TOGETHER, CHAINED IN SERIES BY v25")
print("=" * 86)
n = 0
for r in p.relations:
    if r.predecessor_uid not in NEWU or r.successor_uid not in NEWU:
        continue
    if r.type != "Finish to Start":
        continue
    q, s = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
    if not q or not s:
        continue
    aq, as_ = anchor(q), anchor(s)
    if aq and as_ and aq == as_:
        r.type = "Start to Start"
        r.lag = 0.0
        for x in (q, s):
            c = (x.udfs or {}).get("COMMENTS", "")
            x.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
                f"Runs alongside {(s if x is q else q).activity_id}: Holder "
                f"starts both {aq:%m/%d/%y}, so the tie is start-to-start, "
                f"not one after the other."))[:248])
        n += 1
        print(f"   {str(q.name)[:34]:<36}-> {str(s.name)[:34]:<36}"
              f"both {aq}   FS -> SS")
print(f"\nconverted to start-to-start: {n}")

# ===== 2. two ties that fail both tests ==============================
print("\n" + "=" * 86)
print("2. TIES THAT CONTRADICT HOLDER'S DATES AND THE TRADE ORDER")
print("=" * 86)
KILL = [("Testing & Inspections", "Mechanical Final Connections"),
        ("Install Vesda", "In-Wall Inspections")]
dead = []
for r in p.relations:
    q, s = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
    if not q or not s or q.uid not in NEWU or s.uid not in NEWU:
        continue
    qn = str(q.name).split(" (")[0].strip()
    sn = str(s.name).split(" (")[0].strip()
    for a_, b_ in KILL:
        if qn.lower().startswith(a_.lower()) and sn.lower().startswith(b_.lower()):
            dead.append(r)
            print(f"   dropped  {str(q.activity_id):<22}{qn[:32]:<34}-> "
                  f"{sn[:32]}")
if dead:
    dk = {(r.predecessor_uid, r.successor_uid) for r in dead}
    p.relations = [r for r in p.relations
                   if (r.predecessor_uid, r.successor_uid) not in dk]
print(f"\ndropped: {len(dead)}")

# ===== 3. re-date the logic-dated rows, all predecessors first ========
print("\n" + "=" * 86)
print("3. RE-DATING THE ROWS THAT WERE DATED FROM LOGIC")
print("=" * 86)
pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)
MARK = "from this room's logic"
redo = [a for a in p.activities
        if a.uid in NEWU and MARK in str((a.udfs or {}).get("COMMENTS", ""))]
print(f"rows dated from logic in v25: {len(redo)}")
for a in redo:
    a.early_start = a.early_finish = None
    a.planned_start = a.planned_finish = None


def fwd(d, days):
    d2, k = d, 0
    while k < max(int(days) - 1, 0):
        d2 += dt.timedelta(days=1)
        if d2.weekday() in WORKDAYS:
            k += 1
    return d2


def nextwd(d):
    d += dt.timedelta(days=1)
    while d.weekday() not in WORKDAYS:
        d += dt.timedelta(days=1)
    return d


todo = {a.uid for a in redo}
done = []
for sweep in range(20):
    if not todo:
        break
    progressed = False
    for u in sorted(todo):
        a = byu[u]
        ps = [byu[r.predecessor_uid] for r in pred[a.uid]
              if r.predecessor_uid in byu]
        # every predecessor must have a date before this one can take one
        if any(p_.uid in todo for p_ in ps):
            continue
        fins = []
        for r in pred[a.uid]:
            q = byu.get(r.predecessor_uid)
            if not q:
                continue
            if r.type == "Start to Start":
                d_ = D(q.early_start) or D(q.actual_start) or D(q.planned_start)
            else:
                d_ = (D(q.early_finish) or D(q.actual_finish)
                      or D(q.planned_finish))
            if d_:
                fins.append(d_ if r.type == "Start to Start" else nextwd(d_))
        s = max(fins + [DD]) if fins else DD
        f = fwd(s, (a.planned_duration or 8) / 8)
        a.early_start = a.planned_start = s.isoformat()
        a.early_finish = a.planned_finish = f.isoformat()
        c = re.sub(r"\s*Dated [^.]*from this room's logic[^.]*\.", "",
                   (a.udfs or {}).get("COMMENTS", "")).strip()
        a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
            f"Dated {s:%m/%d/%y}-{f:%m/%d/%y} in v26 from this room's logic, "
            f"behind all {len(ps)} predecessor(s); Holder names this work per "
            f"cable, so he has no row to date it from."))[:248])
        done.append((str(a.activity_id), str(a.name), s, f, len(ps)))
        todo.discard(u)
        progressed = True
    if not progressed:
        break
print(f"re-dated: {len(done)}   still undated: {len(todo)}")
print(f"{'activity':<24}{'name':<40}{'start':>12}{'finish':>12}{'preds':>7}")
for i, nm, s, f, k in sorted(done, key=lambda x: x[2]):
    print(f"{i:<24}{nm[:38]:<40}{str(s):>12}{str(f):>12}{k:>7}")
for u in todo:
    a = byu[u]
    print(f"   STILL UNDATED {a.activity_id} {a.name}")

# ===== checks ========================================================
print("\n" + "=" * 86)
print("CHECKS")
print("=" * 86)
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
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)
    succ[r.predecessor_uid].append(r)
print(f"\n{'room':<9}{'acts':>5}{'no pred':>9}{'no succ':>9}{'undated':>9}"
      f"{'conflicts':>11}")
tc = 0
for rm, acts in sorted(rooms.items()):
    und = [a for a in acts if a.status == "Not Started" and not a.early_start]
    bad = 0
    for a in acts:
        es = D(a.early_start)
        if not es:
            continue
        for r in pred[a.uid]:
            q = byu.get(r.predecessor_uid)
            if not q:
                continue
            if r.type == "Start to Start":
                d_ = D(q.early_start) or D(q.actual_start)
                if d_ and es < d_:
                    bad += 1
            else:
                d_ = D(q.early_finish) or D(q.actual_finish)
                if d_ and es < d_:
                    bad += 1
    tc += bad
    print(f"{rm:<9}{len(acts):>5}"
          f"{len([a for a in acts if not pred[a.uid]]):>9}"
          f"{len([a for a in acts if not succ[a.uid]]):>9}"
          f"{len(und):>9}{bad:>11}")
print(f"\ndate/logic conflicts: 19 in v25  ->  {tc} now")
print(f"relationships {B['rels']} -> {len(p.relations)}   "
      f"start-to-start {B['ss']} -> "
      f"{sum(1 for r in p.relations if r.type == 'Start to Start')}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
outp = f"{SCR}/MDC1_logic_repaired_v26.xml"
write_p6_xml(p, outp, include_resources=False, include_udfs=True)
print(f"\nwrote {outp}")
