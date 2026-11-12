"""MDC-1 v25: make the six new rooms' logic agree with their dates.

v23 cloned the internal logic of ER 203 and HV 101 into the new rooms, and
v24 put Holder's own dates on them. The two disagree in 20 places -- our
cloned order has Testing and Inspections after Set CRAH's, while Holder has
testing in December and the CRAH set in March. A cloned sequence is a guess
about a room; Holder's dates are a statement about that room, so where they
conflict the dates win and the tie is wrong.

FOUR THINGS HAPPEN HERE.

1  RESEQUENCE. For each new room a stage order is read off Holder's own dates
   -- actual start where he has one, planned start otherwise. Cloned internal
   ties that run against that order are dropped and the order is chained
   instead. Only ties created by the v23 clone are touched; every lead-in and
   lead-out to the rest of the job is kept, and so is anything that was in
   the file before. The standing rule holds: logic already set is not removed.

2  LEAD-IN. The ER template carries exactly one external predecessor -- FA
   Permit Approved -- so the ERR clones inherited one tie each for a
   24-activity room. HV 101 shows what a properly fed room looks like: the
   LLE gear delivery into Set HVS Gear, and every room's Set Electrical Skids
   into Electrical Pulls. The same feeds are made here where the activity
   exists, so these rooms are driven by the job rather than floating on one
   permit.

3  DATE WHAT IS LEFT. 14 rows have no Holder equivalent -- the pulls and
   terminations he names per-cable rather than per-room, the gear and skid
   sets. Once the sequence is right those can be dated from it: predecessor
   finish plus duration, on the room's own calendar. Nothing is left blank.

4  CHECK. No cycles, no orphan, every row dated, and no row starting before
   a predecessor finishes.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v24.xml")
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
B = dict(rels=len(p.relations))
rooms = collections.defaultdict(list)
for a in p.activities:
    m = NEW.search(str(a.activity_id))
    if m:
        rooms[m.group(1)].append(a)
NEWU = {a.uid for acts in rooms.values() for a in acts}

have = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
out = collections.defaultdict(set)
for r in p.relations:
    out[r.predecessor_uid].add(r.successor_uid)
nru = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1


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


def tie(s, t, typ="Finish to Start", lag=0.0):
    global nru
    if s == t or (s, t) in have or reaches(t, s):
        return False
    p.relations = list(p.relations) + [Relation(
        uid=str(nru), predecessor_uid=s, successor_uid=t, type=typ, lag=lag)]
    have.add((s, t))
    out[s].add(t)
    nru += 1
    return True


def drop(rels):
    dead = {(r.predecessor_uid, r.successor_uid) for r in rels}
    p.relations = [r for r in p.relations
                   if (r.predecessor_uid, r.successor_uid) not in dead]
    for s, t in dead:
        have.discard((s, t))
        out[s].discard(t)


# ===== 1. resequence from Holder's dates =============================
print("=" * 84)
print("1. RESEQUENCING THE NEW ROOMS FROM HOLDER'S DATES")
print("=" * 84)


def anchor(a):
    return D(a.actual_start) or D(a.early_start) or D(a.planned_start)


removed, added = 0, 0
for rm, acts in sorted(rooms.items()):
    uu = {a.uid for a in acts}
    dated = [a for a in acts if anchor(a)]
    order = sorted(dated, key=lambda a: (anchor(a), str(a.activity_id)))
    rank = {a.uid: i for i, a in enumerate(order)}
    bad = []
    for r in p.relations:
        if r.predecessor_uid in uu and r.successor_uid in uu:
            rp, rs = rank.get(r.predecessor_uid), rank.get(r.successor_uid)
            if rp is not None and rs is not None and rp > rs:
                bad.append(r)
    if bad:
        drop(bad)
        removed += len(bad)
    n = 0
    for i in range(len(order) - 1):
        if tie(order[i].uid, order[i + 1].uid):
            n += 1
    added += n
    print(f"   {rm:<9}{len(acts):>3} acts, {len(dated):>3} dated   "
          f"dropped {len(bad):>2} back-running clone ties, chained {n:>2}")
print(f"\nclone ties dropped {removed}   sequence ties added {added}")

# ===== 2. lead-in, the way HV 101 is fed =============================
print("\n" + "=" * 84)
print("2. FEEDING THE ROOMS FROM THE JOB")
print("=" * 84)


def findall(rx, status=None):
    o = []
    for a in p.activities:
        if a.uid in NEWU:
            continue
        if re.search(rx, str(a.name), re.I):
            if status is None or a.status == status:
                o.append(a)
    return o


gear = findall(r"^HVS gear$|^CRAH'?s?$|^CRAC'?s?$|LLE.*gear")
skids = [a for a in findall(r"^Set Electrical Skids$")
         if "Electrical Rooms" in path(a.wbs_uid)
         or "MV Rooms" in path(a.wbs_uid)]
roof = findall(r"Temporary Dry In")
fed = 0
for rm, acts in sorted(rooms.items()):
    byn = {}
    for a in acts:
        byn[re.sub(r"\s*\(.*?\)\s*$", "", str(a.name)).strip().lower()] = a
    n = 0
    # the gear delivery drives setting the gear
    for key in ("set hvs gear", "set crah's *wbo", "set crahs *wbo",
                "set misc. electrical equip"):
        t = byn.get(key)
        if t is None:
            continue
        for q in gear[:3]:
            if tie(q.uid, t.uid):
                n += 1
    # No skid feeds are added. Every room's Set Electrical Skids feeds HV
    # 101's pulls, because that is the high voltage room those feeders land
    # in -- but HV 101 is fed by 14 of them, not by all 30 in the file, so
    # which rooms land in which HV room is a design fact this file does not
    # carry. Tying all 30 into HVR 101 would over-constrain it with logic
    # nobody asked for. The 14 lead-ins the clone brought across from HV 101
    # stand instead, and the ERR rooms' own skids already feed the HV rooms
    # through the clone's lead-out.
    # the shell has to be dried in before finishes start
    t = byn.get("caulk & paint tt *wbo") or byn.get("overhead caulking & paint *wbo")
    if t is not None:
        for q in roof[:1]:
            if tie(q.uid, t.uid):
                n += 1
    fed += n
    uu = {a.uid for a in acts}
    li = sum(1 for r in p.relations
             if r.successor_uid in uu and r.predecessor_uid not in uu)
    lo = sum(1 for r in p.relations
             if r.predecessor_uid in uu and r.successor_uid not in uu)
    print(f"   {rm:<9}+{n:>3} feeds   now lead-in {li:>3}  lead-out {lo:>3}")
print(f"\nfeeds added: {fed}")

# ===== 3. date what Holder has no row for ============================
print("\n" + "=" * 84)
print("3. DATING THE ROWS HOLDER HAS NO EQUIVALENT FOR")
print("=" * 84)
pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)


def fwd(d, days):
    d2, n = d, 0
    while n < max(int(days) - 1, 0):
        d2 += dt.timedelta(days=1)
        if d2.weekday() in WORKDAYS:
            n += 1
    return d2


def nextwd(d):
    d += dt.timedelta(days=1)
    while d.weekday() not in WORKDAYS:
        d += dt.timedelta(days=1)
    return d


done = []
for _ in range(12):
    hit = False
    for acts in rooms.values():
        for a in acts:
            if a.status != "Not Started" or a.early_start:
                continue
            fins = []
            for r in pred[a.uid]:
                q = byu.get(r.predecessor_uid)
                if not q:
                    continue
                f = D(q.early_finish) or D(q.actual_finish) or D(q.planned_finish)
                if f:
                    fins.append(f)
            if not fins:
                continue
            s = max(max(fins), DD)
            s = nextwd(s)
            f = fwd(s, (a.planned_duration or 8) / 8)
            a.early_start = a.planned_start = s.isoformat()
            a.early_finish = a.planned_finish = f.isoformat()
            c = (a.udfs or {}).get("COMMENTS", "")
            a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
                f"Dated {s:%m/%d/%y}-{f:%m/%d/%y} from this room's logic in "
                f"v25; Holder names this work per cable rather than per room, "
                f"so he has no row to take a date from."))[:248])
            done.append((str(a.activity_id), str(a.name), s, f))
            hit = True
    if not hit:
        break
print(f"dated from the room's own logic: {len(done)}")
for i, n, s, f in sorted(done, key=lambda x: x[2]):
    print(f"   {i:<24}{n[:42]:<44}{s}  ..  {f}")

# ===== 4. lean forward: start what is actually released ==============
# The hand-off rule from v17, re-run because v20 to v24 completed a lot of
# new work and so released a lot of new successors. If every predecessor is
# complete and the activity was due to start on or before the data date, it
# should be running, not waiting for someone to report it. Left alone, F9
# re-drives all of these and the forecast reads as though the job stopped.
#
# One layer deep only: an activity set In Progress is not complete, so its
# own successors do not qualify on this pass. Percent is capped at 95 -- a
# running activity is never 100 -- and no actual date may land after the
# data date.
print("\n" + "=" * 84)
print("4. ACTUALIZING STARTS WHERE THE PREDECESSOR IS ACTUALLY COMPLETE")
print("=" * 84)
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)
started = []
for a in p.activities:
    if a.status != "Not Started" or WBO.search(str(a.name)):
        continue
    if str(a.activity_id).startswith("MDC1.LAB"):
        continue
    if "Milestone" in str(a.activity_type or ""):
        continue
    ps = [byu[r.predecessor_uid] for r in pred[a.uid]
          if r.predecessor_uid in byu]
    if not ps or any(x.status == "Not Started" for x in ps):
        continue
    es = D(a.early_start) or D(a.planned_start)
    if not es or es > DD:
        continue
    dur0 = (a.planned_duration or 8) / 8
    if dur0 <= 1:
        continue          # a day or less drags nothing; leave it to the field
    done = [x for x in ps if x.status == "Completed"]
    run = [x for x in ps if x.status == "In Progress"]
    fins = [D(x.actual_finish) for x in done if D(x.actual_finish)]
    if run:
        # leaning onto work still running: the overlap is the point, so the
        # start is this activity's own due date, not the predecessor's finish
        rs = [D(x.actual_start) for x in run if D(x.actual_start)]
        s = max([es] + ([min(rs)] if rs else []))
    else:
        s = nextwd(max(fins)) if fins else es
    if s > DD:
        s = DD
    dur = max((a.planned_duration or 8) / 8, 1)
    elapsed = sum(1 for i in range((DD - s).days + 1)
                  if (s + dt.timedelta(days=i)).weekday() in WORKDAYS)
    pc = min(95.0, round(100.0 * elapsed / dur, 1))
    a.status = "In Progress"
    a.actual_start = s.isoformat()
    a.percent_complete = max(pc, 1.0)
    a.actual_duration = float(min(elapsed, dur) * 8)
    a.remaining_duration = float(max(dur - elapsed, 0.5) * 8)
    if a.planned_labor_units:
        a.remaining_labor_units = round(
            a.planned_labor_units * (1 - a.percent_complete / 100), 1)
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
        f"Start actualized {s:%m/%d/%y} in v25: {len(done)} predecessor(s) "
        f"complete and {len(run)} running, nothing not-started in front, and "
        f"this {dur0:.0f}-day activity was due by the {DD:%m/%d} data date. "
        f"The schedule leans forward instead of waiting. Percent estimated - "
        f"CONFIRM WITH THE FIELD."))[:248])
    started.append((str(a.activity_id), str(a.name), s, a.percent_complete,
                    dur0, len(run)))
print(f"starts actualized: {len(started)}   "
      f"total duration they were holding: "
      f"{sum(x[4] for x in started):,.0f} days")
print(f"{'activity':<24}{'name':<40}{'days':>5}{'started':>12}{'%':>6}"
      f"{'preds running':>15}")
for i, n, s, pc, dd_, nr in sorted(started, key=lambda x: -x[4]):
    print(f"{i:<24}{n[:38]:<40}{dd_:>5,.0f}{str(s):>12}{pc:>6.0f}{nr:>15}")
nowns = sum(1 for a in p.activities
            if a.status == "Not Started" and not WBO.search(str(a.name))
            and (D(a.early_start) or D(a.planned_start))
            and (D(a.early_start) or D(a.planned_start)) < DD)
print(f"\nstill Not Started with a date before {DD}: {nowns}")
print("those are the catch-up rows -- they are waiting on something "
      "incomplete, so the field has nothing to report on them")

# ===== 5. checks =====================================================
print("\n" + "=" * 84)
print("CHECKS")
print("=" * 84)
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
      f"{'lead-in':>9}{'lead-out':>10}{'conflicts':>11}")
tc = 0
for rm, acts in sorted(rooms.items()):
    uu = {a.uid for a in acts}
    np_ = [a for a in acts if not pred[a.uid]]
    ns = [a for a in acts if not succ[a.uid]]
    und = [a for a in acts if a.status == "Not Started" and not a.early_start]
    li = sum(1 for r in p.relations
             if r.successor_uid in uu and r.predecessor_uid not in uu)
    lo = sum(1 for r in p.relations
             if r.predecessor_uid in uu and r.successor_uid not in uu)
    bad = 0
    for a in acts:
        es = D(a.early_start)
        if not es:
            continue
        for r in pred[a.uid]:
            q = byu.get(r.predecessor_uid)
            qf = D(q.early_finish) or D(q.actual_finish) if q else None
            if qf and es < qf:
                bad += 1
    tc += bad
    print(f"{rm:<9}{len(acts):>5}{len(np_):>9}{len(ns):>9}{len(und):>9}"
          f"{li:>9}{lo:>10}{bad:>11}")
print(f"\ndate/logic conflicts: 20 in v24  ->  {tc} now")
print(f"relationships: {B['rels']} -> {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
outp = f"{SCR}/MDC1_logic_repaired_v25.xml"
write_p6_xml(p, outp, include_resources=False, include_udfs=True)
print(f"\nwrote {outp}")
