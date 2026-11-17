"""The MDC-3 original contract baseline: dates from 02/04, hours from our file.

The Exhibit S carries dates and durations but no hours, and our P6 file
carries hours but has moved on from the contract. So the contract labour
curve is the one built from BOTH: each activity's budgeted hours out of our
file, spread across the dates the 02/04 contract gives it.

That is also the method for the comparison when the 10/7 arrives -- the same
hours against the new dates -- so any difference between the two curves is
the schedule change and nothing else. Holding the hours constant is the
point: it isolates acceleration from scope.
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
C = {r["id"]: r for r in json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))}
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
WORK = frozenset({0, 1, 2, 3, 4, 5})


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
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


WBO = re.compile(r"\*+\s*WBO|by others", re.I)
ours = {str(a.activity_id): a for a in p.activities}
print("=" * 90)
print("COVERAGE -- can our hours be placed on contract dates?")
print("=" * 90)
loaded = [a for a in p.activities if (a.planned_labor_units or 0) > 0]
TOT = sum(a.planned_labor_units or 0 for a in loaded)
print(f"our v32: {len(p.activities)} activities, {len(loaded)} carry hours, "
      f"{TOT:,.0f} h")
print(f"02/04 contract: {len(C)} activities")
shared = [a for a in loaded if str(a.activity_id) in C]
sh = sum(a.planned_labor_units or 0 for a in shared)
print(f"\nloaded activities with a 02/04 contract date: {len(shared)}  "
      f"({100 * len(shared) // max(len(loaded), 1)}%)")
print(f"   hours they carry: {sh:,.0f} h  ({100 * sh / TOT:.0f}% of ours)")
miss = [a for a in loaded if str(a.activity_id) not in C]
print(f"loaded activities NOT in the 02/04 contract: {len(miss)}  "
      f"{sum(a.planned_labor_units or 0 for a in miss):,.0f} h")
if miss:
    c = collections.Counter(path(a.wbs_uid).split(" / ")[0] for a in miss)
    for k, v in c.most_common(6):
        print(f"      {v:>5}  {k[:60]}")

# ---- the curve -------------------------------------------------------
def curve(datefn, acts):
    daily = collections.Counter()
    placed = 0.0
    for a in acts:
        u = a.planned_labor_units or 0
        if not u:
            continue
        s, f = datefn(a)
        if not s:
            continue
        if not f or f < s:
            f = s
        days = [s + dt.timedelta(days=i) for i in range((f - s).days + 1)]
        days = [d for d in days if d.weekday() in WORK] or [s]
        for d in days:
            daily[d] += u / len(days)
        placed += u
    wk = collections.Counter()
    for d, v in daily.items():
        wk[d + dt.timedelta(days=(5 - d.weekday()) % 7)] += v
    return wk, placed


def cdates(a):
    r = C[str(a.activity_id)]
    return D(r["start"]), D(r["finish"])


wk, placed = curve(cdates, shared)
ws = sorted(wk)
print("\n" + "=" * 90)
print("ORIGINAL CONTRACT LABOUR CURVE (02/04/2026 Exhibit S dates)")
print("=" * 90)
print(f"hours placed : {placed:,.0f}")
print(f"span         : {ws[0]} .. {ws[-1]}  ({len(ws)} weeks)")
pk = max(wk, key=wk.get)
print(f"peak week    : {pk}  {wk[pk]:,.0f} h  = {wk[pk] / 50:,.0f} people "
      f"at 50 h")
print(f"median week  : {sorted(wk.values())[len(ws) // 2]:,.0f} h")
print(f"mean week    : {placed / len(ws):,.0f} h")
print(f"\n{'month':<10}{'hours':>12}{'avg people @50h':>18}")
mo = collections.Counter()
mc = collections.Counter()
for w, v in wk.items():
    mo[(w.year, w.month)] += v
    mc[(w.year, w.month)] += 1
for k in sorted(mo):
    print(f"{k[1]:02d}/{k[0]}   {mo[k]:>12,.0f}{mo[k] / mc[k] / 50:>18,.0f}")

print("\n" + "=" * 90)
print("THE CONTRACT MILESTONES JER IS HELD TO")
print("=" * 90)
KEY = ["MDC3.MIL.1210", "MDC3.MIL.1220", "MDC3.MIL.1340", "MDC3.MIL.1370",
       "MDC3.MIL.1380", "MDC3.MIL.1150", "MDC3.MIL.1240", "MDC3.MIL.1270",
       "MDC3.MIL.1410", "MDC3.MIL.1280",
       "MDC3.MIL.PH1.1000", "MDC3.MIL.PH1.1010", "MDC3.MIL.PH1.1020",
       "MDC3.MIL.PH1.1030", "MDC3.MIL.PH1.1050", "MDC3.MIL.PH1.1060",
       "MDC3.MIL.PH2.9000", "MDC3.MIL.PH2.9010", "MDC3.MIL.PH2.9020",
       "MDC3.MIL.PH2.9030", "MDC3.MIL.PH2.9060", "MDC3.MIL.PH2.9040",
       "MDC3.MIL.PH3.9000", "MDC3.MIL.PH3.9020", "MDC3.MIL.PH3.9010",
       "MDC3.MIL.PH3.9030", "MDC3.MIL.PH3.9060", "MDC3.MIL.PH3.9040",
       "MDC3.MIL.1140", "MDC3.MIL.1070", "MDC3.MIL.1150", "MDC3.MIL.1320"]
seen = set()
for k in KEY:
    if k in seen or k not in C:
        continue
    seen.add(k)
    r = C[k]
    d = r["finish"] or r["start"]
    fl = r["finish_flag"] or r["start_flag"]
    print(f"   {k:<22}{r['name'][:46]:<48}{d}{'  ' + fl if fl else ''}")

print("\n" + "=" * 90)
print("JER ELECTRICAL SCOPE INSIDE THE 02/04 CONTRACT")
print("=" * 90)
ELEC = re.compile(
    r"conduit|raceway|\btray\b|wire|\bpull|\bterm\b|terminat|switchgear|"
    r"panel|\blight|device|feeder|busway|bus duct|ground|fire alarm|\bFA\b|"
    r"vesda|\bUPS\b|\bEDP\b|transformer|\bXFMR\b|\bRMU\b|\bGIS\b|skid|"
    r"energi|start.?up|isolator|rough.?in|high steel|hanger|trim.?out|"
    r"electrical", re.I)
el = [r for r in C.values() if ELEC.search(r["name"])]
ds = [D(r["start"]) for r in el if D(r["start"])]
df = [D(r["finish"]) for r in el if D(r["finish"])]
print(f"electrical-named activities in the contract: {len(el)} of {len(C)}")
print(f"   they run {min(ds)} .. {max(df)}")
print(f"   total duration: {sum(r['dur'] or 0 for r in el):,} activity-days")
json.dump({str(w): round(v, 1) for w, v in wk.items()},
          open(f"{SCR}/work/mdc3_curve_0204.json", "w"), indent=0)
print(f"\nwrote {SCR}/work/mdc3_curve_0204.json")
