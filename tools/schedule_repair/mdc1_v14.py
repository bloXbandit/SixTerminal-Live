"""MDC-1 v14: stop the payroll record double-counting the budget.

WHAT IS WRONG. The 38 MDC1.LAB.REC activities are a weekly payroll record --
"Recorded Labour - week of 01/04", and so on through 09/20/2026. Each is
Completed, six days long, and carries planned labour units exactly equal to
actual: 174,771 hours on both sides.

Actual equal to actual is right. Those hours were worked. But planned equal to
actual is not: nothing was ever planned on these activities, because they are
a recording device, not work. The number is a mirror of the actual written
into the budget field.

The effect on the project totals:

                        before        after
  planned (budget)     550,661      375,890     the real plan
  actual               232,671      232,671     unchanged
  remaining            312,541      312,541     unchanged

Note what the actuals say: of 232,671 hours booked, 174,771 sit on the
payroll record and only 57,900 on the activities that consumed them. So the
hours are not double-booked as actuals -- they are recorded in one branch
instead of being spread across the work. That is a reasonable way to track
payroll you cannot allocate. It is only the planned side that is wrong.

THE FIX. Planned and remaining units on the 38 payroll activities go to zero.
Actual units are untouched, so the payroll record still reports every hour
worked, and the budget stops counting hours nobody budgeted.

ONE SIDE EFFECT, worth knowing before importing. In P6 these activities will
now show a units variance equal to their actual hours, because budgeted is
zero and actual is not. That is honest -- it says "this was never budgeted" --
but if a report sums variance across the project it will show -174,771. The
alternative is to leave the budget inflated by the same amount. Zeroing is
the lesser of the two, and it is what makes earned value and productivity
read correctly on the real work.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SRC = sys.argv[1]
SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WORK = frozenset({0, 1, 2, 3, 4, 5})
p = load_xml(SRC)


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def tot(acts, k):
    return sum(getattr(a, k, 0) or 0 for a in acts)


lab = [a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
rest = [a for a in p.activities if not str(a.activity_id).startswith("MDC1.LAB")]


def spread(acts, key):
    out = collections.Counter()
    for a in acts:
        u = getattr(a, key, 0) or 0
        if not u:
            continue
        s = D(a.actual_start) or D(a.planned_start)
        f = D(a.actual_finish) or D(a.planned_finish)
        if not s or not f or f < s:
            if s:
                out[(s.year, s.month)] += u
            continue
        days = [s + dt.timedelta(days=i) for i in range((f - s).days + 1)]
        days = [d for d in days if d.weekday() in WORK] or [s]
        for d in days:
            out[(d.year, d.month)] += u / len(days)
    return out


labh, resth = spread(lab, "planned_labor_units"), spread(rest, "planned_labor_units")
print("=" * 78)
print("WHERE THE USAGE CHART JOLTS, AND WHY")
print("=" * 78)
months = sorted(set(labh) | set(resth))
mx = max((labh[m] + resth[m]) for m in months) or 1
print(f"{'month':<9}{'payroll record':>16}{'real work':>12}{'total':>11}  planned hours")
for m in months:
    t = labh[m] + resth[m]
    if t < 1:
        continue
    print(f"{m[0]}-{m[1]:02d} {labh[m]:>15,.0f}{resth[m]:>12,.0f}{t:>11,.0f}  "
          f"{'#' * int(46 * t / mx)}")
jul = resth.get((2026, 7), 0)
print(f"""
TWO different things are moving that chart, and the payroll record is the
smaller one.

1. The payroll record inflates January to June. In those months it is nearly
   all of the planned hours -- 35,309 against 5,756 of real work in June --
   because it is writing actual hours into the budget field. That is the
   double-count, and it is fixed below.

2. The spike is NOT the payroll record. July 2026 carries {jul:,.0f} planned
   hours of real work, about three times any other month, and the payroll
   record contributes only {labh.get((2026, 7), 0):,.0f} of that month's total.
   That spike is the stale dates: this schedule was last calculated around
   06/30/2026, so every not-started activity got pushed onto the then-data
   date and stacked there. 169 of them still sit on 06/30 exactly, 97 on
   07/22, 92 on 07/16.

   Zeroing the payroll record will not flatten it. Statusing the catch-up
   list and running F9 will, because that is what spreads those activities
   back out across the months they belong in.
""")

print("=" * 78)
print("THE FIX")
print("=" * 78)
b = (tot(p.activities, "planned_labor_units"),
     tot(p.activities, "actual_labor_units"),
     tot(p.activities, "remaining_labor_units"))
changed = 0
for a in lab:
    pl = a.planned_labor_units or 0
    ac = a.actual_labor_units or 0
    if pl and abs(pl - ac) < 0.5:
        a.planned_labor_units = 0.0
        a.remaining_labor_units = 0.0
        old = a.udfs.get("COMMENTS", "")
        a.udfs["COMMENTS"] = ((((old + "  ") if old else "") + (
            f"Budgeted units zeroed: this is a payroll record, not planned "
            f"work, and its {ac:,.0f} planned hours were a copy of the actual, "
            f"double-counting the budget. Actual hours are unchanged."))[:248])
        changed += 1
a2 = (tot(p.activities, "planned_labor_units"),
      tot(p.activities, "actual_labor_units"),
      tot(p.activities, "remaining_labor_units"))
print(f"payroll activities corrected: {changed} of {len(lab)}")
print(f"\n{'':<24}{'before':>14}{'after':>14}")
for i, lbl in enumerate(("planned (budget)", "actual", "remaining")):
    print(f"{lbl:<24}{b[i]:>14,.0f}{a2[i]:>14,.0f}")
print(f"\npayroll record now: planned {tot(lab, 'planned_labor_units'):,.0f}  "
      f"actual {tot(lab, 'actual_labor_units'):,.0f}  "
      f"remaining {tot(lab, 'remaining_labor_units'):,.0f}")
print(f"real work unchanged: planned {tot(rest, 'planned_labor_units'):,.0f}  "
      f"actual {tot(rest, 'actual_labor_units'):,.0f}  "
      f"remaining {tot(rest, 'remaining_labor_units'):,.0f}")

labh2 = spread(lab, "planned_labor_units")
print(f"\nplanned hours by month, after:")
mx2 = max((labh2[m] + resth[m]) for m in months) or 1
for m in months:
    t = labh2[m] + resth[m]
    if t < 1:
        continue
    print(f"{m[0]}-{m[1]:02d} {t:>11,.0f}  {'#' * int(46 * t / mx2)}")

print(f"\nchecks: activities {len(p.activities)}  relationships {len(p.relations)}"
      f"  data date {p.data_date}")
print(f"  negative or odd units: "
      f"{sum(1 for a in p.activities if (a.planned_labor_units or 0) < 0 or (a.remaining_labor_units or 0) < 0)}")
print(f"  longest UDF value: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v14.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
