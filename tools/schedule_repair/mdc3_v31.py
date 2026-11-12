"""MDC-3 v31: the three things MDC-1 got in v20 that this file never did.

1  49 completed activities still carry remaining labour. Finished work with
   hours still booked ahead of it inflates what is left and makes percent
   complete read low -- the same defect cleared on MDC-1, where it was 374
   activities holding 49,549 hours.

2  One actual date sits past the data date. Small, but an actual in the
   future is the kind of thing that stops an import or gets picked apart in
   a claim review.

3  While the file is open, the same consistency check MDC-1 got: an in
   progress activity's remaining labour should be its budget times the share
   not yet done, and a not started activity's remaining should be its whole
   budget.

No date, status, logic or budget is changed. Remaining labour is derived from
budget and percent complete, which is arithmetic, not a judgement about the
work.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC3_logic_repaired_v30.xml")
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
B = dict(bud=sum(a.planned_labor_units or 0 for a in p.activities),
         rem=sum(a.remaining_labor_units or 0 for a in p.activities),
         act=sum(a.actual_labor_units or 0 for a in p.activities))
print(f"MDC-3 v30   data date {DD}   {len(p.activities)} activities")

# ===== 1. completed carrying remaining ===============================
print("\n" + "=" * 86)
print("1. COMPLETED ACTIVITIES STILL CARRYING REMAINING LABOUR")
print("=" * 86)
bad = [a for a in p.activities
       if a.status == "Completed" and (a.remaining_labor_units or 0) > 0]
print(f"{len(bad)} activities holding "
      f"{sum(a.remaining_labor_units or 0 for a in bad):,.0f} h")
print(f"\n{'activity':<26}{'name':<42}{'budget':>9}{'remaining':>11}")
for a in sorted(bad, key=lambda x: -(x.remaining_labor_units or 0))[:20]:
    print(f"{str(a.activity_id):<26}{str(a.name)[:40]:<42}"
          f"{(a.planned_labor_units or 0):>9,.0f}"
          f"{(a.remaining_labor_units or 0):>11,.0f}")
if len(bad) > 20:
    print(f"   ... and {len(bad) - 20} more")
for a in bad:
    a.remaining_labor_units = 0.0
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = (
        ("Remaining labour cleared in v31: this activity is complete, so "
         "nothing remains. It was still booked as work ahead of us.  ")
        + c)[:248]

# ===== 2. the actual past the data date ==============================
print("\n" + "=" * 86)
print("2. ACTUAL DATES PAST THE DATA DATE")
print("=" * 86)
fut = [a for a in p.activities
       if (D(a.actual_finish) and D(a.actual_finish) > DD)
       or (D(a.actual_start) and D(a.actual_start) > DD)]
print(f"{len(fut)} activities")
for a in fut:
    s, f = D(a.actual_start), D(a.actual_finish)
    print(f"   {str(a.activity_id):<26}{str(a.name)[:40]:<42}{a.status}")
    print(f"      actual start {s}   actual finish {f}   data date {DD}")
    print(f"      {path(a.wbs_uid)[:76]}")
    note = []
    if s and s > DD:
        a.actual_start = DD.isoformat()
        note.append(f"start {s:%m/%d/%y}")
    if f and f > DD:
        a.actual_finish = DD.isoformat()
        note.append(f"finish {f:%m/%d/%y}")
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = (
        (f"Actual {' and '.join(note)} was after the {DD:%m/%d/%y} data date, "
         f"which cannot be; pulled back to the data date in v31. CONFIRM THE "
         f"REAL DATE WITH THE FIELD.  ") + c)[:248]
    print(f"      -> pulled back to {DD}")

# ===== 3. remaining consistent with status ===========================
print("\n" + "=" * 86)
print("3. REMAINING LABOUR AGAINST STATUS AND PERCENT")
print("=" * 86)
ip = ns = 0
iph = nsh = 0.0
for a in p.activities:
    bu = a.planned_labor_units or 0
    rl = a.remaining_labor_units or 0
    if a.status == "In Progress":
        pc = min(max(a.percent_complete or 0, 0), 100)
        want = round(bu * (1 - pc / 100), 1)
        if abs(rl - want) > 0.5:
            ip += 1
            iph += want - rl
            a.remaining_labor_units = want
    elif a.status == "Not Started" and bu and abs(rl - bu) > 0.5:
        ns += 1
        nsh += bu - rl
        a.remaining_labor_units = float(bu)
print(f"in progress, remaining retimed : {ip:>5}   {iph:>+12,.0f} h")
print(f"not started, remaining reset   : {ns:>5}   {nsh:>+12,.0f} h")

# ===== checks ========================================================
A = dict(bud=sum(a.planned_labor_units or 0 for a in p.activities),
         rem=sum(a.remaining_labor_units or 0 for a in p.activities),
         act=sum(a.actual_labor_units or 0 for a in p.activities))
print("\n" + "=" * 86)
print("CHECKS")
print("=" * 86)
print(f"{'':<34}{'v30':>14}{'v31':>14}")
for k, lbl in (("bud", "budget"), ("act", "actual"), ("rem", "remaining")):
    print(f"{lbl:<34}{B[k]:>14,.0f}{A[k]:>14,.0f}")
print(f"\ncompleted carrying remaining hours : "
      f"{sum(1 for a in p.activities if a.status == 'Completed' and (a.remaining_labor_units or 0) > 0)}")
print(f"actuals past the data date         : "
      f"{sum(1 for a in p.activities if (D(a.actual_finish) and D(a.actual_finish) > DD) or (D(a.actual_start) and D(a.actual_start) > DD))}")
print(f"in progress at 100%                : "
      f"{sum(1 for a in p.activities if a.status == 'In Progress' and (a.percent_complete or 0) >= 100)}")
byu = {a.uid: a for a in p.activities}
print(f"out-of-sequence pairs              : "
      f"{sum(1 for r in p.relations if byu.get(r.predecessor_uid) and byu.get(r.successor_uid) and byu[r.predecessor_uid].status == 'Not Started' and byu[r.successor_uid].status in ('Completed', 'In Progress'))}")
pre = {r.successor_uid for r in p.relations}
suc = {r.predecessor_uid for r in p.relations}
print(f"islands (no logic at all)          : "
      f"{sum(1 for a in p.activities if a.uid not in pre and a.uid not in suc)}")
o2 = collections.defaultdict(set)
for r in p.relations:
    o2[r.predecessor_uid].add(r.successor_uid)
col = collections.defaultdict(int)
cyc = 0
for s in list(byu):
    if col[s]:
        continue
    st = [(s, iter(o2[s]))]
    col[s] = 1
    while st:
        n_, it = st[-1]
        adv = False
        for v in it:
            if col[v] == 1:
                cyc += 1
            elif col[v] == 0:
                col[v] = 1
                st.append((v, iter(o2[v])))
                adv = True
                break
        if not adv:
            col[n_] = 2
            st.pop()
print(f"cycles                             : {cyc}")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}"
      f"   (limit 255)")
out = f"{SCR}/MDC3_logic_repaired_v31.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
