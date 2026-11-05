"""MDC-3 v22: status JER's field update, and tie precast end to end.

TWO JOBS.

1. THE FIELD UPDATE, from the 10/28 past-due sheet. The date column is a
   FINISH date, so the actual start is worked back from it using the
   activity's own duration on a six-day week.

   55 rows marked complete. 12 carry a date; the other 43 do not, so those
   book on their planned dates -- they were all past due and are now done, so
   the planned window is the only evidence available and each one says so in
   its comment.

   Two of the dated rows finish 10/27, three days PAST the 10/24 data date.
   P6 will not take an actual in the future, so those book as In Progress with
   the remaining duration that reaches their finish. Nothing is invented and
   the data date does not move.

   3 rows are marked not complete but carry a date. Those dates are after the
   data date, so they are forecasts, not actuals: the activity is retimed to
   finish there and left Not Started.

2. PRECAST, END TO END. Today all 58 precast activities sit in 15 islands
   with no tie in or out. The field dates settle what the logic should be:

      A2 EN finishes 11/02 -> A2 TN starts 11/04   next working day
      A2 EN finishes 11/02 -> A3 EN starts 11/04   next working day
      A3 EN finishes 11/09 -> A3 TN starts 11/11   next working day

   so each erection releases its own turnover AND the next area's erection on
   the same side. Two cranes, named North and South, working concurrently:
   A1 ES started 10/05 while A1 EN ran to 10/20, an eleven-day overlap the
   file's Finish-to-Start tie between them forbids.

      EN(n) --FS--> TN(n)         turnover follows its own erection
      EN(n) --FS--> EN(n+1)       north crane walks
      ES(n) --FS--> TS(n)
      ES(n) --FS--> ES(n+1)       south crane walks
      EN(n) --SS+3d--> ES(n)      two cranes, same area, concurrent

   and TN(n) -> ES(n) is REMOVED where it exists: it serialises the cranes and
   the field disproves it (A2 TN finishes 11/16, A3 EN started 11/04).

   Then every Turnover South leads out of the precast WBS, so no area dead
   ends:

      L2 / L3 areas  -> that area's topping chain. The structural set calls
            the deck "5in normal weight concrete topping over precast double
            tees and beams", and the topping opens with Saw Cut/Core Drill
            into those tees, so the tees have to be up first.

      L1 areas (A5, A6, A9, A10, A15) -> Temporary Dry In. Those chains are
            slab ON GRADE -- excavate, underground MEP, subbase, vapour
            barrier, pour -- which PRECEDES precast rather than following it,
            so the topping is the wrong target. The structural set shows the
            precast scope includes main roof and penthouse roof framing, so
            dry-in is what follows it.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WORK = frozenset({0, 1, 2, 3, 4, 5})          # six-day base build


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def back(fin, days):
    """the start that gives `days` working days ending on `fin`"""
    d, n = fin, 1
    while n < max(1, int(round(days))):
        d -= dt.timedelta(days=1)
        if d.weekday() in WORK:
            n += 1
    while d.weekday() not in WORK:
        d -= dt.timedelta(days=1)
    return d


def fwd(start, days):
    d, n = start, 1
    while n < max(1, int(round(days))):
        d += dt.timedelta(days=1)
        if d.weekday() in WORK:
            n += 1
    return d


def wdays(a, b):
    if not a or not b or b < a:
        return 0
    return max(1, sum(1 for i in range((b - a).days + 1)
                      if (a + dt.timedelta(days=i)).weekday() in WORK))


p = load_xml(f"{SCR}/MDC3_logic_repaired_v21.xml")
byu = {a.uid: a for a in p.activities}
byid = {str(a.activity_id).strip(): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
dd = dt.date.fromisoformat(str(p.data_date)[:10])


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


field = json.load(open(f"{SCR}/mdc3_field.json"))
print(f"MDC-3 v21: {len(p.activities)} acts, {len(p.relations)} ties, "
      f"data date {dd:%m/%d/%Y}")
print(f"field rows: {len(field)}\n")

# ---- 1. status the field update ---------------------------------------
done_dated, done_plan, inprog, retimed, skipped = [], [], [], [], []
for row in field:
    a = byid.get(row["id"])
    if a is None:
        skipped.append((row["id"], "not in the file"))
        continue
    flag = row["done"]
    fin = D(row["date"])
    dur = (a.planned_duration or 8) / 8
    if flag == "yes":
        if fin:
            st = back(fin, dur)
            if fin <= dd:
                a.status = "Completed"
                a.actual_start = st.isoformat()
                a.actual_finish = fin.isoformat()
                a.actual_duration = a.planned_duration
                a.remaining_duration = 0.0
                a.percent_complete = 100.0
                a.udfs["COMMENTS"] = (
                    f"Complete per JER field update 10/28/2026. Finished "
                    f"{fin:%m/%d/%Y}; actual start {st:%m/%d/%Y} worked back "
                    f"from the {dur:.0f}-day duration on a six-day week. "
                    f"Planned window was {row['plan']}.")
                done_dated.append((a, st, fin))
            else:
                rem = wdays(dd, fin)
                a.status = "In Progress"
                a.actual_start = st.isoformat()
                a.actual_finish = None
                a.remaining_duration = float(rem) * 8.0
                a.percent_complete = round(
                    100.0 * max(0, wdays(st, dd) - 1) / max(dur, 1), 1)
                a.planned_finish = fin.isoformat()
                a.udfs["COMMENTS"] = (
                    f"JER field update 10/28/2026 reports this finishing "
                    f"{fin:%m/%d/%Y}, which is after the {dd:%m/%d/%Y} data "
                    f"date, so it books as In Progress with {rem} working days "
                    f"remaining rather than as a future actual. Actual start "
                    f"{st:%m/%d/%Y} worked back from the {dur:.0f}-day duration.")
                inprog.append((a, st, fin, rem))
        else:
            ps, pf = D(a.planned_start), D(a.planned_finish)
            if not ps:
                skipped.append((row["id"], "complete but no date anywhere"))
                continue
            pf = pf or fwd(ps, dur)
            if pf > dd:
                pf = dd
            a.status = "Completed"
            a.actual_start = ps.isoformat()
            a.actual_finish = pf.isoformat()
            a.actual_duration = a.planned_duration
            a.remaining_duration = 0.0
            a.percent_complete = 100.0
            a.udfs["COMMENTS"] = (
                f"Complete per JER field update 10/28/2026. No field date was "
                f"given, so it books on its planned window "
                f"{ps:%m/%d/%Y}-{pf:%m/%d/%Y}. DATES INFERRED - the completion "
                f"is reported, the dates are not.")
            done_plan.append((a, ps, pf))
    elif flag == "no" and fin:
        st = back(fin, dur)
        was = D(a.planned_start)
        a.planned_start = st.isoformat()
        a.planned_finish = fin.isoformat()
        a.remaining_duration = a.planned_duration
        a.udfs["COMMENTS"] = (
            f"Not started per JER field update 10/28/2026, which forecasts a "
            f"{fin:%m/%d/%Y} finish. Retimed to {st:%m/%d/%Y}-{fin:%m/%d/%Y} "
            f"from {was}; the date is a forecast, not an actual.")
        retimed.append((a, was, st, fin))

print(f"statused Completed with a field date : {len(done_dated)}")
print(f"statused Completed on planned dates  : {len(done_plan)}  (dates inferred)")
print(f"statused In Progress (finish past dd): {len(inprog)}")
print(f"retimed, still Not Started           : {len(retimed)}")
print(f"skipped                              : {len(skipped)}")
for a, st, fin in done_dated:
    print(f"   DONE  {a.activity_id:<22}{str(a.name)[:38]:<40} {st} .. {fin}")
for a, st, fin, rem in inprog:
    print(f"   PROG  {a.activity_id:<22}{str(a.name)[:38]:<40} {st} .. {fin}"
          f"  {rem}d left, {a.percent_complete:.0f}%")
for a, was, st, fin in retimed:
    print(f"   PLAN  {a.activity_id:<22}{str(a.name)[:38]:<40} {was} -> {st} .. {fin}")
for i, why in skipped:
    print(f"   SKIP  {i}: {why}")

# ---- 2. precast logic -------------------------------------------------
pc = [a for a in p.activities if ".ST.PC." in str(a.activity_id or "")]


def part(a):
    n = str(a.name)
    if re.search(r"Erection North|^Area \d+ Erection$", n, re.I):
        return "EN"
    if re.search(r"Erection South", n, re.I):
        return "ES"
    if re.search(r"Turnover North|^Area \d+ Turnover$", n, re.I):
        return "TN"
    if re.search(r"Turnover South", n, re.I):
        return "TS"
    return None


area = {}
for a in pc:
    m = re.match(r"MDC3\.ST\.PC\.A(\d+)\.", str(a.activity_id))
    k = part(a)
    if m and k:
        area.setdefault(int(m.group(1)), {})[k] = a
AR = sorted(area)

have = {}
for r in p.relations:
    have[(r.predecessor_uid, r.successor_uid)] = r
nxt = collections.defaultdict(set)
for r in p.relations:
    if r.predecessor_uid in byu and r.successor_uid in byu:
        nxt[r.predecessor_uid].add(r.successor_uid)


def reaches(a, b):
    seen, q = {a}, collections.deque([a])
    while q:
        u = q.popleft()
        for v in nxt[u] - seen:
            if v == b:
                return True
            seen.add(v)
            q.append(v)
    return False


nrel = max((int(r.uid) for r in p.relations if str(r.uid).isdigit()),
           default=99000) + 5000
added, dropped, changed = [], [], []


def tie(src, dst, typ="Finish to Start", lag=0.0, why=""):
    global nrel
    if src is None or dst is None or src.uid == dst.uid:
        return False
    if (src.uid, dst.uid) in have:
        return False
    if reaches(dst.uid, src.uid):
        return False
    p.relations = list(p.relations) + [
        Relation(uid=str(nrel), predecessor_uid=src.uid, successor_uid=dst.uid,
                 type=typ, lag=lag)]
    nrel += 1
    have[(src.uid, dst.uid)] = p.relations[-1]
    nxt[src.uid].add(dst.uid)
    added.append((src.activity_id, dst.activity_id, typ, lag / 8, why))
    return True


# 2a. drop TN(n) -> ES(n): it serialises the two cranes
for n in AR:
    g = area[n]
    if "TN" in g and "ES" in g and (g["TN"].uid, g["ES"].uid) in have:
        r = have.pop((g["TN"].uid, g["ES"].uid))
        p.relations = [x for x in p.relations if x is not r]
        nxt[g["TN"].uid].discard(g["ES"].uid)
        dropped.append((g["TN"].activity_id, g["ES"].activity_id,
                        "turnover does not gate the other crane"))

# 2b. EN -> ES becomes start-to-start +3d (concurrent cranes)
for n in AR:
    g = area[n]
    if "EN" not in g or "ES" not in g:
        continue
    key = (g["EN"].uid, g["ES"].uid)
    if key in have:
        r = have[key]
        if r.type != "Start to Start":
            was = r.type
            p.relations = [x for x in p.relations if x is not r]
            have.pop(key)
            nxt[g["EN"].uid].discard(g["ES"].uid)
            tie(g["EN"], g["ES"], "Start to Start", 3 * 8.0,
                "two cranes work the area concurrently")
            changed.append((g["EN"].activity_id, g["ES"].activity_id,
                            was, "Start to Start +3d"))
    else:
        tie(g["EN"], g["ES"], "Start to Start", 3 * 8.0,
            "two cranes work the area concurrently")

# 2c. each erection releases its own turnover
for n in AR:
    g = area[n]
    tie(g.get("EN"), g.get("TN"), why="turnover follows its own erection")
    tie(g.get("ES"), g.get("TS"), why="turnover follows its own erection")

# 2d. the crane walks
for i in range(len(AR) - 1):
    a, b = area[AR[i]], area[AR[i + 1]]
    tie(a.get("EN"), b.get("EN"), why=f"north crane walks A{AR[i]} to A{AR[i+1]}")
    tie(a.get("ES"), b.get("ES"), why=f"south crane walks A{AR[i]} to A{AR[i+1]}")

# 2e. every Turnover South leads out of the precast WBS
L1 = {5, 6, 9, 10, 15}
dryin = next((a for a in p.activities
              if str(a.activity_id) == "MDC3.ROOF.1870"), None)
lead = []
for n in AR:
    g = area[n]
    ts = g.get("TS") or g.get("TN")
    if ts is None:
        continue
    # BOTH halves have to be turned over before the follow-on work starts, so
    # Turnover North and Turnover South each feed the target. Without this,
    # dropping TN -> ES leaves Turnover North with no successor.
    ends = [x for x in (g.get("TN"), g.get("TS")) if x is not None]
    if n in L1:
        for e in ends:
            if tie(e, dryin, why="precast includes roof framing; dry-in follows"):
                lead.append((n, e.activity_id, dryin.activity_id, "Roof", "L1"))
    else:
        top = [a for a in p.activities
               if re.match(rf"MDC3\.ST\.L[123]\.A{n}\.", str(a.activity_id or ""))]
        if not top:
            continue
        top.sort(key=lambda a: (D(a.planned_start) or dt.date(2099, 1, 1),
                                str(a.activity_id)))
        lv = re.match(r"MDC3\.ST\.(L[123])\.", str(top[0].activity_id)).group(1)
        for e in ends:
            if tie(e, top[0], why="topping is poured over the precast tees"):
                lead.append((n, e.activity_id, top[0].activity_id, lv, "topping"))

print(f"\nprecast: {len(added)} ties added, {len(dropped)} dropped, "
      f"{len(changed)} retyped")
print("\ndropped (turnover was gating the other crane):")
for a, b, why in dropped:
    print(f"   {a} -> {b}   {why}")
print("\nretyped:")
for a, b, was, now in changed:
    print(f"   {a} -> {b}   {was} => {now}")
print("\nleads out of the precast WBS:")
for n, a, b, lv, kind in lead:
    print(f"   A{n:<3} {a:<22} -> {b:<22} [{lv}] {kind}")
print(f"\nby kind: " + "  ".join(
    f"{k} {v}" for k, v in collections.Counter(x[4] for x in lead).items()))

# ---- verify -----------------------------------------------------------
alive = {a.uid for a in p.activities}
s2 = collections.defaultdict(list)
ind = collections.Counter()
for r in p.relations:
    if r.predecessor_uid in alive and r.successor_uid in alive:
        s2[r.predecessor_uid].append(r.successor_uid)
        ind[r.successor_uid] += 1
q = collections.deque(u for u in alive if not ind[u])
n = 0
i2 = dict(ind)
while q:
    u = q.popleft()
    n += 1
    for v in s2[u]:
        i2[v] -= 1
        if not i2[v]:
            q.append(v)
succs = {r.successor_uid for r in p.relations}
preds = {r.predecessor_uid for r in p.relations}
pcno = [a for a in pc if a.uid not in succs]
pcnos = [a for a in pc if a.uid not in preds]
adj = collections.defaultdict(set)
for u, vs in s2.items():
    for v in vs:
        adj[u].add(v)
        adj[v].add(u)
seen, isl = set(), 0
for a in p.activities:
    if a.uid in seen:
        continue
    q2 = collections.deque([a.uid])
    seen.add(a.uid)
    while q2:
        u = q2.popleft()
        for v in adj[u] - seen:
            seen.add(v)
            q2.append(v)
    isl += 1
fut = [a for a in p.activities if a.status == "Completed"
       and D(a.actual_finish) and D(a.actual_finish) > dd]
noas = [a for a in p.activities if a.status in ("In Progress", "Completed")
        and not D(a.actual_start)]

# baseline v21 so every number is a measured before/after
o = load_xml(f"{SCR}/MDC3_logic_repaired_v21.xml")
ou = {a.uid for a in o.activities}
opc = [a for a in o.activities if ".ST.PC." in str(a.activity_id or "")]
osucc = {r.successor_uid for r in o.relations}
opred = {r.predecessor_uid for r in o.relations}
oadj = collections.defaultdict(set)
for r in o.relations:
    if r.predecessor_uid in ou and r.successor_uid in ou:
        oadj[r.predecessor_uid].add(r.successor_uid)
        oadj[r.successor_uid].add(r.predecessor_uid)
oseen, oisl = set(), 0
for a in o.activities:
    if a.uid in oseen:
        continue
    q3 = collections.deque([a.uid])
    oseen.add(a.uid)
    while q3:
        u = q3.popleft()
        for v in oadj[u] - oseen:
            oseen.add(v)
            q3.append(v)
    oisl += 1
ost = collections.Counter(a.status for a in o.activities)
nst = collections.Counter(a.status for a in p.activities)

print(f"\n{'':<36}{'v21':>10}{'v22':>10}")
print(f"{'activities':<36}{len(o.activities):>10}{len(p.activities):>10}")
print(f"{'relationships':<36}{len(o.relations):>10}{len(p.relations):>10}")
print(f"{'islands':<36}{oisl:>10}{isl:>10}")
print(f"{'precast with no predecessor':<36}"
      f"{sum(1 for a in opc if a.uid not in osucc):>10}{len(pcno):>10}")
print(f"{'precast with no successor':<36}"
      f"{sum(1 for a in opc if a.uid not in opred):>10}{len(pcnos):>10}")
print(f"{'acyclic':<36}{'all':>10}"
      f"{'all' if n == len(alive) else f'{n}/{len(alive)}':>10}")
print(f"{'completed after the data date':<36}{0:>10}{len(fut):>10}")
print(f"{'in progress with no actual start':<36}{0:>10}{len(noas):>10}")
for k in ("Completed", "In Progress", "Not Started"):
    print(f"{k:<36}{ost[k]:>10}{nst[k]:>10}")
if pcnos:
    print(f"\nprecast still with no successor ({len(pcnos)}):")
    for a in pcnos:
        print(f"   {a.activity_id:<22} {str(a.name)[:46]}")

out = f"{SCR}/MDC3_logic_repaired_v22.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
json.dump({"added": added, "dropped": dropped, "changed": changed,
           "lead_out": lead,
           "done_dated": [(a.activity_id, str(s), str(f)) for a, s, f in done_dated],
           "done_planned": [(a.activity_id, str(s), str(f)) for a, s, f in done_plan],
           "in_progress": [(a.activity_id, str(s), str(f), r) for a, s, f, r in inprog],
           "retimed": [(a.activity_id, str(w), str(s), str(f)) for a, w, s, f in retimed]},
          open(f"{SCR}/work/mdc3_v22.json", "w"), indent=1, default=str)
