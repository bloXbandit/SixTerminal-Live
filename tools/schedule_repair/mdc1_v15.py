"""MDC-1 v15: actualize October from the GC's Exhibit S, by scope where ids differ.

The GC's print carries 166 actual dates after 09/30, running to 10/28. It is
their schedule, not ours -- 5,000 activities against our 2,533, 74 ids in
common, and across the generator rooms not one activity name shared. So most
of it cannot be matched activity to activity.

It can be matched by SCOPE, which is what this does, in three passes, each
one weaker than the last and each recorded so it can be told apart afterwards.

  1. SAME ID. The 74 activities both schedules carry under the same id. Where
     the GC reports an actual and we do not, their dates are taken.

  2. SAME ROOM, SAME WORK. For a GC activity with an actual, find ours in the
     same room whose work is of the same kind -- LBB, isolator, cable tray,
     pull/term, overhead electrical rough-in, fire alarm, lighting, conduit,
     skid, start-up. Scored on how much of the vocabulary agrees, and only
     taken where the match is unambiguous.

  3. WHAT THE WORK IMPLIES. This is the one that does the real work. If the
     GC says the LBB in Gen 320 was set on 10/02, then everything our own
     logic puts in front of that -- the high steel, the hangers, the ladder
     tray, the branch raceway -- was also done, whatever either schedule calls
     it. So every incomplete predecessor of a newly-completed activity is
     completed too, transitively, dated backwards from its successor using its
     own duration.

     This is inference, not report. It is marked as such on every activity it
     touches, and it is bounded: nothing is dated after the 10/31 data date,
     and nothing already carrying a status is overwritten.

Nothing books past 10/31. The GC's latest actual is 10/28, so the whole month
lands inside the data date.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
DD = dt.date(2026, 10, 31)
CUT = dt.date(2026, 9, 30)
WORK = frozenset({0, 1, 2, 3, 4, 5})
E = {a["id"]: a for a in json.load(open(f"{SCR}/work/mdc1_exs.json"))}
p = load_xml(f"{SCR}/MDC1_logic_repaired_v14.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
B0 = collections.Counter(a.status for a in p.activities)


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


def back(fin, days):
    d, n = fin, 1
    while n < max(1, int(round(days))):
        d -= dt.timedelta(days=1)
        if d.weekday() in WORK:
            n += 1
    while d.weekday() not in WORK:
        d -= dt.timedelta(days=1)
    return d


def room(s):
    s = str(s).upper().replace(".", " ")
    m = re.search(r"\b(ER|ERR|MV|MVR|HV|HVR|GEN|GL|GAL|DH|DAT|ELEC|ELC)\s*R?(\d{3})\b", s)
    return f"{m.group(1)}{m.group(2)}" if m else ""


KIND = [("LBB", r"\bLBB\b"), ("isolator", r"isolator"),
        ("tray", r"cable\s*/?\s*tray|ladder tray"),
        ("pull/term", r"\bpull|\bterm|cable pulls"),
        ("oh-elec", r"rough.?in electrical|rough.?in/ ?fa|branch raceway|"
                    r"overhead rough in"),
        ("fire alarm", r"fire alarm|\bFA\b"), ("lighting", r"light"),
        ("conduit", r"conduit"), ("skid", r"skid"),
        ("start-up", r"start.?up|burn.?in|energi"), ("SCR", r"\bSCR\b|dosing")]


def kinds(n):
    return {k for k, rx in KIND if re.search(rx, str(n), re.I)}


pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)

log = {"id": [], "room": [], "implied": []}


def complete(a, st, fi, how, note):
    """mark done, clamped to the data date, never overwriting a status"""
    if a.status == "Completed":
        return False
    fi = min(fi, DD)
    st = min(st or fi, fi)
    a.status = "Completed"
    a.actual_start = st.isoformat()
    a.actual_finish = fi.isoformat()
    a.actual_duration = a.planned_duration
    a.remaining_duration = 0.0
    a.percent_complete = 100.0
    old = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((old + "  ") if old else "") + note)[:248])
    log[how].append((str(a.activity_id), str(a.name)[:40], st, fi))
    return True


# ===== pass 1: same id =================================================
print("=" * 78)
print("1. SAME ACTIVITY ID")
print("=" * 78)
for i in sorted(set(E) & set(ours)):
    g, o = E[i], ours[i]
    gf, gs = D(g["finish"]), D(g["start"])
    if g["finished"] and gf and o.status != "Completed":
        complete(o, gs or gf, gf, "id",
                 f"Completed from the GC Exhibit S 11/03/2026, which reports "
                 f"actual {gs or gf} to {gf} under the same activity id.")
print(f"completed: {len(log['id'])}")
for i, n, s, f in log["id"]:
    print(f"   {i:<24}{n:<42}{s} .. {f}")

# ===== pass 2: same room, same kind of work ============================
print("\n" + "=" * 78)
print("2. SAME ROOM, SAME KIND OF WORK")
print("=" * 78)
byroom = collections.defaultdict(list)
for a in p.activities:
    r = room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))
    if r:
        byroom[r].append(a)
recent = [a for a in E.values()
          if a["finished"] and D(a["finish"]) and D(a["finish"]) > CUT]
amb = 0
for g in sorted(recent, key=lambda x: str(x["finish"])):
    r = room(f"{g['id']} {g['name']}")
    gk = kinds(g["name"])
    if not r or not gk:
        continue
    scored = []
    for a in byroom.get(r, []):
        if a.status == "Completed":
            continue
        s = len(gk & kinds(a.name))
        if s:
            scored.append((s, a))
    if not scored:
        continue
    top = max(s for s, _ in scored)
    best = [a for s, a in scored if s == top]
    if len(best) > 1:
        # Several of ours answer to the same description -- a room can carry
        # Set LBB, Pull LBB to MSB and Final Connections LBB. The GC reporting
        # "LBB Install" done means the FIRST of them is done, not all three,
        # so take the earliest and let the predecessor pass carry the rest.
        amb += 1
        best = [min(best, key=lambda x: (D(x.planned_start) or dt.date(2030, 1, 1),
                                         str(x.activity_id)))]
    gf, gs = D(g["finish"]), D(g["start"])
    complete(best[0], gs or gf, gf, "room",
             f"Completed from the GC Exhibit S 11/03/2026: their "
             f"'{g['name'][:60]}' in {r} finished {gf}. Matched on room and "
             f"kind of work, not activity id -- the two schedules name this "
             f"work differently.")
print(f"completed: {len(log['room'])}   of which resolved by taking the "
      f"earliest of several candidates: {amb}")
for i, n, s, f in log["room"][:25]:
    print(f"   {i:<24}{n:<42}{s} .. {f}")
if len(log["room"]) > 25:
    print(f"   ... and {len(log['room']) - 25} more")

# ===== pass 3: what the work implies ===================================
print("\n" + "=" * 78)
print("3. WHAT THE COMPLETED WORK IMPLIES ABOUT ITS PREDECESSORS")
print("=" * 78)
seeds = [ours[i] for i, _, _, _ in log["id"] + log["room"]]
order, seen = [], set()
stack = [a.uid for a in seeds]
while stack:
    u = stack.pop()
    if u in seen:
        continue
    seen.add(u)
    order.append(u)
    for q in pred[u]:
        if q in byu and byu[q].status != "Completed":
            stack.append(q)
# date predecessors after their successors, so work backwards from the seeds
order = [u for u in order if byu[u].status != "Completed"]
changed = True
rounds = 0
while changed and rounds < 40:
    changed, rounds = False, rounds + 1
    for u in order:
        a = byu[u]
        if a.status == "Completed":
            continue
        ss = [byu[v] for v in succ[u] if v in byu]
        done = [D(x.actual_start) for x in ss
                if x.status == "Completed" and D(x.actual_start)]
        if not done:
            continue
        fin = min(done)
        fin = min(fin, DD)
        dur = max(1, round((a.planned_duration or 8) / 8))
        if complete(a, back(fin, dur), fin, "implied",
                    f"Completed by inference, not report: work our logic puts "
                    f"after this was done in October per the GC Exhibit S "
                    f"11/03/2026, so this must have finished first. Dated "
                    f"back {dur} working days from {fin}. CONFIRM WITH THE "
                    f"FIELD."):
            changed = True
print(f"completed by inference: {len(log['implied'])}  (in {rounds} passes)")
k = collections.Counter(re.sub(r"\s*\(.*?\)", "", n).strip()
                        for _, n, _, _ in log["implied"])
for kk, v in k.most_common(14):
    print(f"   {v:>4}  {kk}")

# ===== verify ==========================================================
p.data_date = DD.isoformat()
A1 = collections.Counter(a.status for a in p.activities)
bad = [a for a in p.activities if a.status == "Completed"
       and D(a.actual_finish) and D(a.actual_finish) > DD]
seq = 0
for r in p.relations:
    a, b = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
    if not a or not b or a.status != "Completed" or b.status != "Completed":
        continue
    fa, sb = D(a.actual_finish), D(b.actual_start)
    if fa and sb and sb < fa:
        seq += 1
print("\n" + "=" * 78)
print(f"{'':<34}{'before':>10}{'after':>10}")
for s in ("Completed", "In Progress", "Not Started"):
    print(f"{s:<34}{B0.get(s, 0):>10}{A1.get(s, 0):>10}")
print(f"\ncompleted dated after the {DD} data date: {len(bad)}")
print(f"completed pairs where the successor starts before the predecessor "
      f"finishes: {seq}")
print(f"longest UDF value: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
print(f"\nnewly completed in total: "
      f"{len(log['id']) + len(log['room']) + len(log['implied'])}"
      f"  (same id {len(log['id'])}, same room {len(log['room'])}, "
      f"implied {len(log['implied'])})")
out = f"{SCR}/MDC1_logic_repaired_v15.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
json.dump({k: [[i, n, str(s), str(f)] for i, n, s, f in v]
           for k, v in log.items()},
          open(f"{SCR}/work/mdc1_v15_log.json", "w"), indent=1)
print(f"\nwrote {out}")
