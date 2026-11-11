"""MDC-1 v20: make the actual labour record real, and let the remaining
hours mean something.

Baselines are out of this entirely. Two things only: what has been spent,
and what is left.

1. THE OCTOBER ACTUALS. v19 filled five missing weeks by scaling the JER
   daily reports by 1.75. Against the manager's own onsite record those
   five weeks came to 13,092 hours where he reports 66,750 -- 20%. The
   daily reports were never a sample of the workforce; they are a handful
   of foremen out of a crew that reached 350. So the scaling is thrown out
   and the manager's onsite hours go in at face value for the four weeks
   he covers. His record stops at 10/25 and the data date is 10/31, so that
   last week carries the 10/25 rate forward, which is the only assumption
   left in the labour record and is labelled as one.

   Worth keeping in view: over the 22 weeks where both records are real,
   JER payroll runs 66% of his onsite column, and the gap widens from 97%
   in early May to 24% by 09/20 -- right before our record stops. A
   definitional difference would hold roughly steady. A widening gap that
   ends in a cliff is a feed that stopped being filled in, so his column
   is the better record of the recent past and ours is the better record
   of the spring.

2. THE REMAINING HOURS. 374 completed activities still carry remaining
   labour -- 49,549 hours of work that is finished and still booked as
   ahead of us -- and 48 in-progress activities carry a remaining figure
   that does not match their percent complete. That is what makes the file
   claim 312,035 hours left against a 338,091 hour budget of which 237,313
   is already spent: 160% of the budget, which cannot be.

   Completed means nothing remains. In progress means budget times the
   share not yet done. Neither invents an actual hour: JER books its spend
   on the weekly payroll branch, and earning hours onto the work as well
   would count the same labour twice -- which is exactly the double count
   that was taken off this file once already.
"""
import collections
import datetime as dt
import sys

import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")


def D(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ---- the manager's onsite record ------------------------------------
wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
their = {}
for r in wb["MDC1"].iter_rows(min_row=2, values_only=True):
    d = D(r[0])
    if d and num(r[4]):
        their[d] = (num(r[3]), num(r[4]))
wb.close()
LASTREC = max(their)

p = load_xml(f"{SCR}/MDC1_logic_repaired_v19.xml")
DD = D(p.data_date)
work = [a for a in p.activities
        if not str(a.activity_id).startswith("MDC1.LAB")]
lab = [a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
B = (sum(a.planned_labor_units or 0 for a in work),
     sum(a.actual_labor_units or 0 for a in p.activities),
     sum(a.remaining_labor_units or 0 for a in work))

# ===== 1. October actuals ============================================
print("=" * 78)
print("1. THE OCTOBER ACTUALS")
print("=" * 78)
est = sorted((a for a in lab if ".EST." in str(a.activity_id)),
             key=lambda a: D(a.actual_finish))
print(f"estimated weeks in v19: {len(est)}")
print(f"{'week ending':<14}{'v19 booked':>12}{'manager':>12}{'people':>9}  source")
rep = []
for a in est:
    w = D(a.actual_finish)
    rec = their.get(w) or their.get(w + dt.timedelta(days=1))
    old = a.actual_labor_units or 0
    if rec:
        ppl, hrs = rec
        a.actual_labor_units = round(hrs, 1)
        a.udfs["COMMENTS"] = (
            f"Actual hours from the MDC-1 onsite labour record: {ppl:,.0f} "
            f"people, {hrs:,.0f} hours. Replaces a v19 estimate of "
            f"{old:,.0f} h scaled off JER daily reports, which cover only a "
            f"few foremen and understated this week by "
            f"{(1 - old / hrs):.0%}.")[:248]
        src = "manager's onsite record"
    else:
        ppl, hrs = their[LASTREC]
        a.actual_labor_units = round(hrs, 1)
        a.udfs["COMMENTS"] = (
            f"ESTIMATE. The onsite labour record stops at {LASTREC:%m/%d} and "
            f"the data date is {DD:%m/%d}, so this week carries the "
            f"{LASTREC:%m/%d} rate of {ppl:,.0f} people / {hrs:,.0f} h. The "
            f"only assumption in the labour record - replace it when the "
            f"real week lands.")[:248]
        src = f"carried from {LASTREC:%m/%d} (ESTIMATE)"
    a.activity_id = str(a.activity_id).replace(".EST.", ".OCT.")
    a.name = f"Labour Record - week of {w - dt.timedelta(days=5):%m/%d/%Y}"
    print(f"{str(w):<14}{old:>12,.0f}{a.actual_labor_units:>12,.0f}"
          f"{a.actual_labor_units / 50:>9,.0f}  {src}")
    rep.append((w, old, a.actual_labor_units, src))
print(f"\nOctober actuals: {sum(r[1] for r in rep):,.0f} h  ->  "
      f"{sum(r[2] for r in rep):,.0f} h")

# ===== 2. remaining hours ============================================
print("\n" + "=" * 78)
print("2. THE REMAINING HOURS")
print("=" * 78)
cl = ip = 0
cl_h = ip_h = 0.0
for a in work:
    bu = a.planned_labor_units or 0
    rl = a.remaining_labor_units or 0
    if a.status == "Completed":
        if rl:
            cl += 1
            cl_h += rl
            a.remaining_labor_units = 0.0
    elif a.status == "In Progress":
        want = round(bu * (1 - min(max(a.percent_complete or 0, 0), 100) / 100), 1)
        if abs(rl - want) > 0.5:
            ip += 1
            ip_h += rl - want
            a.remaining_labor_units = want
    elif a.status == "Not Started" and bu and abs(rl - bu) > 0.5:
        a.remaining_labor_units = float(bu)
print(f"completed, remaining cleared : {cl:>5} activities  {cl_h:>10,.0f} h")
print(f"in progress, remaining retimed: {ip:>5} activities  {ip_h:>+10,.0f} h")

A = (sum(a.planned_labor_units or 0 for a in work),
     sum(a.actual_labor_units or 0 for a in p.activities),
     sum(a.remaining_labor_units or 0 for a in work))
print("\n" + "=" * 78)
print(f"{'':<26}{'v19':>14}{'v20':>14}")
print(f"{'budget (work)':<26}{B[0]:>14,.0f}{A[0]:>14,.0f}")
print(f"{'actual spent (all)':<26}{B[1]:>14,.0f}{A[1]:>14,.0f}")
print(f"{'remaining (work)':<26}{B[2]:>14,.0f}{A[2]:>14,.0f}")
print(f"{'spent + remaining':<26}{B[1] + B[2]:>14,.0f}{A[1] + A[2]:>14,.0f}")
print(f"{'   as % of budget':<26}{100 * (B[1] + B[2]) / B[0]:>13,.0f}%"
      f"{100 * (A[1] + A[2]) / A[0]:>13,.0f}%")

# the double count that is still in the file, reported not changed
labw = {D(a.actual_finish) for a in lab if D(a.actual_finish)}
lo, hi = min(labw), max(labw)
ov = [a for a in work if (a.actual_labor_units or 0)
      and D(a.actual_start) and lo <= D(a.actual_start) <= hi]
print(f"\nSTILL IN THE FILE, not changed here: {len(ov)} work activities carry "
      f"{sum(a.actual_labor_units or 0 for a in ov):,.0f} h of actual")
print(f"inside the payroll window {lo} .. {hi}, so those hours are counted "
      f"both there and")
print("on the weekly payroll branch. Worth settling, but it is a convention "
      "call, not an error.")

act = sum(a.actual_labor_units or 0 for a in lab)
print(f"\nlabour record now {act:,.0f} h over {len(lab)} weeks, "
      f"running to {max(labw)}  (data date {DD})")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v20.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
