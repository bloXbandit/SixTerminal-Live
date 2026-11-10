"""MDC-1 v17: book the 150 overdue findings, then let the chain lean forward.

TWO PASSES.

1. BOOK THE 150. Everything tests 1-3 of the triage turned up, taken at the
   user's instruction regardless of how strong the evidence is -- better
   covered than not, and anything booked wrongly gets adjusted in P6.

     21   a successor is already running, so this must have finished
    111   the GC reports that kind of work done in that room
     18   a JER daily report names that work in that room

   Dates come from whatever found it: the successor's actual start for the
   first group, the GC's reported finish for the second, the report date for
   the third. Everything is capped at the 10/31 data date.

2. LEAN ON THE NEXT ACTIVITY. Once a predecessor is complete, a successor
   left Not Started makes the schedule assume a gap that is usually not
   there: F9 can only start it at the data date, so every un-statused
   hand-off pushes the chain out, and over a long chain those gaps compound
   into months. The fix is to assume the next activity picked up rather than
   waiting for someone to report that it did.

   So: any activity whose predecessors are ALL complete, and which should
   have started by the data date, is set In Progress from the working day
   after the last of those predecessors finished.

   This is deliberately one layer deep. Marking B in progress leaves B
   incomplete, so C does not qualify -- the schedule leans on the next
   activity, it does not sprint to the end. No cascade, and nothing is
   invented beyond the first hand-off.

   Remaining duration is recut from the data date so the work left is
   honest, and percent complete is set from how much of the duration has
   elapsed rather than assumed.

Every activity touched says in its comment which pass set it and on what
evidence. Nothing is dated past the data date, and nothing already Completed
is altered.
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
WORK = frozenset({0, 1, 2, 3, 4, 5})
E = {a["id"]: a for a in json.load(open(f"{SCR}/work/mdc1_exs.json"))}
R = json.load(open(f"{SCR}/work/mdc1_daily.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v16.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
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


def nextwd(d):
    d = d + dt.timedelta(days=1)
    while d.weekday() not in WORK:
        d += dt.timedelta(days=1)
    return d


def back(fin, days):
    d, n = fin, 1
    while n < max(1, int(round(days))):
        d -= dt.timedelta(days=1)
        if d.weekday() in WORK:
            n += 1
    while d.weekday() not in WORK:
        d -= dt.timedelta(days=1)
    return d


def wdays(a, b):
    if not a or not b or b < a:
        return 0
    return sum(1 for i in range((b - a).days + 1)
               if (a + dt.timedelta(days=i)).weekday() in WORK)


DD = D(p.data_date)
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)


def room(s):
    s = str(s).upper().replace(".", " ")
    m = re.search(r"\b(ER|ERR|MV|HV|GEN|GL|GAL|DH|DAT|ELEC|POE)\s*R?\s?(\d{3})\b", s)
    return f"{m.group(1)}{m.group(2)}" if m else ""


KIND = [("LBB", r"\bLBB\b"), ("isolator", r"isolator"),
        ("tray", r"cable\s*/?\s*tray|ladder tray"),
        ("pull/term", r"\bpull|\bterm|cable pulls"),
        ("oh-elec", r"rough.?in electrical|rough.?in/ ?fa|branch raceway|"
                    r"overhead rough in"),
        ("fire alarm", r"fire alarm|\bFA\b|vesda"), ("lighting", r"\blight"),
        ("conduit", r"conduit|\bEMT\b"), ("skid", r"skid"),
        ("start-up", r"start.?up|burn.?in|energi"),
        ("device", r"device|trim out"), ("hanger", r"hanger|high steel")]


def kinds(n):
    return {k for k, rx in KIND if re.search(rx, str(n), re.I)}


gcdone = collections.defaultdict(dict)
for a in E.values():
    if a["finished"] and D(a["finish"]):
        r = room(a["id"] + " " + a["name"])
        if r:
            for k in kinds(a["name"]):
                d = D(a["finish"])
                if k not in gcdone[r] or d > gcdone[r][k]:
                    gcdone[r][k] = d
fieldwork = collections.defaultdict(dict)
for r in R:
    d = D(r["date"])
    if not d or d > DD:
        continue
    txt = f"{r['tasks']} {r['comments']}"
    for m in re.finditer(r"\b(ER|ERR|MV|HV|GEN|GL|DH|POE|ELEC)\s*R?\s?(\d{3})\b",
                         txt, re.I):
        rm = f"{m.group(1).upper()}{m.group(2)}"
        for k in kinds(txt):
            if k not in fieldwork[rm] or d > fieldwork[rm][k]:
                fieldwork[rm][k] = d


def complete(a, fin, why):
    fin = min(fin, DD)
    dur = max(1, round((a.planned_duration or 8) / 8))
    a.status = "Completed"
    a.actual_start = back(fin, dur).isoformat()
    a.actual_finish = fin.isoformat()
    a.actual_duration = a.planned_duration
    a.remaining_duration = 0.0
    a.percent_complete = 100.0
    old = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((old + "  ") if old else "") + why)[:248])


# ===== PASS 1 =========================================================
print("=" * 76)
print("1. BOOKING THE 150")
print("=" * 76)
overdue = [a for a in p.activities
           if a.status == "Not Started" and not WBO.search(str(a.name))
           and D(a.early_start) and D(a.early_start) < DD]
n1 = n2 = n3 = 0
for a in overdue:
    rm = room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))
    kk = kinds(a.name)
    ds = [byu[v] for v in succ[a.uid] if v in byu]
    moved = [x for x in ds if x.status in ("Completed", "In Progress")
             and D(x.actual_start)]
    if moved:
        s = min(D(x.actual_start) for x in moved)
        tag = moved[0]
        complete(a, max(D(a.early_start), s - dt.timedelta(days=1)),
                 f"Completed because {tag.activity_id} "
                 f"({str(tag.name)[:28]}) is already {tag.status.lower()} and "
                 f"cannot have begun before this finished. Dates inferred, "
                 f"not reported.")
        n1 += 1
    elif rm and kk and (set(gcdone.get(rm, {})) & kk):
        k = sorted(set(gcdone[rm]) & kk)[0]
        complete(a, gcdone[rm][k],
                 f"Completed from the GC Exhibit S 11/03/2026, which reports "
                 f"{k} work in {rm} finished {gcdone[rm][k]}. Matched on room "
                 f"and kind of work, not activity id. CONFIRM.")
        n2 += 1
    elif rm and kk and (set(fieldwork.get(rm, {})) & kk):
        k = sorted(set(fieldwork[rm]) & kk)[0]
        complete(a, fieldwork[rm][k],
                 f"Completed from a JER daily report naming {k} work in {rm} "
                 f"on {fieldwork[rm][k]}. A report naming the work is not a "
                 f"sign-off. CONFIRM.")
        n3 += 1
print(f"successor already running : {n1}")
print(f"GC reports it done        : {n2}")
print(f"JER daily report names it : {n3}")
print(f"total booked complete     : {n1 + n2 + n3}")

# Booking a thing complete without its predecessors leaves the schedule
# claiming a successor finished while what feeds it never started. Pass 1
# tripled those pairs, 31 to 92. If X is done then everything feeding X is
# done, so the completion is carried backwards until that is true.
print("\n--- carrying those completions back to their predecessors ---")
prop = 0
changed = True
rounds = 0
while changed and rounds < 40:
    changed, rounds = False, rounds + 1
    for a in p.activities:
        if a.status == "Completed":
            continue
        ds = [byu[v] for v in succ[a.uid] if v in byu]
        done = [D(x.actual_start) for x in ds
                if x.status in ("Completed", "In Progress") and D(x.actual_start)]
        if not done:
            continue
        fin = min(min(done) - dt.timedelta(days=1), DD)
        es = D(a.early_start)
        if es and fin < es:
            fin = min(es, DD)
        complete(a, fin,
                 "Completed by inference: work our logic puts after this is "
                 "already under way, so this must have finished first. Dates "
                 "inferred, not reported. CONFIRM WITH THE FIELD.")
        prop += 1
        changed = True
print(f"predecessors completed to keep the sequence honest: {prop} "
      f"(in {rounds} passes)")

# ===== PASS 2 =========================================================
print("\n" + "=" * 76)
print("2. LEANING ON THE NEXT ACTIVITY")
print("=" * 76)
started, skipped, overdue_handoff = [], 0, []
for a in p.activities:
    if a.status != "Not Started" or WBO.search(str(a.name)):
        continue
    ps = [byu[q] for q in pred[a.uid] if q in byu]
    if not ps or any(x.status != "Completed" for x in ps):
        continue
    fins = [D(x.actual_finish) for x in ps if D(x.actual_finish)]
    if not fins:
        continue
    st = nextwd(max(fins))
    es = D(a.early_start)
    if es and es > st:
        st = es
    if st > DD:
        skipped += 1
        continue
    dur = max(1, round((a.planned_duration or 8) / 8))
    elapsed = wdays(st, DD)
    rem = max(1, dur - elapsed)
    a.status = "In Progress"
    a.actual_start = st.isoformat()
    a.actual_finish = None
    a.remaining_duration = float(rem) * 8.0
    # capped below 100: an activity cannot be in progress AND complete, and
    # the first run produced 52 of those. Where the hand-off is old enough
    # that the work should already have finished, 95% says "all but done,
    # go and confirm" rather than asserting a completion nobody reported.
    a.percent_complete = min(95.0, round(100.0 * elapsed / dur, 1))
    if elapsed >= dur:
        overdue_handoff.append(str(a.activity_id))
    last = max(ps, key=lambda x: D(x.actual_finish) or dt.date(1900, 1, 1))
    old = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((old + "  ") if old else "") + (
        f"Assumed started {st}: everything in front of it is complete "
        f"({last.activity_id} finished {D(last.actual_finish)}), and leaving "
        f"it Not Started would make the schedule assume a gap that is usually "
        f"not there. {elapsed} of {dur} days elapsed, {rem} remaining. "
        f"NOT REPORTED - correct in P6 if it has not begun."))[:248])
    started.append((str(a.activity_id), str(a.name)[:38], st, dur, rem,
                    a.percent_complete))
print(f"activities whose predecessors are all complete, now assumed started: "
      f"{len(started)}")
print(f"   skipped, the hand-off falls after the data date: {skipped}")
print(f"   of those started, {len(overdue_handoff)} have been open longer than "
      f"their own duration -- shown at 95%, they want a real call")
print(f"\n{'activity':<26}{'name':<40}{'started':<12}{'dur':>4}{'rem':>5}{'%':>7}")
for i, n, s, d, r, pc in sorted(started, key=lambda x: x[2])[:26]:
    print(f"{i:<26}{n:<40}{str(s):<12}{d:>4}{r:>5}{pc:>7.0f}")
if len(started) > 26:
    print(f"... and {len(started) - 26} more")

# ===== verify =========================================================
A1 = collections.Counter(a.status for a in p.activities)
bad = sum(1 for a in p.activities
          if (D(a.actual_finish) and D(a.actual_finish) > DD)
          or (D(a.actual_start) and D(a.actual_start) > DD))
oos = 0
for r in p.relations:
    a, b = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
    if a and b and a.status == "Not Started" and b.status in ("Completed",
                                                              "In Progress"):
        oos += 1
print("\n" + "=" * 76)
print(f"{'':<22}{'before':>10}{'after':>10}")
for s in ("Completed", "In Progress", "Not Started"):
    print(f"{s:<22}{B0.get(s, 0):>10}{A1.get(s, 0):>10}")
print(f"\nactuals past the data date: {bad}")
print(f"out-of-sequence (successor moving, predecessor not started): {oos}")
print(f"in progress with no remaining duration: "
      f"{sum(1 for a in p.activities if a.status == 'In Progress' and (a.remaining_duration or 0) <= 0)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v17.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
