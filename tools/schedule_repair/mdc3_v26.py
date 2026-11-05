"""MDC-3 v26: reconcile to Holder's 11/04 export.

Rebuilt from v22. Two earlier attempts are worth naming because the fixes are
the point.

v23 added our own 16-activity ER-room backbone to the three missing
rooms. Holder's rooms already contain that backbone -- Overhead Rough In Fire
Protection, Install Drywall Framing, Wall Rough In's, Set Electrical Skids,
Set CRAH's, Testing and Inspections, Start ups of Equipment, all of it. Our
pattern and theirs are the same pattern because both came off the same
contract. Adding ours on top duplicated their activities and collided with
their activity ids. So the rooms come in whole, on Holder's ids.

Four passes:

1. ACTUALS. 26 activities Holder reports finished that we carried as Not
   Started or In Progress take Holder's actual dates. Five run the other way
   -- we say Completed, Holder says still in progress -- and those are NOT
   reversed, because our side came from JER's own field update. They are
   flagged in the activity comment instead.

2. THE THREE MISSING ROOMS: ER 210, ERR 202, ELEC 202. Holder's activities on
   Holder's ids, under WBS folders named by room number, with the non-JER
   scope marked **WBO the way the rest of this file marks it. Nothing
   invented: 200 activities, all traceable to a row in their export.

3. THE TAG GATE. Holder runs a four-stage verification on 194 pieces of
   equipment -- Red (JER), Pink (Holder), Orange (Cloud HQ), Yellow (ready to
   energize). 776 activities we did not carry at all, and nothing energizes
   until all four clear.

v24 then imported Holder's relationships with a cycle guard but no redundancy
guard, and pushed relationship redundancy from 15% to 33% -- MDC-1 sits at
17%. Long-range ties that merely skip a chain we already carry came in
alongside the real logic, one of them reading FS+208d because the "lag" was
really the intermediate chain. v25 adds the redundancy guard and processes
candidates shortest-lag-first, so the tight real tie lands before the
long-range one that would have displaced it.

4. HOLDER'S LOGIC. Every Holder relationship whose two ends both now exist
   here, that we do not already carry, and that the existing network does not
   already imply. Their export has no relationship type or lag column, so
   both are inferred from their own dates: for each pair the implied lag is
   computed for FS, SS, FF and SF, and the type whose lag is smallest wins.
   Each candidate is cycle-checked and redundancy-checked before it goes in.

Nothing existing is deleted. The user's standing rule is that logic already
set in P6 stays, so this run only adds -- no relationship is removed, and no
status is downgraded.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Activity, Relation, WBSNode
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WORK = frozenset({0, 1, 2, 3, 4, 5})
SRC = "Holder MDC-3 schedule export 11/04/2026"


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def wdays(a, b):
    """working days from a to b inclusive; 0 if unordered"""
    if not a or not b or b < a:
        return 0
    return max(1, sum(1 for i in range((b - a).days + 1)
                      if (a + dt.timedelta(days=i)).weekday() in WORK))


def off(a, b):
    """signed working-day step count from a to b (0 if same day)"""
    if not a or not b:
        return None
    if a == b:
        return 0
    s = 1 if b > a else -1
    lo, hi = (a, b) if b > a else (b, a)
    n = sum(1 for i in range((hi - lo).days)
            if (lo + dt.timedelta(days=i + 1)).weekday() in WORK)
    return s * n


H = json.load(open(f"{SCR}/work/holder_mdc3.json"))
hact = {a["id"]: a for a in H["acts"]}
hpairs = [(a, b) for a, b, t, l in H["pairs"]]

p = load_xml(f"{SCR}/MDC3_logic_repaired_v22.xml")
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
dd = dt.date.fromisoformat(str(p.data_date)[:10])
CAL = collections.Counter(a.calendar_uid for a in p.activities).most_common(1)[0][0]
V22A, V22R = len(p.activities), len(p.relations)


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


nuid = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
nwbs = max(int(w.uid) for w in p.wbs_nodes if str(w.uid).isdigit()) + 1
nrel = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1

rels = list(p.relations)
have = {(r.predecessor_uid, r.successor_uid) for r in rels}
nxt = collections.defaultdict(set)
prv = collections.defaultdict(set)
for r in rels:
    nxt[r.predecessor_uid].add(r.successor_uid)
    prv[r.successor_uid].add(r.predecessor_uid)


def reaches(a, b):
    """true if a already reaches b, so a -> b adds nothing / b -> a cycles"""
    if a == b:
        return True
    seen, q = {a}, collections.deque([a])
    while q:
        u = q.popleft()
        for v in nxt[u]:
            if v == b:
                return True
            if v not in seen:
                seen.add(v)
                q.append(v)
    return False


def tie(src, dst, typ="Finish to Start", lag=0.0, guard=True):
    """guard=True also rejects a tie the existing logic already implies.

    Without that guard this run pushed relationship redundancy from 15% to
    33%; MDC-1 sits at 17%. A tie that only restates a path already in the
    network adds no constraint and hides the real driver, and the long-range
    ones carry absurd inferred lags (one read FS+208d) because the gap is
    actually the intermediate chain nobody skipped.
    """
    global nrel
    if src is None or dst is None or src.uid == dst.uid:
        return False
    if (src.uid, dst.uid) in have:
        return False
    if reaches(dst.uid, src.uid):
        return False
    if guard and reaches(src.uid, dst.uid):
        return False
    rels.append(Relation(uid=str(nrel), predecessor_uid=src.uid,
                         successor_uid=dst.uid, type=typ, lag=lag))
    nrel += 1
    have.add((src.uid, dst.uid))
    nxt[src.uid].add(dst.uid)
    prv[dst.uid].add(src.uid)
    return True


# ===== 1. ACTUALS ======================================================
print("=" * 78)
print("1. ACTUALS RECONCILED TO HOLDER")
print("=" * 78)
adopted, conflict = [], []
for i, h in sorted(hact.items()):
    a = ours.get(i)
    if a is None or not h["finished"] or a.status == "Completed":
        continue
    hf, hs = D(h["finish"]), D(h["start"])
    if not hf:
        continue
    was, st = a.status, hs or hf
    if hf <= dd:
        a.status = "Completed"
        a.actual_start, a.actual_finish = st.isoformat(), hf.isoformat()
        a.actual_duration = a.planned_duration
        a.remaining_duration, a.percent_complete = 0.0, 100.0
    else:
        a.status = "In Progress"
        a.actual_start, a.actual_finish = st.isoformat(), None
        a.remaining_duration = float(wdays(dd, hf)) * 8.0
        a.planned_finish = hf.isoformat()
    a.udfs["COMMENTS"] = (
        f"Statused from the {SRC}, which reports actual {st} to {hf}. "
        f"Our file carried this as {was}.")
    adopted.append((i, str(a.name)[:38], was, a.status, st, hf))
for i, h in sorted(hact.items()):
    a = ours.get(i)
    if a is None or a.status != "Completed":
        continue
    if h["started"] and not h["finished"]:
        a.udfs["COMMENTS"] = (
            (a.udfs.get("COMMENTS", "") + "  ") if a.udfs.get("COMMENTS") else ""
        ) + (f"DISAGREEMENT: the {SRC} still shows this In Progress, started "
             f"{h['start']}. Kept Completed because our status came from JER's "
             f"own field update, which is closer to the work. Confirm with "
             f"Holder.")
        conflict.append((i, str(a.name)[:44], h["start"]))
print(f"adopted Holder's actuals on {len(adopted)} activities "
      f"({sum(1 for x in adopted if x[3] == 'Completed')} to Completed, "
      f"{sum(1 for x in adopted if x[3] == 'In Progress')} to In Progress)")
for i, nm, was, now, s, f in sorted(adopted, key=lambda x: str(x[5])):
    print(f"   {i:<22}{nm:<40}{was:<12}-> {now:<12}{s} .. {f}")
print(f"\n{len(conflict)} kept as ours where Holder still says In Progress:")
for i, nm, s in conflict:
    print(f"   {i:<22}{nm:<46}Holder started {s}")

# ===== 2. THE THREE MISSING ROOMS ======================================
print("\n" + "=" * 78)
print("2. ER 210, ERR 202, ELEC 202 -- Holder's activities, Holder's ids")
print("=" * 78)
ROOMS = [("ER 210", "MDC3.PH1.ER.210."), ("ERR 202", "MDC3.PH1.ERR202."),
         ("ELEC 202", "MDC3.PH1.ELC202.")]
# non-JER scope, tested first so "Rough In Mechanical" does not fall through
# to the electrical default
WBO_RX = re.compile(
    r"Fire Protection|Rough In Mechanical|Mechanical Final|Install Drywall|"
    r"Hang Drywall|Close-in Wall|Tape\s*/?\s*Finish|Hang Tape|Prime/First|"
    r"\bPaint\b|Floor Finish|Partition|Door Frame|County|Wall Inspection|"
    r"Holder Building Control|MEP Penetration|MEP In-wall|Install MEP Overhead|"
    r"MEP Overhead Supports|Set CRAH", re.I)
parent = next(w for w in p.wbs_nodes
              if w.name == "Electrical Rooms - Phase 1" and "Phase 1" in path(w.uid))
roomrep, roomids = [], {}
for label, pfx in ROOMS:
    node = WBSNode(uid=str(nwbs), name=label, code=label.replace(" ", ""),
                   parent_uid=parent.uid, sequence_num=900, id_prefix=pfx)
    nwbs += 1
    p.wbs_nodes = list(p.wbs_nodes) + [node]
    wbs[node.uid] = node
    hset = {i: a for i, a in hact.items() if i.startswith(pfx)}
    nw = 0
    for i in sorted(hset):
        h = hset[i]
        is_wbo = bool(WBO_RX.search(h["name"]))
        nm = f"{h['name']} **WBO" if is_wbo else h["name"]
        dur = float(h["dur"] or 1)
        a = Activity(
            uid=str(nuid), activity_id=i, name=nm, wbs_uid=node.uid,
            calendar_uid=CAL, activity_type="Task Dependent",
            status="Not Started", planned_duration=dur * 8.0,
            remaining_duration=dur * 8.0, actual_duration=0.0,
            percent_complete=0.0, planned_start=str(h["start"])[:10],
            planned_finish=str(h["finish"])[:10])
        a.udfs["COMMENTS"] = (
            f"{label} was missing from our file entirely. Imported from the "
            f"{SRC} on Holder's own activity id so this room stays "
            f"reconcilable against their schedule."
            + (" Marked WBO: not JER scope." if is_wbo else ""))
        nuid += 1
        p.activities = list(p.activities) + [a]
        byu[a.uid] = a
        ours[i] = a
        nw += is_wbo
    roomids[label] = set(hset)
    roomrep.append((label, len(hset), nw, len(hset) - nw))
print(f"{'room':<10}{'activities':>12}{'**WBO':>8}{'JER scope':>11}")
for r in roomrep:
    print(f"{r[0]:<10}{r[1]:>12}{r[2]:>8}{r[3]:>11}")
print(f"{'total':<10}{sum(r[1] for r in roomrep):>12}"
      f"{sum(r[2] for r in roomrep):>8}{sum(r[3] for r in roomrep):>11}")

# ===== 3. THE TAG GATE =================================================
print("\n" + "=" * 78)
print("3. THE FOUR-STAGE TAG GATE")
print("=" * 78)
tagset = {i: a for i, a in hact.items()
          if re.search(r"(Red|Pink|Orange|Yellow) Tag", a["name"])
          and i not in ours}
root = min(p.wbs_nodes, key=lambda w: len(path(w.uid)))
ph1 = next((w for w in p.wbs_nodes
            if w.name.startswith("Phase 1") and w.parent_uid == root.uid), root)
tagnode = WBSNode(uid=str(nwbs), name="Equipment Tag Verification (PH1)",
                  code="TAGPH1", parent_uid=ph1.uid, sequence_num=950,
                  id_prefix="MDC3.PH1.P.")
nwbs += 1
p.wbs_nodes = list(p.wbs_nodes) + [tagnode]
wbs[tagnode.uid] = tagnode
STAGE = {"Red": 1, "Pink": 2, "Orange": 3, "Yellow": 4}
OWNER = {"Red": "JER (contractor)", "Pink": "Holder", "Orange": "Cloud HQ",
         "Yellow": "ready for energization"}
tagged = {}
for i in sorted(tagset):
    h = tagset[i]
    dur = float(h["dur"] or 1)
    st = next((k for k in STAGE if h["name"].startswith(k)), None)
    eq = h["name"].split(" - ")[-1].strip()
    a = Activity(
        uid=str(nuid), activity_id=i, name=h["name"], wbs_uid=tagnode.uid,
        calendar_uid=CAL, activity_type="Task Dependent", status="Not Started",
        planned_duration=dur * 8.0, remaining_duration=dur * 8.0,
        actual_duration=0.0, percent_complete=0.0,
        planned_start=str(h["start"])[:10], planned_finish=str(h["finish"])[:10])
    a.udfs["COMMENTS"] = (
        f"Stage {STAGE.get(st, '?')} of 4 in Holder's equipment verification "
        f"gate for {eq} -- Red (JER), Pink (Holder), Orange (Cloud HQ), "
        f"Yellow (ready for energization). Nothing energizes until all four "
        f"clear. Signed off by: {OWNER.get(st, '?')}. From the {SRC}."
        + ("  THIS IS JER'S OWN SIGN-OFF." if st == "Red" else ""))
    nuid += 1
    p.activities = list(p.activities) + [a]
    byu[a.uid] = a
    ours[i] = a
    tagged.setdefault(eq, {})[st] = a
print(f"imported {len(tagset)} tag activities over {len(tagged)} equipment items")
print(f"   JER sign-off (Red Tag) activities: "
      f"{sum(1 for s in tagged.values() if s.get('Red'))}")
hd = [D(h['start']) for i, h in tagset.items() if D(h['start'])]
print(f"   window {min(hd)} .. "
      f"{max(D(h['finish']) for h in tagset.values() if D(h['finish']))}")

# ===== 4. HOLDER'S LOGIC ===============================================
print("\n" + "=" * 78)
print("4. HOLDER'S RELATIONSHIPS, TYPE AND LAG INFERRED FROM THEIR DATES")
print("=" * 78)
opairs = {(str(byu[r.predecessor_uid].activity_id).strip(),
           str(byu[r.successor_uid].activity_id).strip())
          for r in rels if r.predecessor_uid in byu and r.successor_uid in byu}
cand = [(a, b) for a, b in dict.fromkeys(hpairs)
        if a in ours and b in ours and (a, b) not in opairs]
print(f"Holder relationships            {len(set(hpairs)):>6}")
print(f"   both ends now exist here     "
      f"{sum(1 for a, b in set(hpairs) if a in ours and b in ours):>6}")
print(f"   we do not already carry      {len(cand):>6}\n")

TYPES = (("Finish to Start", "f", "s"), ("Start to Start", "s", "s"),
         ("Finish to Finish", "f", "f"), ("Start to Finish", "s", "f"))


def infer(a, b):
    """pick the relationship type whose implied lag is smallest"""
    ha, hb = hact.get(a), hact.get(b)
    if not ha or not hb:
        return "Finish to Start", 0.0
    pt = {"s": D(ha["start"]), "f": D(ha["finish"])}
    st = {"s": D(hb["start"]), "f": D(hb["finish"])}
    best = None
    for name, pk, sk in TYPES:
        if not pt[pk] or not st[sk]:
            continue
        o = off(pt[pk], st[sk])
        if o is None:
            continue
        lag = o - 1 if pk == "f" and sk == "s" else o
        if best is None or abs(lag) < abs(best[1]):
            best = (name, lag)
    return best or ("Finish to Start", 0)


# Shortest lag first. Order decides which ties win: a tight near-zero-lag tie
# is the real logic, so it goes in first and then correctly makes the
# long-range tie over the same span redundant. Processed the other way round
# the long-range tie lands first and rejects the real one -- the same own goal
# the MDC-2 generator raceway ties hit.
scored = sorted(((infer(a, b), a, b) for a, b in cand),
                key=lambda x: (abs(x[0][1]), x[0][0] != "Finish to Start"))
added = collections.Counter()
lagdist = collections.Counter()
skipcyc = skipred = 0
landed = collections.Counter()
bigkept = []
for n, ((typ, lag), a, b) in enumerate(scored):
    if n and not n % 500:
        print(f"   ... {n}/{len(scored)}  added {sum(added.values())}")
    src, dst = ours[a], ours[b]
    if reaches(src.uid, dst.uid):
        skipred += 1
        continue
    if tie(src, dst, typ, float(lag) * 8.0):
        added[typ] += 1
        lagdist[lag] += 1
        landed[".".join(b.split(".")[:3])] += 1
        if abs(lag) > 15:
            bigkept.append((lag, typ, a, b))
    else:
        skipcyc += 1
print(f"\nadded {sum(added.values())} relationships")
for k, v in added.most_common():
    print(f"   {k:<20}{v:>6}")
print(f"\n   skipped, existing logic already implies it  {skipred:>6}")
print(f"   skipped, would create a cycle               {skipcyc:>6}")
nz = sum(v for k, v in lagdist.items() if k)
print(f"\n   zero lag {lagdist.get(0, 0)}   with a lag {nz}")
print(f"   lag spread: " + "  ".join(
    f"{k:+d}d x{v}" for k, v in sorted(lagdist.items()) if k and v > 8))
print(f"\n   kept with |lag| over 15d: {len(bigkept)} -- these survived the "
      f"redundancy guard, so the tie is the only thing connecting the pair "
      f"and the gap is Holder's own")
for lag, typ, a, b in sorted(bigkept, key=lambda x: -abs(x[0]))[:8]:
    print(f"      {typ[:2]} {lag:+5d}d  {a:<24}-> {b}")
print(f"\nwhere the new logic landed (top 12):")
for k, v in landed.most_common(12):
    print(f"   {v:>5}  {k}")

# ===== 5. CLOSE THE NEW ACTIVITIES' OPEN ENDS ==========================
print("\n" + "=" * 78)
print("5. OPEN ENDS ON THE NEW ACTIVITIES")
print("=" * 78)
new = [a for a in p.activities if int(a.uid) >= 105784]
mil1 = ours.get("MDC3.MIL.PH1.1000")
nop = [a for a in new if not prv[a.uid]]
nos = [a for a in new if not nxt[a.uid]]
print(f"new activities {len(new)}:  no predecessor {len(nop)},  "
      f"no successor {len(nos)}")
print(f"   open starts by branch: "
      f"{dict(collections.Counter('.'.join(str(a.activity_id).split('.')[:3]) for a in nop).most_common(6))}")

# A tag belongs behind its own equipment, not behind a slab pour. The
# designator is the last " - " segment of the tag name
# ("Red Tag - Contractor Verification - HVS-HV101-01"), so take it from there
# rather than pattern-matching equipment shapes: ADC-R-27 does not fit the
# usual three-part shape, and a looser pattern happily mis-matched it onto
# ADC-R-54, a different circuit.
EQ = re.compile(r"^(?:Red|Pink|Orange|Yellow) Tag\b.*?-\s*([^-]+(?:-[^-]+)*)$",
                re.I)


def desig(n):
    m = EQ.match(str(n))
    return str(n).split(" - ")[-1].strip() if m else None


wanted = {d for d in (desig(a.name) for a in p.activities) if d}
byeq = collections.defaultdict(list)
for a in p.activities:
    if desig(a.name):
        continue
    nm = str(a.name)
    for d in wanted:
        if d in nm:
            byeq[d].append(a)
# the rooftop ADC risers are the one group with no activity naming them: their
# install work lives in our summary "Install Electrical Feeders to ADC's",
# which is the honest predecessor for their Red Tag
adcfeed = [a for a in p.activities
           if "Install Electrical Feeders to ADC" in str(a.name)
           and D(a.planned_finish)]
adcsrc = max(adcfeed, key=lambda x: D(x.planned_finish)) if adcfeed else None
print(f"   ADC riser tags will anchor on "
      f"{adcsrc.activity_id if adcsrc is not None else 'nothing'}"
      f" ({str(adcsrc.name) if adcsrc is not None else '-'})")
ntag = nroom = nfall = nadc = 0
# the room's own earliest activity leads its room
firstof = {}
for label, ids in roomids.items():
    got = [ours[i] for i in ids if i in ours]
    got = [x for x in got if D(x.planned_start)]
    if got:
        firstof[label] = min(got, key=lambda x: (D(x.planned_start), str(x.activity_id)))
for a in list(nop):
    eq = desig(a.name)
    done = False
    if eq:
        cands = [x for x in byeq.get(eq, []) if D(x.planned_finish)]
        if cands:
            src = max(cands, key=lambda x: D(x.planned_finish))
            if tie(src, a):
                ntag += 1
                done = True
        if not done and eq.startswith("ADC-R-") and adcsrc is not None:
            if tie(adcsrc, a):
                nadc += 1
                done = True
    if not done:
        for label, ids in roomids.items():
            if str(a.activity_id) in ids and label in firstof \
                    and firstof[label].uid != a.uid:
                if tie(firstof[label], a):
                    nroom += 1
                    done = True
                break
print(f"   tied {ntag} tag activities behind the equipment they verify")
print(f"   tied {nadc} ADC riser tags behind the rooftop feeder install")
print(f"   tied {nroom} room activities behind their room's first activity")
# whatever is still open starts where its room/branch does: the Level 2 slab
# that releases our existing ER rooms
ent = collections.Counter()
for a in p.activities:
    mm = re.match(r"MDC3\.PH1\.ER\.(\d{3})\.1000$", str(a.activity_id))
    if mm:
        for u in prv[a.uid]:
            if u in byu:
                ent[str(byu[u].activity_id)] += 1
anchor = ours.get(ent.most_common(1)[0][0]) if ent else None
print(f"   existing ER rooms are released by {ent.most_common(2)}")
for a in [x for x in new if not prv[x.uid]]:
    if anchor is not None and tie(anchor, a):
        nfall += 1
print(f"   tied {nfall} remaining open starts off "
      f"{anchor.activity_id if anchor is not None else '-'}")
nc = 0
for a in [x for x in new if not nxt[x.uid]]:
    if tie(a, mil1):
        nc += 1
print(f"   tied {nc} open finishes into {mil1.activity_id if mil1 else '-'} "
      f"({str(mil1.name) if mil1 else ''})")
print(f"   remaining open: {sum(1 for a in new if not prv[a.uid])} "
      f"no predecessor, {sum(1 for a in new if not nxt[a.uid])} no successor")

# ===== verify ==========================================================
p.relations = rels
alive = {a.uid for a in p.activities}
ind = collections.Counter()
s2 = collections.defaultdict(list)
for r in rels:
    if r.predecessor_uid in alive and r.successor_uid in alive:
        ind[r.successor_uid] += 1
        s2[r.predecessor_uid].append(r.successor_uid)
q = collections.deque(u for u in alive if not ind[u])
i2, n = dict(ind), 0
while q:
    u = q.popleft()
    n += 1
    for v in s2[u]:
        i2[v] -= 1
        if not i2[v]:
            q.append(v)
ids = collections.Counter(str(a.activity_id) for a in p.activities)
dupid = sorted(k for k, v in ids.items() if v > 1)
dup = len(rels) - len({(r.predecessor_uid, r.successor_uid) for r in rels})
dang = sum(1 for r in rels
           if r.predecessor_uid not in alive or r.successor_uid not in alive)
seen, comp = set(), 0
adj = collections.defaultdict(set)
for r in rels:
    adj[r.predecessor_uid].add(r.successor_uid)
    adj[r.successor_uid].add(r.predecessor_uid)
big = 0
for u in alive:
    if u in seen:
        continue
    comp += 1
    c, qq = 0, collections.deque([u])
    seen.add(u)
    while qq:
        x = qq.popleft()
        c += 1
        for y in adj[x] - seen:
            seen.add(y)
            qq.append(y)
    big = max(big, c)
print("\n" + "=" * 78)
print(f"{'':<36}{'v22':>10}{'v26':>10}")
print(f"{'activities':<36}{V22A:>10}{len(p.activities):>10}")
print(f"{'relationships':<36}{V22R:>10}{len(rels):>10}")
print(f"{'duplicate activity ids':<36}{0:>10}{len(dupid):>10}")
print(f"{'duplicate relationships':<36}{0:>10}{dup:>10}")
print(f"{'dangling relationships':<36}{0:>10}{dang:>10}")
print(f"{'acyclic':<36}{'all':>10}"
      f"{'all' if n == len(alive) else f'{n}/{len(alive)}':>10}")
print(f"{'islands':<36}{38:>10}{comp:>10}")
print(f"{'largest network':<36}{'':>10}{f'{100 * big // len(alive)}%':>10}")
if dupid:
    print(f"DUPLICATE IDS: {dupid[:10]}")
print(f"\nstatus: " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
    a.status for a in p.activities).most_common()))
print(f"completed dated after the data date: "
      f"{sum(1 for a in p.activities if a.status == 'Completed' and D(a.actual_finish) and D(a.actual_finish) > dd)}")

out = f"{SCR}/MDC3_logic_repaired_v26.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
json.dump({"adopted": [[i, w, nw, str(s), str(f)] for i, _, w, nw, s, f in adopted],
           "conflict": conflict, "rooms": roomrep,
           "tags": len(tagset), "tag_equipment": len(tagged),
           "rel_added": dict(added), "rel_cand": len(cand),
           "skip_cycle": skipcyc},
          open(f"{SCR}/work/mdc3_v26.json", "w"), indent=1, default=str)
