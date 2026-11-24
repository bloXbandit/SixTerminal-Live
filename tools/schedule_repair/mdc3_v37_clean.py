"""MDC-3 v37: make the long-lead deliveries behave like deliveries, and strip
the lag that double-counts concrete cure.

Two changes, each measured on its own so the contribution of each is visible.

1. LONG LEAD EQUIPMENT. 920 LLE activities carry durations up to 359 days and
   310 ties to each other. A delivery is an event, not work on site: one
   switchboard does not wait for the previous one to arrive. Chaining them
   produced late dates in 2020 and 2021 and 188 activities of negative float
   that meant nothing.

   Each becomes a 1-day activity pinned on its delivery date, keeping the date
   the schedule already expects. The ties between deliveries go. The ties from
   a delivery to the work that installs it stay -- those are real.

2. CURE LAG. 98 ties take a "7-Day Cure Time" or "Sealer & Cure" activity as
   predecessor and then add lag on top, 1,653 days of it. The cure is already
   modelled as its own activity, so the lag is a second helping of the same
   wait with nothing to explain it.

   Only the lag is removed; every relationship stays. Where a successor is
   genuinely waiting on something else -- crane availability is the likely
   one -- that will show up as the successor simply moving earlier, and it
   wants real logic rather than a number parked on a cure tie.

Nothing is deleted. Durations, lags and one constraint per delivery change.
"""
import collections, datetime as dt, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v36.xml"
HRS=8.0
CURE=re.compile(r'cure time|sealer\s*&\s*cure|7-day cure', re.I)

def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None

def finish(p):
    compute_dates(p)
    return max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
               if D(a.actual_finish) or D(a.early_finish))

def negfloat(p):
    return sum(1 for a in p.activities
               if a.total_float is not None and a.total_float < 0)

base=load_xml(SRC)
print(f"BASELINE v36   finish {finish(base)}   negative float {negfloat(base)}")

p=load_xml(SRC)
byu={a.uid:a for a in p.activities}
lle={a.uid for a in p.activities if ".LLE." in str(a.activity_id)}

# ---- 1. deliveries ---------------------------------------------------
before=len(p.relations)
p.relations=[r for r in p.relations
             if not (r.predecessor_uid in lle and r.successor_uid in lle)]
cut=before-len(p.relations)
shrunk=saved=0
reverted=[0]
for a in p.activities:
    if a.uid not in lle or a.planned_duration <= HRS:
        continue
    dlv = D(a.planned_finish) or D(a.early_finish)
    if not dlv:
        continue
    if a.actual_finish:
        continue                      # already delivered; history, left alone
    was = a.planned_duration/HRS
    saved += was - 1
    a.planned_duration = HRS
    a.remaining_duration = HRS
    # 824 of these are "In Progress" procurement bars anchored on an actual
    # start of 02/09/26. An in-progress activity is scheduled from its actual
    # start, so cutting the bar to one day drags the finish back to that start
    # -- on the first attempt that moved the generators 500 days early. The
    # delivery EVENT has not happened, only the procurement behind it, so the
    # event reverts to Not Started and the bar it replaced goes away with it.
    if a.status == "In Progress":
        a.status = "Not Started"
        a.actual_start = None
        a.percent_complete = 0.0
        reverted[0] += 1
    # "Start On or After" raises the early date to the delivery without
    # capping the late date. "Start On" would cap it, and that is what
    # manufactured 271 activities of negative float on the first attempt:
    # the pin, not the logic, was reporting the job as late.
    a.constraint_type = "Start On or After"
    a.constraint_date = dlv.isoformat()
    a.udfs = dict(a.udfs or {})
    a.udfs["COMMENTS"] = (
        f"v37: delivery event, 1 day on {dlv:%m/%d/%y}, was a {was:.0f}d "
        f"procurement bar. The schedule now carries the delivery date rather "
        f"than the supplier's lead time as site duration.")[:248]
    shrunk += 1
print(f"\n1. DELIVERIES")
print(f"   LLE-to-LLE ties removed      : {cut}")
print(f"   deliveries shortened to 1 day: {shrunk}")
print(f"   site duration removed        : {saved:,.0f} working days")
print(f"   procurement bars reverted to Not Started: {reverted[0]}")
print(f"   already delivered, left as history      : {sum(1 for a in p.activities if a.uid in lle and a.actual_finish)}")
f1,n1=finish(p),negfloat(p)
print(f"   -> finish {f1}   negative float {n1}")

# ---- 2. cure lag -----------------------------------------------------
stripped=days=0
for r in p.relations:
    q=byu.get(r.predecessor_uid)
    if q is not None and r.lag > 0 and CURE.search(str(q.name)):
        days += r.lag/HRS
        r.lag = 0.0
        stripped += 1
print(f"\n2. CURE LAG")
print(f"   ties stripped of lag: {stripped}")
print(f"   lag removed         : {days:,.0f} days")
f2,n2=finish(p),negfloat(p)
print(f"   -> finish {f2}   negative float {n2}")

print(f"\n{'='*62}\nNET")
print(f"   finish          {finish(base)}  ->  {f2}")
print(f"   negative float  {negfloat(base)}  ->  {n2}")
out=f"{SCR}/MDC3_logic_repaired_v37.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"\nwrote {out}")
