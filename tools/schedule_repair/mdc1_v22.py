"""MDC-1 v22: merge the four duplicated rooms away.

ER 201, ER 202, ER 212 and HV 101 each exist twice, because the WBS folder
exists twice -- "ER 201" alongside "ER R201", "HV 101" alongside "HV R101",
and ER 212 simply twice under the same name. Each pair carries a full
backbone, so the room's scope is counted twice: 89 surplus activities holding
6,635 budgeted hours.

WHICH COPY SURVIVES, decided on evidence rather than on which came first:

  ER 202   Holder puts ER 202 in Phase 2. Our "ER R202" sits in Phase 1, so
           it is a duplicate AND misfiled. The Phase 2 folder survives.
  ER 201   Both sit in Phase 2, which agrees with Holder, so the name
           decides: Holder writes "ER 201", not "ER R201". The folder that
           matches also carries the progress -- 8 complete and 1 running
           against 3 complete in the other.
  HV 101   Same: Holder writes "HV 101". Neither copy has started.
  ER 212   Both folders carry the same name in the same phase, so neither
           name nor phase can decide it. The loadings can: one copy carries
           a flat 62 hours on all 24 activities, which is a placeholder, not
           an estimate. The other varies by work type and matches the peer
           rooms on every one of the eight that differ. That one survives.

Across all 22 pairs that disagree on hours, the surviving family lands closer
to the median of the 11-to-26 undoubled electrical and HV rooms 16 times
against 6, which is the check that this is the right way round.

NOTHING IS SIMPLY DELETED. Each dropped row is merged into its twin first:

  status   the more advanced of the two wins, with its actual dates. Eight
           pairs disagree, and six of those have the dropped copy sitting at
           Not Started while the survivor is complete -- but two run the
           other way, so taking the survivor's status blindly would throw
           away real progress.
  hours    per activity, whichever figure is closer to the peer-room median.
           The surviving ROOM is chosen once; the hours are judged one
           activity at a time, so a good number is not lost because it
           happens to sit on the row being dropped.
  logic    every relationship touching a dropped row is re-pointed at its
           twin, then de-duplicated. Self-loops and anything that would
           close a cycle are dropped, and nothing else is: the standing rule
           is that logic already set does not get removed, so this moves it
           rather than deleting it.

A dropped row with no twin in the surviving folder is not deleted either --
it is re-parented into that folder, because an unmatched name is scope, not
a duplicate. Finally the emptied folders go.
"""
import collections
import datetime as dt
import re
import statistics
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")

# folder uid -> the folder uid it merges into
MERGE = {"26132": "26187",   # ER R201  -> ER 201   (Holder's name)
         "26133": "26181",   # ER R202  -> ER 202   (Holder's phase)
         "26182": "26110",   # ER 212   -> ER 212   (real loadings beat 62h flat)
         "26138": "26123"}   # HV R101  -> HV 101   (Holder's name)
RANK = {"Completed": 2, "In Progress": 1, "Not Started": 0}

p = load_xml(f"{SCR}/MDC1_logic_repaired_v21.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


# ---- peer medians, from rooms that are NOT doubled -------------------
touched = set(MERGE) | set(MERGE.values())
peer = collections.defaultdict(list)
for a in p.activities:
    if a.wbs_uid in touched:
        continue
    if not re.search(r"Electrical Rooms|HV Rooms", path(a.wbs_uid)):
        continue
    h = a.planned_labor_units or 0
    if h:
        peer[norm(a.name)].append(h)
med = {k: statistics.median(v) for k, v in peer.items() if len(v) >= 3}
print(f"peer medians available for {len(med)} activity names "
      f"(from undoubled electrical and HV rooms)")

B = dict(acts=len(p.activities), rels=len(p.relations),
         bud=sum(a.planned_labor_units or 0 for a in p.activities),
         done=sum(1 for a in p.activities if a.status == "Completed"))

# ---- relationship index ---------------------------------------------
have = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
out = collections.defaultdict(set)
for r in p.relations:
    out[r.predecessor_uid].add(r.successor_uid)


def reaches(s, t, cap=200000):
    """is t already downstream of s?"""
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


# ---- merge -----------------------------------------------------------
print("\n" + "=" * 80)
print("MERGING")
print("=" * 80)
nuid = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
drop, moved = [], []
stats = collections.Counter()
hourlog, statlog = [], []

for dfold, kfold in MERGE.items():
    ds = [a for a in p.activities if a.wbs_uid == dfold]
    ks = {norm(a.name): a for a in p.activities if a.wbs_uid == kfold}
    print(f"\n{path(dfold)}")
    print(f"   -> {path(kfold)}")
    print(f"   {len(ds)} rows to merge into {len(ks)}")
    for d in ds:
        k = ks.get(norm(d.name))
        if k is None:
            d.wbs_uid = kfold
            moved.append(d)
            stats["re-parented, no twin"] += 1
            continue
        # --- status: the more advanced wins, with its actual dates
        if RANK.get(d.status, 0) > RANK.get(k.status, 0):
            statlog.append((str(k.activity_id), k.status, d.status,
                            str(d.activity_id)))
            k.status = d.status
            k.actual_start = d.actual_start
            k.actual_finish = d.actual_finish
            k.actual_duration = d.actual_duration
            k.percent_complete = d.percent_complete
            k.remaining_duration = d.remaining_duration
            stats["status taken from the dropped copy"] += 1
        # --- hours: whichever is closer to the peer median
        hd, hk = d.planned_labor_units or 0, k.planned_labor_units or 0
        m = med.get(norm(d.name))
        if m is not None and abs(hd - hk) > 1 and abs(hd - m) < abs(hk - m):
            hourlog.append((str(k.activity_id), hk, hd, m))
            k.planned_labor_units = hd
            if k.status == "Completed":
                k.remaining_labor_units = 0.0
            elif k.status == "Not Started":
                k.remaining_labor_units = float(hd)
            stats["hours taken from the dropped copy"] += 1
        # --- logic: re-point every tie onto the twin
        for r in list(p.relations):
            if r.predecessor_uid == d.uid or r.successor_uid == d.uid:
                s = k.uid if r.predecessor_uid == d.uid else r.predecessor_uid
                t = k.uid if r.successor_uid == d.uid else r.successor_uid
                if s == t:
                    stats["tie dropped: self-loop onto the twin"] += 1
                    continue
                if (s, t) in have:
                    stats["tie dropped: twin already has it"] += 1
                    continue
                if reaches(t, s):
                    stats["tie dropped: would close a cycle"] += 1
                    continue
                p.relations = list(p.relations) + [Relation(
                    uid=str(nuid), predecessor_uid=s, successor_uid=t,
                    type=r.type, lag=r.lag)]
                have.add((s, t))
                out[s].add(t)
                nuid += 1
                stats["tie re-pointed onto the twin"] += 1
        c = (k.udfs or {}).get("COMMENTS", "")
        k.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
            f"Duplicate {d.activity_id} merged into this row in v22: "
            f"{path(dfold).split(' / ')[-1]} duplicated "
            f"{path(kfold).split(' / ')[-1]}, counting the room twice. Its "
            f"logic was re-pointed here."))[:248])
        drop.append(d)

print("\n" + "-" * 80)
for k, v in stats.most_common():
    print(f"   {v:>5}  {k}")

if statlog:
    print(f"\nstatus carried across from a dropped row ({len(statlog)}):")
    for i, was, now, src in statlog:
        print(f"   {i:<24}{was:<13}-> {now:<13}(from {src})")
if hourlog:
    print(f"\nhours taken from the dropped row, being closer to peers "
          f"({len(hourlog)}):")
    for i, was, now, m in hourlog:
        print(f"   {i:<24}{was:>7,.0f}h -> {now:>7,.0f}h   peer median "
              f"{m:,.0f}h")
if moved:
    print(f"\nre-parented, no twin to merge into ({len(moved)}):")
    for a in moved:
        print(f"   {str(a.activity_id):<24}{str(a.name)[:48]}")

# ---- delete ----------------------------------------------------------
dead = {a.uid for a in drop}
p.activities = [a for a in p.activities if a.uid not in dead]
p.relations = [r for r in p.relations
               if r.predecessor_uid not in dead and r.successor_uid not in dead]
still = {a.wbs_uid for a in p.activities}
empt = [u for u in MERGE if u not in still]
p.wbs_nodes = [w for w in p.wbs_nodes if w.uid not in empt]
print(f"\nactivities deleted: {len(dead)}   empty folders removed: {len(empt)}")
for u in empt:
    print(f"   {u}")

# ---- verify ----------------------------------------------------------
print("\n" + "=" * 80)
print("CHECKS")
print("=" * 80)
live = {a.uid for a in p.activities}
orph = [r for r in p.relations
        if r.predecessor_uid not in live or r.successor_uid not in live]
print(f"relationships pointing at a deleted activity: {len(orph)}")
o2 = collections.defaultdict(set)
for r in p.relations:
    o2[r.predecessor_uid].add(r.successor_uid)
WHITE, GREY, BLACK = 0, 1, 2
col = collections.defaultdict(int)
cyc = 0
for s in list(live):
    if col[s] != WHITE:
        continue
    st = [(s, iter(o2[s]))]
    col[s] = GREY
    while st:
        n, it = st[-1]
        adv = False
        for v in it:
            if col[v] == GREY:
                cyc += 1
            elif col[v] == WHITE:
                col[v] = GREY
                st.append((v, iter(o2[v])))
                adv = True
                break
        if not adv:
            col[n] = BLACK
            st.pop()
print(f"cycles: {cyc}")
dupf = 0
for w in p.wbs_nodes:
    m = re.match(r"^(ERR|HVR|ER|MV|HV|GEN|DH)\s*R?\s*(\d{3})$",
                 str(w.name).strip().upper())
    if m:
        dupf += 1
kk = collections.Counter()
for w in p.wbs_nodes:
    m = re.match(r"^(ERR|HVR|ER|MV|HV|GEN|DH|GL|GAL|CUP)\s*R?\s*(\d{3})$",
                 str(w.name).strip().upper())
    if m:
        kk[f"{m.group(1)}{m.group(2)}"] += 1
print(f"room folders still appearing twice: "
      f"{sum(1 for v in kk.values() if v > 1)}")
cnt = collections.Counter(a.wbs_uid for a in p.activities)
RX = re.compile(r"\b(ERR|HVR|ER|MV|HV|GEN|DH)[\s\.\-_]*R?[\s\.\-_]*(\d{3})\b",
                re.I)
g = collections.defaultdict(list)
for a in p.activities:
    m = RX.search(f"{a.activity_id} {a.name} {path(a.wbs_uid)}")
    if m:
        g[(f"{m.group(1).upper()}{m.group(2)}", norm(a.name))].append(a)
print(f"(room, name) pairs still duplicated: "
      f"{sum(1 for v in g.values() if len(v) > 1)}")

A = dict(acts=len(p.activities), rels=len(p.relations),
         bud=sum(a.planned_labor_units or 0 for a in p.activities),
         done=sum(1 for a in p.activities if a.status == "Completed"))
print("\n" + "=" * 80)
print(f"{'':<22}{'v21':>12}{'v22':>12}{'change':>11}")
for k, lbl in (("acts", "activities"), ("rels", "relationships"),
               ("bud", "budget hours"), ("done", "completed")):
    print(f"{lbl:<22}{B[k]:>12,.0f}{A[k]:>12,.0f}{A[k] - B[k]:>+11,.0f}")
print(f"\nlongest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
outp = f"{SCR}/MDC1_logic_repaired_v22.xml"
write_p6_xml(p, outp, include_resources=False, include_udfs=True)
print(f"\nwrote {outp}")
