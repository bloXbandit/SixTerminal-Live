"""MDC-3 v35: build the Fire Alarm scope as its own WBS.

Decisions the user settled, and what each one means here:

1. Only the PURE fire-alarm activities move into the new tree. The 8 combined
   "OH Branch Electrical / Fire Alarm / Controls Raceway" lines and the ~42
   "Overhead Rough In Electrical/ FA - ER xxx" lines stay exactly where they
   are -- deleting those would take real electrical rough-in with them.
   Moving rather than deleting means every existing relationship survives:
   relations reference activity uids, not WBS, so a move costs no logic.

2. Fire Alarm is a top-level WBS, sibling to the three phases, because the
   levels do not respect phase boundaries and neither does the FA crew.

3. Generator rooms 312-314 are NOT complete. Those actuals belong to MDC-1.
   Nothing here marks them done.

4. Scaffolding is 45 CALENDAR days, so it runs on the 7-day calendar.
   Device install starts one week after the scaffold FINISHES.

5. Valve rooms are 101-104 / 201-204 / 301-304, four per level, read off the
   Phalcon FA-VALVE sheet.

6. The floor mapping is read from the FA set itself: the first digit of the
   room number is the level. Generator rooms 301-328 are PENTHOUSE, not
   Level 1. The scaffold is Level 1 work erected to reach them.

7. The existing generator-room folders are left alone. The FA tree gets its
   OWN per-room folders so no room data is hidden or duplicated.

Crew flow is chained in DATE order, not numeric order, so the new logic
follows how the crew actually moves and does not fight the dates already in
the file. Nothing existing is removed.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402
from engine.xml_writer import write_p6_xml                  # noqa: E402
from engine.schedule_model import Activity, WBSNode         # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
HRS = 8.0
CAL5, CAL7 = "1", "6591"          # Standard 5-day, G7-DAY NO HOLIDAY
SCAF_START = "2026-11-30"
SCAF_CAL_DAYS = 45
GAP_AFTER_SCAFFOLD = 5            # one week, working days

p = load_xml(f"{SCR}/MDC3_logic_repaired_v34.xml")
ids = {str(a.activity_id): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
root = next(w for w in p.wbs_nodes if w.parent_uid not in wbs)
Rel = type(p.relations[0])
nrel = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
nact = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
nwbs = max(int(w.uid) for w in p.wbs_nodes if str(w.uid).isdigit()) + 1
seq = max(w.sequence_num for w in p.wbs_nodes if w.parent_uid == root.uid) + 1


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def S(a):
    return D(a.actual_start) or D(a.early_start) or D(a.planned_start)


def mkwbs(name, parent, code, order):
    global nwbs
    w = WBSNode(uid=str(nwbs), name=name, code=code, parent_uid=parent,
                sequence_num=order)
    p.wbs_nodes.append(w)
    wbs[w.uid] = w
    nwbs += 1
    return w


def mkact(aid, name, node, days, men, cal=CAL5, note=""):
    global nact
    a = Activity(uid=str(nact), activity_id=aid, name=name, wbs_uid=node.uid,
                 calendar_uid=cal, planned_duration=days * HRS,
                 remaining_duration=days * HRS, status="Not Started")
    a.planned_labor_units = round(men * 10.6 * days, 1)
    a.remaining_labor_units = a.planned_labor_units
    a.udfs = {"Number of Electricians": str(men)}
    if note:
        a.udfs["COMMENTS"] = note[:248]
    p.activities.append(a)
    ids[aid] = a
    nact += 1
    return a


def tie(pred, succ, typ="Finish to Start", lag_days=0.0):
    global nrel
    if pred is None or succ is None:
        return False
    p.relations.append(Rel(uid=str(nrel), predecessor_uid=pred.uid,
                           successor_uid=succ.uid, type=typ,
                           lag=lag_days * HRS))
    nrel += 1
    return True


# ====================================================================
# the tree
# ====================================================================
FA = mkwbs("Fire Alarm", root.uid, "MDC3.FA", seq)
L1 = mkwbs("Level 1", FA.uid, "MDC3.FA.L1", 1)
L2 = mkwbs("Level 2", FA.uid, "MDC3.FA.L2", 2)
PH = mkwbs("Penthouse", FA.uid, "MDC3.FA.PH", 3)

n_scaf = mkwbs("Scaffolding", L1.uid, "MDC3.FA.L1.SCAF", 1)
n_l1er = mkwbs("Electrical Rooms", L1.uid, "MDC3.FA.L1.ER", 2)
n_l1dh = mkwbs("Data Halls & Galleries", L1.uid, "MDC3.FA.L1.DH", 3)
n_l1vr = mkwbs("Valve Rooms", L1.uid, "MDC3.FA.L1.VR", 4)
n_l2er = mkwbs("Electrical Rooms", L2.uid, "MDC3.FA.L2.ER", 1)
n_l2dh = mkwbs("Data Halls & Galleries", L2.uid, "MDC3.FA.L2.DH", 2)
n_l2vr = mkwbs("Valve Rooms", L2.uid, "MDC3.FA.L2.VR", 3)
n_gs = mkwbs("Generator Rooms - South", PH.uid, "MDC3.FA.PH.GENS", 1)
n_gn = mkwbs("Generator Rooms - North", PH.uid, "MDC3.FA.PH.GENN", 2)
n_phvr = mkwbs("Valve Rooms", PH.uid, "MDC3.FA.PH.VR", 3)

# ====================================================================
# 1. move the pure-FA activities (logic survives untouched)
# ====================================================================
import re                                                   # noqa: E402
PURE = re.compile(r'^(Install Fire Alarm Devices|Install Upper Vesda|'
                  r'Upper Vesda Install|Install Lower Vesda|'
                  r'Lower Vesda Install|Inspection of (Upper|Lower) Vesda)',
                  re.I)
COMB = re.compile(r'rough in|branch electrical|controls rack', re.I)
moved = 0
for a in p.activities:
    nm = str(a.name).strip()
    if not PURE.match(nm) or COMB.search(nm):
        continue
    m = re.search(r'\b(DH|GL)\s?(\d)(\d{2})', f"{a.activity_id} {nm}")
    if not m:
        continue
    a.wbs_uid = (n_l1dh if m.group(2) == "1" else n_l2dh).uid
    moved += 1
print(f"pure-FA activities moved into the FA tree : {moved}")

# ====================================================================
# 2. scaffolding
# ====================================================================
scaf = mkact("MDC3.FA.L1.SCAF.1000", "Start Scaffolding - Level 1", n_scaf,
             SCAF_CAL_DAYS, 4, cal=CAL7,
             note="45 CALENDAR days from 11/30/26. JER scope. Erected at "
                  "Level 1 to reach the penthouse generator rooms. Crew size "
                  "ASSUMED 4, matching the FA device crew - confirm.")
scaf.constraint_type = "Start On"
scaf.constraint_date = SCAF_START
scaf.planned_start = SCAF_START

# ====================================================================
# 3. generator rooms - FA device install, penthouse
# ====================================================================
def gen_acts(n):
    return [a for a in p.activities
            if re.search(rf'GEN\.{n}\.', str(a.activity_id))]


def find(n, pat):
    for a in gen_acts(n):
        if re.search(pat, str(a.name), re.I):
            return a
    return None


added_gen = 0
for lo, hi, node, side in ((315, 328, n_gs, "South"), (301, 314, n_gn, "North")):
    rooms = [(n, min((S(a) for a in gen_acts(n) if S(a)), default=None))
             for n in range(lo, hi + 1)]
    rooms = [(n, d) for n, d in rooms if d]
    rooms.sort(key=lambda x: x[1])          # DATE order = real crew flow
    prev = None
    for n, _ in rooms:
        room = mkwbs(f"Gen {n}", node.uid, f"MDC3.FA.PH.GEN.{n}", n)
        a = mkact(f"MDC3.FA.GEN.{n}.1000", f"FA Device Install (Gen {n})",
                  room, 5, 4,
                  note=f"FA device install, {side}side penthouse generator "
                       f"room. 4 electricians. Duration ASSUMED 5d - confirm.")
        tie(scaf, a, lag_days=GAP_AFTER_SCAFFOLD)       # 1 wk after scaffold
        tie(find(n, r'Hang SCR'), a)                    # room's own overhead
        tie(prev, a)                                    # crew flow
        tie(a, find(n, r'Engine Start Up'))             # into commissioning
        prev = a
        added_gen += 1
print(f"generator-room FA device installs added   : {added_gen}")

# ====================================================================
# 4. electrical rooms - VESDA racks (none exist today, all 26)
# ====================================================================
def room_act(pfx, n, pat, last=False):
    """Find a room's activity by name. `last` takes the latest-dated match --
    ER 210 calls its paint "Prime/First Coat Paint", and a room with several
    paint coats should hang the VESDA rack off the final one."""
    hits = [a for a in p.activities
            if re.search(rf'{pfx}[ .]{n}\b', f"{a.activity_id} {a.name}")
            and re.search(pat, str(a.name), re.I)]
    if not hits:
        return None
    if last:
        hits.sort(key=lambda a: S(a) or dt.date(1900, 1, 1))
    return hits[-1] if last else hits[0]


er_rooms = sorted({m.group(1) for a in p.activities
                   for m in [re.search(r'\bER[ .](\d{3})',
                                       f"{a.activity_id} {a.name}")] if m})
added_er = 0
for r in er_rooms:
    node = n_l1er if r[0] == "1" else n_l2er
    sub = mkwbs(f"ER {r}", node.uid, f"MDC3.FA.L{r[0]}.ER.{r}", int(r))
    a = mkact(f"MDC3.FA.ER.{r}.1000", f"Install VESDA Rack (ER {r})", sub,
              3, 2,
              note="VESDA rack. Follows room paint per FA PM. Duration "
                   "ASSUMED 3d - confirm.")
    tie(room_act("ER", r, r'paint', last=True), a)
    tie(a, room_act("ER", r, r'Testing and Inspections')
           or room_act("ER", r, r'Device Trim Out'))
    added_er += 1
print(f"ER VESDA racks added                      : {added_er}")

# ====================================================================
# 5. valve rooms - 4 per level
# ====================================================================
added_vr = 0
tail = scaf
for lvl, node in (("1", n_l1vr), ("2", n_l2vr), ("3", n_phvr)):
    prev_room = None
    for k in range(1, 5):
        rno = f"{lvl}0{k}"
        sub = mkwbs(f"Valve Room {rno}", node.uid,
                    f"MDC3.FA.L{lvl}.VR.{rno}", k)
        pn = mkact(f"MDC3.FA.VR.{rno}.1000",
                   f"Install FA Panel (Valve Rm {rno})", sub, 10, 2,
                   note="Panel install, 2 wk. Valve-room sequence and dates "
                        "ASSUMED - no anchor in the schedule, CONFIRM.")
        dv = mkact(f"MDC3.FA.VR.{rno}.1010",
                   f"FA Device Install (Valve Rm {rno})", sub, 5, 2)
        ts = mkact(f"MDC3.FA.VR.{rno}.1020",
                   f"FA Testing & Commissioning (Valve Rm {rno})", sub, 5, 2)
        tie(pn, dv)
        tie(dv, ts)
        tie(prev_room or tail, pn,
            lag_days=GAP_AFTER_SCAFFOLD if prev_room is None and lvl == "1"
            else 0)
        prev_room = ts
        added_vr += 3
    tail = prev_room
print(f"valve-room activities added               : {added_vr}")

# ====================================================================
# checks
# ====================================================================
print("\n" + "=" * 70)
pre = {r.successor_uid for r in p.relations}
new = [a for a in p.activities if str(a.activity_id).startswith("MDC3.FA.")]
print(f"new FA activities              : {len(new)}")
print(f"   with a predecessor          : {sum(1 for a in new if a.uid in pre)}")
print(f"   hours added                 : "
      f"{sum(a.planned_labor_units for a in new):,.0f} h")
print(f"activities total               : {len(p.activities):,}")
print(f"relations total                : {len(p.relations):,}")
seen = set()
dup = sum(1 for r in p.relations
          if (r.predecessor_uid, r.successor_uid) in seen
          or seen.add((r.predecessor_uid, r.successor_uid)))
print(f"duplicate ties                 : {dup}")
print(f"self ties                      : "
      f"{sum(1 for r in p.relations if r.predecessor_uid == r.successor_uid)}")
adj = collections.defaultdict(set)
for r in p.relations:
    adj[r.predecessor_uid].add(r.successor_uid)
col, loops = collections.defaultdict(int), 0
for n in list(adj):
    if col[n]:
        continue
    st = [(n, iter(adj[n]))]
    col[n] = 1
    while st:
        u, it = st[-1]
        try:
            v = next(it)
            if col[v] == 1:
                loops += 1
            elif col[v] == 0:
                col[v] = 1
                st.append((v, iter(adj[v])))
        except StopIteration:
            col[u] = 2
            st.pop()
print(f"circular logic                 : {loops}")
out = f"{SCR}/MDC3_logic_repaired_v35.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
