"""MDC-1 v24: put Holder's forecast dates on the six new rooms.

v23 took Holder's ACTUAL dates onto the new rooms -- 45 starts and 40
finishes -- but set no forecast dates at all, so the 49 rows that have not
started carry no date of any kind and cannot be placed in a lookahead window.
That was read as an F9 problem; it is not. Holder's own export carries a
planned start for 171 of his rows in these rooms, running 10/26/26 to
03/31/27, and several fall inside the current two-week window.

So each not-started row takes his planned dates, matched the same way the
actuals were: by room and work stage, and where a stage spans his sub-areas
-- the galleries are split, 64 rows for two passes of 32 -- the earliest
start and the latest finish across them, because the stage is not done in
that room until the last sub-area is.

Both the planned and the early dates are set. P6 will recompute the early
dates on the next schedule run, which is the point: until then these rows
need a date to be reportable, and the only honest source for it is the GC's
own forecast. Each row says so in its COMMENTS.

The three data hall finish milestones get their early dates filled in the
same way, from the planned dates v23 already gave them.
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
R = json.load(open(f"{SCR}/work/mdc1_exs2.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v23.xml")
NEW = re.compile(r"^MDC1\.PH\d\.(ERR20[12]|HVR101|GAL(101|102|202))\.")
WORKDAYS = frozenset({0, 1, 2, 3, 4})


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


DD = D(p.data_date)


def toks(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
            if len(t) > 2}


def fwd(d, days):
    n = 0
    while n < max(int(days) - 1, 0):
        d += dt.timedelta(days=1)
        if d.weekday() in WORKDAYS:
            n += 1
    return d


ROOMRX = {"ERR201": r"ERR\s*201|ERR201", "ERR202": r"ERR\s*202|ERR202",
          "HVR101": r"HVR\s*101|HVR101", "GAL101": r"GAL101|GL101",
          "GAL102": r"GAL102|GL102", "GAL202": r"GAL202|GL202"}
hold = {}
for rm, rx in ROOMRX.items():
    hold[rm] = [d for d in R
                if re.search(rx, f"{d['Activity ID']} {d['Activity Name']}",
                             re.I)
                and not re.search(r"Level \d Cx", str(d["Activity Name"]))]

print("=" * 80)
print("FORECAST DATES ONTO THE NEW ROOMS")
print("=" * 80)
acts = [a for a in p.activities if NEW.search(str(a.activity_id))]
print(f"new-room activities: {len(acts)}   "
      f"not started: {sum(1 for a in acts if a.status == 'Not Started')}")
set_, nohit, log = 0, [], []
for a in acts:
    if a.status != "Not Started":
        continue
    m = NEW.search(str(a.activity_id))
    rm = m.group(1)
    gt = toks(a.name)
    hits = []
    for d in hold.get(rm, []):
        ht = toks(d["Activity Name"])
        if not ht or not gt:
            continue
        if any((w in gt) != (w in ht) for w in
               ("rough", "close", "term", "test", "pull", "set", "trim",
                "inspect", "startup", "hanger", "light", "vesda")):
            continue
        if len(ht & gt) / len(ht | gt) >= 0.5:
            hits.append(d)
    st = [D(d.get("Start")) for d in hits if D(d.get("Start"))]
    fi = [D(d.get("Finish")) for d in hits if D(d.get("Finish"))]
    if not st:
        nohit.append(a)
        continue
    s = min(st)
    f = max(fi) if fi else fwd(s, (a.planned_duration or 8) / 8)
    if f < s:
        f = fwd(s, (a.planned_duration or 8) / 8)
    a.planned_start = s.isoformat()
    a.planned_finish = f.isoformat()
    a.early_start = s.isoformat()
    a.early_finish = f.isoformat()
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
        f"Forecast dates {s:%m/%d/%y}-{f:%m/%d/%y} from Holder's 11/03 "
        f"export ({len(hits)} row(s) for this stage). Our own dates on the "
        f"next schedule run."))[:248])
    set_ += 1
    log.append((str(a.activity_id), str(a.name), s, f, len(hits)))
print(f"\ndated from Holder's forecast: {set_}")
print(f"{'activity':<24}{'name':<40}{'start':>12}{'finish':>12}{'rows':>6}")
for i, n, s, f, k in sorted(log, key=lambda x: x[2]):
    print(f"{i:<24}{n[:38]:<40}{str(s):>12}{str(f):>12}{k:>6}")
if nohit:
    print(f"\nno Holder row for this stage, left undated: {len(nohit)}")
    for a in nohit:
        print(f"   {str(a.activity_id):<24}{str(a.name)[:48]}")

# data hall milestones: fill the early dates from the planned ones
print("\n" + "=" * 80)
print("DATA HALL MILESTONES")
print("=" * 80)
for a in p.activities:
    if re.search(r"Complete - Level 3 Cx Done", str(a.name)):
        if a.planned_start and not a.early_start:
            a.early_start = a.planned_start
            a.early_finish = a.planned_finish or a.planned_start
        print(f"   {str(a.activity_id):<24}{str(a.name)[:44]:<46}"
              f"{D(a.early_start)}")

inwin = [x for x in log if DD <= x[2] <= DD + dt.timedelta(days=14)]
late = [x for x in log if x[2] < DD]
print("\n" + "=" * 80)
print(f"of the {set_} newly dated rows:")
print(f"   starting inside the 10/31-11/14 window : {len(inwin)}")
print(f"   already overdue at the data date       : {len(late)}")
print(f"   later than the window                  : {set_ - len(inwin) - len(late)}")
nd = [a for a in acts
      if not a.planned_start and not a.early_start and not a.actual_start]
print(f"\nnew-room rows still carrying no date at all: {len(nd)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v24.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
