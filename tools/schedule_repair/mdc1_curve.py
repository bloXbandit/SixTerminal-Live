"""Would our MDC-1 P6 file draw the labour curve the manager's report shows?

The report has two charts, both weekly: workers and hours, each with a
baseline, an actual, and a P6 budgeted series. The actuals stop after 10/25.

This spreads our schedule's labour units across the days each activity runs
-- budgeted units over the planned window, actual units over the actual one
-- buckets them into the same week-ending periods the report uses, and puts
the two side by side.

Two things make an exact match impossible and are worth stating before the
numbers. We do not know which P6 file the manager's report is built from. And
our file's actual hours are not spread across the work: 174,771 of the
232,671 sit on 38 weekly payroll-record activities, which is where JER books
hours it cannot allocate. So our actual curve is really the payroll curve,
and that is the fair thing to compare against their actual series.
"""
import collections
import datetime as dt
import re
import sys

import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WORK = frozenset({0, 1, 2, 3, 4, 5})


def D(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


# ---- the manager's figures ------------------------------------------
wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
ws = wb["MDC1"]
rep = {}
for r in ws.iter_rows(min_row=2, values_only=True):
    d = D(r[0])
    if not d:
        continue
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0
    rep[d] = {"plab": num(r[1]), "phrs": num(r[2]),
              "olab": num(r[3]), "ohrs": num(r[4])}
wb.close()
weeks = sorted(rep)
nz = [d for d in weeks if rep[d]["phrs"] or rep[d]["ohrs"]]
print(f"report MDC1 sheet: {len(rep)} weeks, {nz[0]} .. {nz[-1]}")
print(f"   planned hours total : {sum(rep[d]['phrs'] for d in rep):,.0f}")
print(f"   onsite  hours total : {sum(rep[d]['ohrs'] for d in rep):,.0f}")
lastact = max((d for d in rep if rep[d]["ohrs"]), default=None)
print(f"   last week with onsite hours: {lastact}")

# ---- our schedule ---------------------------------------------------
p = load_xml(f"{SCR}/MDC1_logic_repaired_v18.xml")
DD = D(p.data_date)


def spread(acts, units, s_attr, f_attr):
    out = collections.Counter()
    for a in acts:
        u = getattr(a, units, 0) or 0
        if not u:
            continue
        s, f = D(getattr(a, s_attr, None)), D(getattr(a, f_attr, None))
        if not s:
            continue
        if not f or f < s:
            f = s
        days = [s + dt.timedelta(days=i) for i in range((f - s).days + 1)]
        days = [d for d in days if d.weekday() in WORK] or [s]
        per = u / len(days)
        for d in days:
            out[d] += per
    return out


def toweeks(daily):
    """bucket into the report's week-ending dates"""
    out = collections.Counter()
    for d, v in daily.items():
        w = next((x for x in weeks if x >= d), None)
        if w:
            out[w] += v
    return out


bud = toweeks(spread(p.activities, "planned_labor_units",
                     "early_start", "early_finish"))
act = toweeks(spread(p.activities, "actual_labor_units",
                     "actual_start", "actual_finish"))
lab = [a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
labact = toweeks(spread(lab, "actual_labor_units",
                        "actual_start", "actual_finish"))
print(f"\nour v18: budgeted {sum(bud.values()):,.0f} h   "
      f"actual {sum(act.values()):,.0f} h   "
      f"of which payroll record {sum(labact.values()):,.0f} h")

print("\n" + "=" * 96)
print("WEEKLY, ACTUALS SIDE  (the period both sides claim to cover)")
print("=" * 96)
print(f"{'week ending':<14}{'report planned':>16}{'ours budgeted':>15}"
      f"{'report onsite':>15}{'ours actual':>13}{'ours payroll':>14}")
show = [d for d in weeks if dt.date(2026, 6, 1) <= d <= dt.date(2026, 11, 30)]
for d in show:
    print(f"{str(d):<14}{rep[d]['phrs']:>16,.0f}{bud.get(d, 0):>15,.0f}"
          f"{rep[d]['ohrs']:>15,.0f}{act.get(d, 0):>13,.0f}"
          f"{labact.get(d, 0):>14,.0f}")

print("\n" + "=" * 96)
print("FORWARD LOOK  (where the report shows the green P6 budgeted bars)")
print("=" * 96)
print(f"{'week ending':<14}{'report planned':>16}{'ours budgeted':>15}  ratio")
fwd = [d for d in weeks if dt.date(2026, 12, 1) <= d <= dt.date(2027, 6, 30)]
for d in fwd[::2]:
    r, o = rep[d]["phrs"], bud.get(d, 0)
    print(f"{str(d):<14}{r:>16,.0f}{o:>15,.0f}  "
          f"{(o / r) if r else 0:>5.2f}x")

print("\n" + "=" * 96)
print("DOES THE SHAPE MATCH?")
print("=" * 96)
both = [d for d in weeks if rep[d]["phrs"] > 0 and bud.get(d, 0) > 0]
if both:
    rs = [rep[d]["phrs"] for d in both]
    os_ = [bud[d] for d in both]
    n = len(both)
    mr, mo = sum(rs) / n, sum(os_) / n
    cov = sum((rs[i] - mr) * (os_[i] - mo) for i in range(n))
    vr = sum((x - mr) ** 2 for x in rs) ** .5
    vo = sum((x - mo) ** 2 for x in os_) ** .5
    corr = cov / (vr * vo) if vr and vo else 0
    print(f"weeks where both have budgeted hours: {n} "
          f"({both[0]} .. {both[-1]})")
    print(f"   report mean {mr:,.0f} h/week   ours {mo:,.0f} h/week   "
          f"ours is {mo / mr:.2f}x")
    print(f"   week-to-week correlation of the two curves: {corr:+.2f}")
    pr = max(both, key=lambda d: rep[d]["phrs"])
    po = max(both, key=lambda d: bud[d])
    print(f"   report peaks {rep[pr]['phrs']:,.0f} h on {pr}")
    print(f"   ours peaks   {bud[po]:,.0f} h on {po}")
ra = [d for d in weeks if rep[d]["ohrs"] > 0]
oa = [d for d in weeks if labact.get(d, 0) > 0]
if ra and oa:
    print(f"\nactual hours: report runs {ra[0]} .. {ra[-1]} "
          f"({sum(rep[d]['ohrs'] for d in ra):,.0f} h)")
    print(f"              ours  runs {oa[0]} .. {oa[-1]} "
          f"({sum(labact.values()):,.0f} h)")
