"""MDC-3 v32: bring the file up to a 10/31 data date off Holder's 11/04 export.

His export carries 116 actual starts and 104 actual finishes, running to
10/29. Against our v31 only five of them are new, which says the file was
already close to current:

    4 he shows finished that we have running
    1 he shows started that we have not started

One of those four matters more than the rest. Excavate Footers for Elevator
#1 actually started AND finished on 10/28. v31 had to drag its start back to
10/24 because an actual cannot sit past the data date -- with the data date
at 10/31 that clamp is wrong, so the real dates go back in.

Then the data date moves 10/24 to 10/31. Nothing in his file happened between
10/29 and 10/31, so no work is being invented by moving it; the file simply
stops claiming to be a week staler than it is.

Not changed: the 828 activities we have in progress that his export does not
carry at all. They are long lead equipment and site utilities at a
granularity he does not schedule -- switchgear on order, dry coolers in
fabrication -- and his silence is not evidence against them. The one genuine
disagreement is noted, not overridden.
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
NEWDD = dt.date(2026, 10, 31)
H = {x["id"]: x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p = load_xml(f"{SCR}/MDC3_logic_repaired_v31.xml")
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
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


OLDDD = D(p.data_date)
ours = {str(a.activity_id): a for a in p.activities}
print(f"MDC-3 v31   data date {OLDDD} -> {NEWDD}")

# ===== 1. what the 828 actually are ==================================
print("\n" + "=" * 88)
print("THE 828 WE SHOW RUNNING THAT HOLDER DOES NOT CARRY")
print("=" * 88)
ip = [a for a in p.activities if a.status == "In Progress"]
only = [a for a in ip if str(a.activity_id) not in H]
c = collections.Counter(path(a.wbs_uid).split(" / ")[0] for a in only)
for k, v in c.most_common(8):
    print(f"   {v:>5}  {k[:64]}")
print("   -> his export does not schedule at this granularity; left alone")

# ===== 2. apply his actuals ==========================================
print("\n" + "=" * 88)
print("ACTUALS FROM HOLDER'S 11/04 EXPORT")
print("=" * 88)
fin = sta = 0
log = []
for i, h in H.items():
    a = ours.get(i)
    if a is None:
        continue
    hs = D(h["start"]) if h["start"] else None
    hf = D(h["finish"]) if h["finish"] else None
    if h["finish_actual"] and hf and hf <= NEWDD and a.status != "Completed":
        was = a.status
        a.status = "Completed"
        a.actual_start = (hs or hf).isoformat()
        a.actual_finish = hf.isoformat()
        a.actual_duration = a.planned_duration
        a.remaining_duration = 0.0
        a.percent_complete = 100.0
        a.remaining_labor_units = 0.0
        c_ = (a.udfs or {}).get("COMMENTS", "")
        a.udfs["COMMENTS"] = (
            (f"Completed {hf:%m/%d/%y} per Holder's 11/04/26 export "
             f"(was {was.lower()} here).  ") + c_)[:248]
        fin += 1
        log.append(("finished", a, hs, hf, was))
    elif h["start_actual"] and hs and hs <= NEWDD \
            and a.status == "Not Started":
        a.status = "In Progress"
        a.actual_start = hs.isoformat()
        dur = max((a.planned_duration or 8) / 8, 1)
        el = sum(1 for k in range((NEWDD - hs).days + 1)
                 if (hs + dt.timedelta(days=k)).weekday() < 5)
        a.percent_complete = min(95.0, max(1.0, round(100 * el / dur, 1)))
        a.remaining_duration = float(
            max(dur - el, 0.5) * 8)
        if a.planned_labor_units:
            a.remaining_labor_units = round(
                a.planned_labor_units * (1 - a.percent_complete / 100), 1)
        c_ = (a.udfs or {}).get("COMMENTS", "")
        a.udfs["COMMENTS"] = (
            (f"Started {hs:%m/%d/%y} per Holder's 11/04/26 export. Percent "
             f"estimated - CONFIRM WITH THE FIELD.  ") + c_)[:248]
        sta += 1
        log.append(("started", a, hs, None, "Not Started"))
print(f"completed: {fin}   started: {sta}")
for what, a, hs, hf, was in sorted(log, key=lambda x: (x[3] or x[2])):
    print(f"   {what:<10}{str(a.activity_id):<24}{str(a.name)[:38]:<40}"
          f"{(hf or hs)}   (was {was})")

# ===== 3. the clamped elevator =======================================
print("\n" + "=" * 88)
print("THE DATE v31 HAD TO CLAMP")
print("=" * 88)
a = ours.get("MDC3.ST.ELEV.1060")
h = H.get("MDC3.ST.ELEV.1060")
if a is not None and h is not None:
    hs, hf = D(h["start"]), D(h["finish"])
    print(f"   {a.activity_id}  {a.name}")
    print(f"      v31 clamped the start to {OLDDD} because an actual cannot "
          f"sit past the data date")
    print(f"      Holder: started {hs}, finished {hf} -- both valid at "
          f"{NEWDD}")
    a.actual_start = hs.isoformat()
    a.actual_finish = hf.isoformat()
    c_ = re.sub(r"Actual start [^.]*\.\s*", "",
                (a.udfs or {}).get("COMMENTS", ""))
    a.udfs["COMMENTS"] = (
        (f"Real dates restored in v32: started and finished {hf:%m/%d/%y} per "
         f"Holder's 11/04 export. v31 had pulled the start back to the old "
         f"{OLDDD:%m/%d} data date.  ") + c_)[:248]
    print(f"      -> restored {hs} .. {hf}")

# ===== 4. move the data date =========================================
p.data_date = NEWDD.isoformat()
print(f"\ndata date set to {NEWDD}")

# ===== checks ========================================================
print("\n" + "=" * 88)
print("CHECKS")
print("=" * 88)
byu = {a.uid: a for a in p.activities}
st = collections.Counter(a.status for a in p.activities)
print(f"status: {dict(st)}")
print(f"completed carrying remaining hours : "
      f"{sum(1 for a in p.activities if a.status == 'Completed' and (a.remaining_labor_units or 0) > 0)}")
print(f"actuals past the data date         : "
      f"{sum(1 for a in p.activities if (D(a.actual_finish) and D(a.actual_finish) > NEWDD) or (D(a.actual_start) and D(a.actual_start) > NEWDD))}")
print(f"in progress at 100%                : "
      f"{sum(1 for a in p.activities if a.status == 'In Progress' and (a.percent_complete or 0) >= 100)}")
print(f"out-of-sequence pairs              : "
      f"{sum(1 for r in p.relations if byu.get(r.predecessor_uid) and byu.get(r.successor_uid) and byu[r.predecessor_uid].status == 'Not Started' and byu[r.successor_uid].status in ('Completed', 'In Progress'))}")
pre = {r.successor_uid for r in p.relations}
suc = {r.predecessor_uid for r in p.relations}
print(f"islands                            : "
      f"{sum(1 for a in p.activities if a.uid not in pre and a.uid not in suc)}")
af = [D(a.actual_finish) for a in p.activities if D(a.actual_finish)]
print(f"latest actual in the file          : {max(af) if af else '-'}")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC3_logic_repaired_v32.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
