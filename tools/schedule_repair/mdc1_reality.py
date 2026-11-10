"""Does what the crews report doing match what the MDC-1 schedule says is next?

The daily reports cover 09/17 to 11/10 -- 175 of them for MDC-1, 128 after the
payroll record stops and 17 inside the 10/24-10/31 week the GC never statused.
This does not change the schedule. It asks one question: in the window the
schedule is being updated to, is the work the field describes the same work
the schedule has queued?

Report text is free-form, so both sides are reduced to a vocabulary of trade
terms -- conduit, tray, pulls, terminations, fire alarm, lighting, heat trace,
gear, boxes, supports -- and the two frequency profiles are compared. Where
the field reports a kind of work the schedule has nothing open for, that is
either scope the schedule is missing or work running ahead of its logic.
Either way it is worth seeing before a lookahead is built on it.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
DD = dt.date(2026, 10, 31)
R = json.load(open(f"{SCR}/work/mdc1_daily.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v15_final.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}


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


VOCAB = [
    ("conduit / EMT", r"conduit|\bemt\b|\bpvc\b|rigid|\bpipe run"),
    ("boxes & supports", r"\bbox(es)?\b|support|strut|hanger|clip|stem|prefab"),
    ("cable tray", r"cable tray|\btray\b|ladder"),
    ("wire pull", r"\bpull|pulling|wire\b"),
    ("terminations", r"\bterm\b|terminat|land(ing)? wire|connections"),
    ("fire alarm", r"fire alarm|\bFA\b|vesda|strobe|horn|smoke detect|\bFACP\b"),
    ("lighting", r"\blight|fixture|lamp"),
    ("heat trace", r"heat.?trace"),
    ("gear / equipment", r"switchgear|\bgear\b|panel|\bMSB\b|\bLBB\b|load bank|"
                        r"transformer|\bXFMR\b|\bRMU\b|skid|breaker|\bATS\b"),
    ("grounding", r"ground|\bgnd\b|bond"),
    ("devices / trim", r"receptacle|device|trim|cover plate|\bdisco\b"),
    ("telecom", r"telecom|\bdata\b cable|fiber"),
    ("in-slab / underground", r"in.?slab|underground|stub.?up|ductbank|\bUG\b"),
    ("testing / QC", r"\bmeg\b|megger|test|inspect|\bQC\b|punch"),
]


def kinds(t):
    t = str(t)
    return {k for k, rx in VOCAB if re.search(rx, t, re.I)}


WIN = [("09/27 - 10/31 (the update window)", dt.date(2026, 9, 27), DD),
       ("11/01 - 11/10 (after the data date)", dt.date(2026, 11, 1),
        dt.date(2026, 11, 10))]

print("=" * 78)
print("1. WHAT THE FIELD REPORTS DOING")
print("=" * 78)
field = {}
for label, lo, hi in WIN:
    rs = [r for r in R if D(r["date"]) and lo <= D(r["date"]) <= hi]
    c = collections.Counter()
    for r in rs:
        for k in kinds(r["tasks"] + " " + r["comments"]):
            c[k] += 1
    field[label] = (rs, c)
    hrs = sum(float(r["hours"] or 0) for r in rs)
    print(f"\n{label}: {len(rs)} reports, {hrs:,.0f} hours")
    for k, v in c.most_common():
        print(f"   {v:>4} reports mention   {k}")

print("\n" + "=" * 78)
print("2. WHAT THE SCHEDULE HAS QUEUED IN THAT WINDOW")
print("=" * 78)
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
openwork = [a for a in p.activities
            if a.status != "Completed" and not WBO.search(str(a.name))]
# what the schedule thinks should be running: started, or due to start by now
due = [a for a in openwork
       if (a.status == "In Progress")
       or (D(a.planned_start) and D(a.planned_start) <= DD)]
sc = collections.Counter()
for a in due:
    for k in kinds(a.name):
        sc[k] += 1
print(f"\nunfinished JER activities due to have started by {DD}: {len(due)}")
for k, v in sc.most_common():
    print(f"   {v:>4} activities   {k}")

print("\n" + "=" * 78)
print("3. SIDE BY SIDE - does the field match the plan?")
print("=" * 78)
_, fc = field[WIN[0][0]]
nrep = len(field[WIN[0][0]][0])
print(f"{'work type':<26}{'% of reports':>14}{'activities queued':>19}  verdict")
allk = sorted(set(fc) | set(sc), key=lambda k: -fc.get(k, 0))
for k in allk:
    f = 100 * fc.get(k, 0) // max(nrep, 1)
    s = sc.get(k, 0)
    if fc.get(k, 0) and not s:
        v = "FIELD BUSY, NOTHING QUEUED"
    elif s and not fc.get(k, 0):
        v = "queued, not reported"
    elif f >= 20 and s < 10:
        v = "field heavy, schedule thin"
    else:
        v = "aligned"
    print(f"{k:<26}{f:>13}%{s:>19}  {v}")

print("\n" + "=" * 78)
print("4. THE UNSTATUSED WEEK, 10/24 - 10/31")
print("=" * 78)
wk = [r for r in R if D(r["date"]) and dt.date(2026, 10, 24) <= D(r["date"]) <= DD]
print(f"{len(wk)} reports, {sum(float(r['hours'] or 0) for r in wk):,.0f} hours, "
      f"foremen: {', '.join(sorted({str(r['foreman']) for r in wk}))}")
for r in sorted(wk, key=lambda x: str(x["date"])):
    t = re.sub(r"\s+", " ", r["tasks"])[:118]
    print(f"\n   {r['date']}  {str(r['foreman'])[:18]:<20}{r['workers']}w "
          f"{r['hours']}h  {r['status']}")
    print(f"      {t}")

print("\n" + "=" * 78)
print("5. ROOMS AND AREAS THE FIELD NAMES")
print("=" * 78)
RM = re.compile(r"\b(ER|ERR|MV|HV|GEN|GL|DH|ELEC|POE|VAL|MECH)[\s\-]?(\d{3})\b", re.I)
rooms = collections.Counter()
for r in R:
    d = D(r["date"])
    if not d or d < dt.date(2026, 9, 27):
        continue
    for m in RM.finditer(r["tasks"] + " " + r["comments"]):
        rooms[f"{m.group(1).upper()} {m.group(2)}"] += 1
print(f"rooms named in reports since 09/27: {dict(rooms.most_common(16))}")
areas = collections.Counter()
AR = re.compile(r"(data hall|gallery|roof|cup\b|penthouse|generator|"
                r"north ?east|north ?west|south ?east|south ?west|"
                r"\b[1-4](st|nd|rd|th) floor|level [1-4]|segment [a-h])", re.I)
for r in R:
    d = D(r["date"])
    if not d or d < dt.date(2026, 9, 27):
        continue
    for m in AR.finditer(r["tasks"] + " " + r["comments"]):
        areas[m.group(1).lower().replace("  ", " ")] += 1
print(f"\nareas named: {dict(areas.most_common(16))}")
