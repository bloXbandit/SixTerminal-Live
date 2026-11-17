"""Verify the 237 hand-read WBS bands without re-reading them.

Every band was transcribed by eye off a render, so the failure mode to worry
about is a misread digit or month that still looks plausible. Re-reading
would just risk repeating the same mistake, so this checks the data against
itself instead.

Each band carries three numbers that are not independent: P6's At Completion
Duration is the count of WORKING days between start and finish. So for every
row, duration and the dates have to agree. A misread digit breaks the
identity; a correctly read row survives it. Holidays and any non-standard
calendar make the match approximate rather than exact, so the test is a
tolerance, and what matters is which rows fall far outside it.

Second check: a parent band must envelope its children. "Electrical Rooms -
Datacenter A" has to start no later than the earliest ER room under it and
finish no earlier than the latest.
"""
import datetime as dt
import sys

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
sys.path.insert(0, f"{SCR}/work")
from mdc3_1007_bands import BANDS                      # noqa: E402

MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def pd(s):
    s = s.replace(" A", "").strip()
    d, m, y = s.split("-")
    return dt.date(2000 + int(y), MON[m], int(d))


def workdays(a, b, week=5):
    """Working days inclusive: week=5 is Mon-Fri, week=6 is Mon-Sat."""
    if b < a:
        return -1
    n = 0
    d = a
    while d <= b:
        if d.weekday() < week:
            n += 1
        d += dt.timedelta(days=1)
    return n


# A first pass compared every band against a 5-day week and threw 63 rows.
# The pattern in them was the answer, not the problem.
#
# The positive differences cluster at +9 to +12 on bands spanning about a
# year, independently across dozens of unrelated rows. That is holidays: P6
# takes them out of the duration and a raw working-day count does not.
#
# The negative differences were more interesting. Long Lead Equipment
# Delivery stated 343 against 286 implied, Equipment Set 138 against 115,
# Admin Space 119 against 99 -- every one of them a ratio of almost exactly
# 1.200, which is six days over five. Those activities are on a SIX-DAY
# calendar. Not a transcription error, a different work week, and one worth
# carrying into the pricing on its own account.
#
# So a band is checked against both calendars, with an allowance for
# holidays that scales with the span, and only a row that fits NEITHER is
# treated as a suspect read.
HOLIDAYS_PER_YEAR = 10.0


def is_actual(s):
    # NOT `"A" in s` -- that matches the capital A in "Aug" and "Apr" and
    # flags every April and August date as an actual.
    return s.strip().endswith(" A")


rows = [(nm, dur, pd(s), pd(fl), is_actual(s), is_actual(fl), pg)
        for nm, dur, s, fl, pg in BANDS]

print("=" * 92)
print("CHECK 1  --  does At Completion Duration match the working days "
      "between the dates?")
print("=" * 92)
bad, fit5, fit6 = [], 0, 0
six = []
for nm, dur, s, f, sa, fa, pg in rows:
    yrs = (f - s).days / 365.25
    hol = HOLIDAYS_PER_YEAR * yrs
    w5, w6 = workdays(s, f, 5), workdays(s, f, 6)
    e5 = abs(w5 - hol - dur)                 # residual on a 5-day week
    e6 = abs(w6 - hol * 6 / 5 - dur)         # residual on a 6-day week
    tol = max(5.0, 4.0 + 6.0 * yrs)          # longer spans, looser tolerance
    if e5 <= tol and e5 <= e6:
        fit5 += 1
    elif e6 <= tol:
        fit6 += 1
        six.append((nm, dur, s, f, pg))
    else:
        bad.append((nm, dur, s, f, w5, w6, min(e5, e6), pg))

print(f"bands checked                 : {len(rows)}")
print(f"consistent on a 5-day week    : {fit5}")
print(f"consistent on a 6-day week    : {fit6}")
print(f"fits NEITHER -- suspect read  : {len(bad)}")

if six:
    print("\n" + "-" * 92)
    print("ON A SIX-DAY CALENDAR  (Holder has already priced premium time "
          "into these)")
    print("-" * 92)
    for nm, dur, s, f, pg in sorted(six, key=lambda x: -x[1]):
        print(f"   {nm[:44]:<46}{dur:>5}d   "
              f"{s.strftime('%d-%b-%y')} .. {f.strftime('%d-%b-%y')}  p{pg}")

if bad:
    print("\n" + "-" * 92)
    print("SUSPECT -- fits neither calendar, re-read these off the page")
    print("-" * 92)
    print(f"{'band':<40}{'stated':>7}{'5-day':>7}{'6-day':>7}{'resid':>7}"
          f"{'pg':>5}   dates")
    for nm, dur, s, f, w5, w6, e, pg in sorted(bad, key=lambda x: -x[6]):
        print(f"{nm[:38]:<40}{dur:>7}{w5:>7}{w6:>7}{e:>7.0f}{pg:>5}   "
              f"{s.strftime('%d-%b-%y')} .. {f.strftime('%d-%b-%y')}")

print("\n" + "=" * 92)
print("CHECK 2  --  does each parent band envelope its children?")
print("=" * 92)
BY = {nm: (dur, s, f) for nm, dur, s, f, *_ in rows}
FAMILIES = [
    ("Electrical Rooms - Datacenter A",
     ["ERR 202", "ER 208", "ER 209", "ER 210", "ER 211", "ER 212", "ER 213",
      "ER 214"]),
    ("Electrical Rooms - Datacenter B",
     ["ERR 201", "ER 201", "ER 202", "ER 203", "ER 204", "ER 205", "ER 206",
      "ER 207"]),
    ("Electrical Rooms - Datacenter C",
     ["ERR 101", "ER 101", "ER 102", "ER 103", "ER 104", "ER 105", "ER 106",
      "ER 107"]),
    ("HV / HVR / MV Rooms - Datacenter A",
     ["HVR Rooms - Datacenter A", "HV Rooms - Datacenter A",
      "MV Rooms - Datacenter A"]),
    ("HV / MV Rooms - Datacenter B",
     ["HV Rooms - Datacenter B", "MV Rooms - Datacenter B"]),
    ("HV / HVR / MV Rooms - Datacenter C",
     ["HV Rooms - Datacenter C", "MV Rooms - Datacenter C"]),
    ("Generator Rooms - Datacenter A",
     ["GEN 315", "GEN 316", "GEN 317", "GEN 318", "GEN 319", "GEN 320",
      "GEN 321", "GEN 322", "GEN 323", "GEN 325", "GEN 327"]),
    ("Generator Rooms - Datacenter B",
     ["GEN 324", "GEN 314", "GEN 312", "GEN 310", "GEN 308", "GEN 307",
      "GEN 306", "GEN 304", "GEN 302"]),
    ("Generator Rooms - Datacenter C",
     ["GEN 326", "GEN 327 (C)", "GEN 328", "GEN 313", "GEN 311", "GEN 309",
      "GEN 307 (C)", "GEN 305", "GEN 303", "GEN 301"]),
    ("Commissioning",
     ["Level 2 Commissioning", "Pre-Commissioning Verification (PH1)",
      "Commissioning / Closeout (PH1)", "Commissioning / Closeout (PH2)",
      "Commissioning / Closeout (PH3)"]),
    ("Data Hall & Gallery (DH 202)",
     ["Data Hall 202", "Galleries (GL 203 & GL 204)"]),
    ("Data Hall & Gallery (DH 201)",
     ["Data Hall 201 (Expanded)", "Galleries (GL 201 & GL 202)"]),
    ("Data Hall & Gallery (DH 101)",
     ["Data Hall 101", "Galleries (GL 101 & GL 102)"]),
]
prob = 0
for parent, kids in FAMILIES:
    if parent not in BY:
        print(f"   {parent}: PARENT NOT CAPTURED")
        prob += 1
        continue
    have = [k for k in kids if k in BY]
    miss = [k for k in kids if k not in BY]
    if not have:
        continue
    pd_, ps, pf = BY[parent]
    cs = min(BY[k][1] for k in have)
    cf = max(BY[k][2] for k in have)
    flags = []
    if ps > cs:
        flags.append(f"parent starts {(ps - cs).days}d AFTER first child")
    if pf < cf:
        flags.append(f"parent ends {(cf - pf).days}d BEFORE last child")
    status = "  ".join(flags) if flags else "ok"
    if flags:
        prob += 1
    print(f"   {parent[:44]:<46}{len(have):>2} kids   {status}")
    if miss:
        print(f"      (not captured: {', '.join(miss)})")

print(f"\nenvelope problems: {prob}")

print("\n" + "=" * 92)
print("CHECK 3  --  the actual dates should all sit before the 25-Sep-26 "
      "data date")
print("=" * 92)
DD = dt.date(2026, 9, 25)
late = [(nm, s, f, sa, fa) for nm, dur, s, f, sa, fa, pg in rows
        if (sa and s > DD) or (fa and f > DD)]
print(f"bands carrying an actual flag          : "
      f"{sum(1 for r in rows if r[4] or r[5])}")
print(f"actual dates AFTER the data date       : {len(late)}"
      f"   {'<- would be a misread' if late else '(correct)'}")
for nm, s, f, sa, fa in late:
    print(f"   {nm:<44}{s} {'A' if sa else ''}  {f} {'A' if fa else ''}")

fut = [(nm, s, f) for nm, dur, s, f, sa, fa, pg in rows
       if not sa and s < DD]
print(f"non-actual starts BEFORE the data date : {len(fut)}"
      f"   {'<- check these' if fut else '(correct)'}")
for nm, s, f in fut[:10]:
    print(f"   {nm:<44}{s} .. {f}")
