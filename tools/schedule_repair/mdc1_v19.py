"""MDC-1 v19: fix the ten bad labour loadings, and fill the payroll gap.

1. THE LOADINGS. Ten Equipment Layout/Isolator Install activities carry
   thousands of budgeted hours against a one-day duration -- 3,978 h on nine
   of them, 2,778 on the tenth, which is 497 and 347 people for a single day.

   Their peers settle it. Eighteen other activities with the same name, the
   same one-day duration and the same eight electricians carry 78 hours; the
   six-electrician version carries 60. So 78 is what these should be, and
   3,978 looks like 78 with a 39 typed in front of it.

   Those ten are 10.3% of the whole budget and nine of them land in the same
   week, which is the entire false peak in July.

2. THE PAYROLL GAP. The record stops at the week of 09/20 while the data date
   is 10/31, so the actual curve dies five weeks early and never reaches the
   data date the way the manager's does.

   The daily reports can fill it, but only after throwing out six of them.
   Those six -- all one foreman -- book 88, 100 and in one case 1,000 hours
   per worker per day, and between them carry 64,900 of the 80,620 hours in
   the whole set. One says 47 workers logged 47,000 hours in a day. The other
   168 reports sit between 4 and 16 hours a worker, which is a shift.

   The cleaned reports still under-count payroll, because not every foreman
   files every day: on the two weeks where both sources exist they come to
   roughly three-quarters of the recorded payroll. So each new week is written
   up by that ratio and labelled an estimate on the activity itself. These are
   NOT payroll records and should be replaced when the real numbers arrive.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Activity
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
R = json.load(open(f"{SCR}/work/mdc1_daily.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v18.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


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
B = (sum(a.planned_labor_units or 0 for a in p.activities),
     sum(a.actual_labor_units or 0 for a in p.activities))

# ===== 1. the loadings ================================================
print("=" * 76)
print("1. THE TEN BAD LOADINGS")
print("=" * 76)
eq = [a for a in p.activities if "Equipment Layout/Isolator" in str(a.name)]
peer = collections.defaultdict(list)
for a in eq:
    ne = str((a.udfs or {}).get("Number of Electricians", "")).strip()
    d = round((a.planned_duration or 8) / 8)
    u = a.planned_labor_units or 0
    if u and u / max(d, 1) <= 200:
        peer[(ne, d)].append(u)
print("what the healthy peers carry:")
for k, v in sorted(peer.items()):
    print(f"   {k[0]} electricians, {k[1]}d  ->  "
          f"{sorted(set(v))} h   ({len(v)} activities)")
fixed = []
for a in eq:
    u = a.planned_labor_units or 0
    d = round((a.planned_duration or 8) / 8)
    if not u or u / max(d, 1) <= 200:
        continue
    ne = str((a.udfs or {}).get("Number of Electricians", "")).strip()
    cand = peer.get((ne, d))
    if not cand:
        print(f"   {a.activity_id}: no peer to copy, left alone")
        continue
    good = max(set(cand), key=cand.count)
    old = a.planned_labor_units
    a.planned_labor_units = float(good)
    if a.status == "Completed":
        a.actual_labor_units = float(good)
        a.remaining_labor_units = 0.0
    elif a.status == "Not Started":
        a.remaining_labor_units = float(good)
    else:
        a.remaining_labor_units = float(good) * (
            (a.remaining_duration or 0) / max(a.planned_duration or 1, 1))
    c = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
        f"Budgeted units corrected from {old:,.0f} h to {good:,.0f}: "
        f"{old:,.0f} over a {d}-day duration is {old / max(d, 1):,.0f} hours a "
        f"day. {len(cand)} activities with the same name, duration and "
        f"electrician count carry {good:,.0f}."))[:248])
    fixed.append((str(a.activity_id), old, good))
print(f"\ncorrected {len(fixed)}:")
for i, o, g in fixed:
    print(f"   {i:<24}{o:>9,.0f} h  ->  {g:>6,.0f} h")
print(f"removed from the budget: {sum(o - g for _, o, g in fixed):,.0f} h")

# ===== 2. the payroll gap =============================================
print("\n" + "=" * 76)
print("2. THE FIVE MISSING PAYROLL WEEKS")
print("=" * 76)
rows = []
for r in R:
    d, w, h = D(r["date"]), float(r["workers"] or 0), float(r["hours"] or 0)
    if not d or h <= 0:
        continue
    if w > 0 and h / w > 16:
        continue          # impossible for one shift
    rows.append((d, h))
drop = sum(1 for r in R if D(r["date"]) and float(r["hours"] or 0) > 0) - len(rows)
print(f"daily reports used: {len(rows)}   thrown out as impossible: {drop}")


def weekend(d):
    return d + dt.timedelta(days=(5 - d.weekday()) % 7)


daily = collections.Counter()
for d, h in rows:
    daily[weekend(d)] += h
lab = {D(a.actual_finish): a for a in p.activities
       if str(a.activity_id).startswith("MDC1.LAB") and D(a.actual_finish)}
both = [w for w in daily if w in lab]
ratio = 1.0
if both:
    s_rep = sum(daily[w] for w in both)
    s_pay = sum(lab[w].actual_labor_units or 0 for w in both)
    ratio = s_pay / s_rep if s_rep else 1.0
    print(f"\nweeks where both sources exist: {len(both)}")
    for w in sorted(both):
        print(f"   {w}   payroll {lab[w].actual_labor_units:>8,.0f}   "
              f"reports {daily[w]:>8,.0f}")
    print(f"reports are {s_rep / s_pay:.0%} of payroll on those weeks, "
          f"so each new week is scaled by {ratio:.2f}")
last = max(lab)
cal = sorted({a.calendar_uid for a in p.activities},
             key=lambda c: -sum(1 for a in p.activities if a.calendar_uid == c))[0]
parent = next((w for w in p.wbs_nodes if "Labour Record" in w.name), None)
nuid = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
added = []
for w in sorted(daily):
    if w <= last or w > DD or parent is None:
        continue
    est = daily[w] * ratio
    if est < 1:
        continue
    st = w - dt.timedelta(days=5)
    a = Activity(
        uid=str(nuid), activity_id=f"MDC1.LAB.EST.{w:%Y%m%d}",
        name=f"Estimated Labour - week of {st:%m/%d/%Y}",
        wbs_uid=parent.uid, calendar_uid=cal,
        activity_type="Task Dependent", status="Completed",
        planned_duration=48.0, remaining_duration=0.0, actual_duration=48.0,
        percent_complete=100.0,
        planned_start=st.isoformat(), planned_finish=w.isoformat(),
        actual_start=st.isoformat(), actual_finish=w.isoformat(),
        planned_labor_units=0.0, actual_labor_units=round(est, 1),
        remaining_labor_units=0.0)
    a.udfs["COMMENTS"] = (
        f"ESTIMATE, NOT PAYROLL. {daily[w]:,.0f} hours were reported in JER "
        f"daily reports this week, scaled by {ratio:.2f} because the reports "
        f"run short of recorded payroll. Replace with the real figure when it "
        f"arrives.")[:248]
    nuid += 1
    p.activities = list(p.activities) + [a]
    added.append((str(a.activity_id), st, w, daily[w], est))
print(f"\nweeks added: {len(added)}  (payroll record ended {last})")
print(f"{'activity':<26}{'week':<26}{'reported':>11}{'booked':>11}")
for i, s, w, rep, est in added:
    print(f"{i:<26}{f'{s:%m/%d} - {w:%m/%d}':<26}{rep:>11,.0f}{est:>11,.0f}")

A = (sum(a.planned_labor_units or 0 for a in p.activities),
     sum(a.actual_labor_units or 0 for a in p.activities))
print("\n" + "=" * 76)
print(f"{'':<26}{'before':>12}{'after':>12}")
print(f"{'budgeted hours':<26}{B[0]:>12,.0f}{A[0]:>12,.0f}")
print(f"{'actual hours':<26}{B[1]:>12,.0f}{A[1]:>12,.0f}")
af = [D(a.actual_finish) for a in p.activities if D(a.actual_finish)]
print(f"\nactual hours now run to {max(af)}   (data date {DD})")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v19.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
