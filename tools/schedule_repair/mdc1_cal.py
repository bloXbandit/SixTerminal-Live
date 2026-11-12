"""Would a 6-day 10-hour calendar fix the April 2028 finish?

Measured rather than argued. The app carries the same CPM pass the Schedule
button runs, and it converts an activity's duration with ITS OWN calendar's
hours per day, so a calendar change is a real experiment, not an estimate.

Four runs, so the calendar's effect can be told apart from the two things
already known to be wrong with the file:

  A  as it stands, F9 only
  B  F9 with the stale Start On constraints cleared
  C  F9 on a 6-day 10-hour calendar
  D  both

Scenario A is the control: it is what pressing Schedule today would give.
"""
import collections
import copy
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Calendar, compute_dates
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
HARD = ("start on", "mandatory start", "must start on")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


base = load_xml(f"{SCR}/MDC1_logic_repaired_v20.xml")
DD = D(base.data_date)
print(f"MDC-1 v20   data date {DD}   "
      f"{len(base.activities)} activities  {len(base.relations)} relations")

stale = [a for a in base.activities
         if (a.constraint_type or "").strip().lower() in HARD
         and D(a.constraint_date) and D(a.constraint_date) < DD
         and a.status != "Completed"]
print(f"stale hard start constraints dated before the data date: {len(stale)}")
cc = collections.Counter((a.constraint_type or "").strip() for a in stale)
for k, v in cc.most_common():
    print(f"   {v:>4}  {k}")


def tail(p, label):
    """Where the remaining work lands, and how it is spread."""
    rem = [a for a in p.activities
           if a.status != "Completed"
           and not str(a.activity_id).startswith("MDC1.LAB")
           and not WBO.search(str(a.name))]
    fins = [D(a.early_finish) for a in rem if D(a.early_finish)]
    last = max(fins)
    wk = collections.Counter()
    for a in rem:
        u = a.remaining_labor_units or 0
        if not u:
            continue
        s, f = D(a.early_start), D(a.early_finish)
        if not s:
            continue
        if not f or f < s:
            f = s
        s = max(s, DD)
        if f < s:
            f = s
        cal = next((c for c in p.calendars if c.uid == a.calendar_uid), None)
        wdays = (cal.work_days if cal else frozenset({0, 1, 2, 3, 4}))
        days = [s + dt.timedelta(days=i) for i in range((f - s).days + 1)]
        days = [d for d in days if d.weekday() in wdays] or [s]
        for d in days:
            wk[d + dt.timedelta(days=(5 - d.weekday()) % 7)] += u / len(days)
    pk = max(wk, key=wk.get) if wk else None
    hrs = sum(wk.values())
    first = wk.get(min(wk), 0) if wk else 0
    weeks = (last - DD).days / 7
    return {"label": label, "last": last, "weeks": weeks,
            "peak_wk": pk, "peak_h": wk[pk] if pk else 0,
            "peak_ppl": (wk[pk] / 50) if pk else 0,
            "first_wk_h": first, "hrs": hrs,
            "p90": sorted(wk.values())[int(len(wk) * .9)] if wk else 0,
            "n": len(rem)}


def run(clear_constraints, six_ten, label):
    p = copy.deepcopy(base)
    if clear_constraints:
        for a in p.activities:
            if (a.constraint_type or "").strip().lower() in HARD \
                    and D(a.constraint_date) and D(a.constraint_date) < DD \
                    and a.status != "Completed":
                a.constraint_type = None
                a.constraint_date = None
    if six_ten:
        days, hpd = six_ten
        cal = Calendar(uid="9001",
                       name=f"{len(days)}-DAY {hpd:g}-HOUR", hours_per_day=hpd,
                       hours_per_week=hpd * len(days),
                       hours_per_month=hpd * len(days) * 52 / 12,
                       hours_per_year=hpd * len(days) * 52, type="Project",
                       work_days=frozenset(days), holidays=frozenset())
        p.calendars = [cal] + list(p.calendars)
        for a in p.activities:
            if a.status != "Completed" \
                    and not str(a.activity_id).startswith("MDC1.LAB"):
                a.calendar_uid = cal.uid
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    return tail(p, label)


FIVE, SIX = range(5), range(6)
res = [run(False, False, "A  as it stands, F9 only"),
       run(True, False, "B  F9, stale constraints cleared"),
       run(False, (SIX, 10), "C  F9, 6-day 10-hour  (60 h/wk)"),
       run(True, (SIX, 10), "D  both"),
       run(True, (FIVE, 10), "E  F9, 5-day 10-hour  (50 h/wk)"),
       run(True, (SIX, 8), "F  F9, 6-day 8-hour   (48 h/wk)")]

print("\n" + "=" * 92)
print("WHAT THE SCHEDULE PASS ACTUALLY GIVES")
print("=" * 92)
print(f"{'scenario':<36}{'work ends':>12}{'weeks':>8}"
      f"{'peak week':>12}{'peak h/wk':>11}{'peak crew':>11}")
for r in res:
    hpw = 60 if "6-day 10" in r["label"] else (
        50 if "5-day 10" in r["label"] else 48 if "6-day 8" in r["label"] else 40)
    print(f"{r['label']:<36}{r['last'].strftime('%m/%d/%Y'):>12}"
          f"{r['weeks']:>8,.0f}{r['peak_wk'].strftime('%m/%d'):>12}"
          f"{r['peak_h']:>11,.0f}{r['peak_h'] / hpw:>11,.0f}")

a, b, c, d, e, f6 = res
print(f"\nCREW NEEDED vs CREW YOU HAVE (350 people)")
for r, hpw in ((a, 40), (d, 60), (e, 50), (f6, 48)):
    cap = 350 * hpw
    over = [1 for _ in range(1)]
    print(f"   {r['label']:<36} capacity {cap:>7,} h/wk   "
          f"peak needs {r['peak_h']:>7,.0f} h/wk = {r['peak_h'] / cap:>4.1f}x capacity")
print(f"\nthe calendar on its own   : {a['last']} -> {c['last']}  "
      f"({(a['last'] - c['last']).days} days earlier)")
print(f"the constraints on their own: {a['last']} -> {b['last']}  "
      f"({(a['last'] - b['last']).days} days earlier)")
print(f"both together             : {a['last']} -> {d['last']}  "
      f"({(a['last'] - d['last']).days} days earlier)")
print(f"\nremaining hours are {a['hrs']:,.0f} h in every case - a calendar "
      f"changes duration, never scope.")
print(f"at 350 people on a 50-hour week that is "
      f"{a['hrs'] / 17500:,.0f} weeks of labour "
      f"-> {(DD + dt.timedelta(weeks=a['hrs'] / 17500)):%m/%d/%Y}")
print(f"at 350 people on a 60-hour week that is "
      f"{a['hrs'] / 21000:,.0f} weeks of labour "
      f"-> {(DD + dt.timedelta(weeks=a['hrs'] / 21000)):%m/%d/%Y}")
