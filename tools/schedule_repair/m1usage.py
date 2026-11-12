"""What curve does our file actually draw, and which series is the screenshot?"""
import collections
import copy
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import compute_dates
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WORK = frozenset({0, 1, 2, 3, 4, 5})


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


base = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
DD = D(base.data_date)
p = copy.deepcopy(base)
compute_dates(p, hold_unlinked_dates=False, apply_dates=True)


def spread(acts, attr, sa, fa):
    out = collections.Counter()
    for a in acts:
        u = getattr(a, attr, 0) or 0
        if not u:
            continue
        s, f = D(getattr(a, sa, None)), D(getattr(a, fa, None))
        if not s:
            continue
        if not f or f < s:
            f = s
        days = [s + dt.timedelta(days=i) for i in range((f - s).days + 1)]
        days = [d for d in days if d.weekday() in WORK] or [s]
        for d in days:
            out[d] += u / len(days)
    return out


def weekly(daily):
    w = collections.Counter()
    for d, v in daily.items():
        w[d + dt.timedelta(days=(5 - d.weekday()) % 7)] += v
    return w


lab = [a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
work = [a for a in p.activities
        if not str(a.activity_id).startswith("MDC1.LAB")]

series = {
    "ACTUAL - everything (payroll branch + work)":
        weekly(spread(p.activities, "actual_labor_units",
                      "actual_start", "actual_finish")),
    "ACTUAL - payroll branch only":
        weekly(spread(lab, "actual_labor_units",
                      "actual_start", "actual_finish")),
    "ACTUAL - work activities only (no payroll branch)":
        weekly(spread(work, "actual_labor_units",
                      "actual_start", "actual_finish")),
    "REMAINING - after the schedule pass":
        weekly(spread(work, "remaining_labor_units",
                      "early_start", "early_finish")),
}
print("WHAT OUR FILE DRAWS, WEEKLY")
print("=" * 94)
print(f"{'series':<52}{'total h':>12}{'peak h/wk':>12}{'peak week':>14}")
for k, w in series.items():
    if not w:
        print(f"{k:<52}{'0':>12}")
        continue
    pk = max(w, key=w.get)
    print(f"{k:<52}{sum(w.values()):>12,.0f}{w[pk]:>12,.0f}"
          f"{pk.strftime('%m/%d/%Y'):>14}")

print("\n" + "=" * 94)
print("THE SCREENSHOT PEAKS: blue about 280, green spike about 900, red about 350")
print("=" * 94)
a_all = series["ACTUAL - everything (payroll branch + work)"]
a_work = series["ACTUAL - work activities only (no payroll branch)"]
rem = series["REMAINING - after the schedule pass"]
for lbl, w, target in (("blue (actual)", a_all, 280),
                       ("blue if payroll branch is excluded", a_work, 280),
                       ("green (remaining)", rem, 900)):
    if not w:
        continue
    pk = max(w.values())
    print(f"   {lbl:<38}our peak {pk:>9,.0f} h/wk   "
          f"screenshot {target:>5}   ratio {pk / target:>7,.1f}x")

print("\nper DAY instead of per week (the chart's bars may be daily):")
for lbl, w in (("blue (actual, everything)", a_all),
               ("green (remaining)", rem)):
    if w:
        print(f"   {lbl:<38}peak {max(w.values()) / 6:>9,.0f} h/day")

print("\n" + "=" * 94)
print("THE SPIKE AT THE DATA DATE")
print("=" * 94)
ws = sorted(rem)
for w in ws[:8]:
    print(f"   {w}   {rem[w]:>10,.0f} h   = {rem[w] / 50:>6,.0f} people "
          f"at 50h{'   <-- first week after the data date' if w == ws[0] else ''}")
tot = sum(rem.values())
top4 = sum(sorted(rem.values(), reverse=True)[:4])
print(f"\nremaining total {tot:,.0f} h; the top 4 weeks hold {top4:,.0f} h "
      f"= {100 * top4 / tot:.0f}% of all remaining work")

print("\n" + "=" * 94)
print("ACTUAL LABOUR BEFORE THE DATA DATE, BY SOURCE")
print("=" * 94)
print(f"   payroll branch (MDC1.LAB.*) : "
      f"{sum(a.actual_labor_units or 0 for a in lab):>10,.0f} h "
      f"on {len(lab)} weekly records")
print(f"   work activities             : "
      f"{sum(a.actual_labor_units or 0 for a in work):>10,.0f} h "
      f"on {sum(1 for a in work if a.actual_labor_units)} activities")
print(f"   TOTAL                       : "
      f"{sum(a.actual_labor_units or 0 for a in p.activities):>10,.0f} h")
print(f"\n   budget on work activities   : "
      f"{sum(a.planned_labor_units or 0 for a in work):>10,.0f} h")
print(f"   of which on COMPLETED work  : "
      f"{sum(a.planned_labor_units or 0 for a in work if a.status == 'Completed'):>10,.0f} h"
      f"   <-- this is what a baseline would show before the data date")
