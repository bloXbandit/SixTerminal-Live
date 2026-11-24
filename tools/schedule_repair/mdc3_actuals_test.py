"""Test the claim: the 2030 finish is driven by outstanding activities that
need actuals, not by bad logic. Measure the population, then re-run F9 with
those activities statused at their planned dates and see where it lands."""
import sys, datetime as dt, collections
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
wbs={w.uid:w for w in p.wbs_nodes}
def top(u):
    last=None
    while u in wbs: last=wbs[u].name; u=wbs[u].parent_uid
    return last
def top1(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else (o[0] if o else "?")
dd=D(p.data_date)
print(f"data date: {dd}\n")
# activities whose PLANNED work sits behind the data date but aren't statused
late=[a for a in p.activities if a.status=="Not Started"
      and D(a.planned_start) and D(a.planned_start)<dd]
part=[a for a in p.activities if a.status=="In Progress"
      and D(a.planned_finish) and D(a.planned_finish)<dd]
print(f"Not Started but planned to START before the data date : {len(late):>5}")
print(f"In Progress but planned to FINISH before it           : {len(part):>5}")
print(f"                                                 total: {len(late)+len(part):>5}"
      f"  of {len(p.activities):,}")
print(f"\nhours sitting in them: {sum(a.planned_labor_units for a in late+part):,.0f} h")
print("\nwhere they are:")
for k,v in collections.Counter(top1(a) for a in late+part).most_common(10):
    print(f"   {v:>5}  {k[:60]}")
print("\nhow far behind:")
b=collections.Counter()
for a in late:
    d=(dd-D(a.planned_start)).days
    b["over 1 year" if d>365 else "6-12 months" if d>180 else
      "3-6 months" if d>90 else "under 3 months"]+=1
for k in ("over 1 year","6-12 months","3-6 months","under 3 months"):
    if b[k]: print(f"   {b[k]:>5}  {k}")
# ---- the test: status them at planned dates, re-run F9 --------------
base=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml"); compute_dates(base)
f0=max(D(a.early_finish) or D(a.planned_finish) for a in base.activities
       if D(a.early_finish) or D(a.planned_finish))
q=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
n=0
for a in q.activities:
    ps,pf=D(a.planned_start),D(a.planned_finish)
    if a.status=="Not Started" and ps and ps<dd and pf and pf<dd:
        a.status="Completed"; a.actual_start=ps.isoformat()
        a.actual_finish=pf.isoformat(); a.percent_complete=100.0; n+=1
    elif a.status=="Not Started" and ps and ps<dd:
        a.status="In Progress"; a.actual_start=ps.isoformat()
        a.percent_complete=50.0; n+=1
compute_dates(q)
f1=max(D(a.early_finish) or D(a.planned_finish) for a in q.activities
       if D(a.early_finish) or D(a.planned_finish))
print(f"\n{'='*64}\nTEST: status the {n} overdue activities at their planned dates")
print(f"{'='*64}")
print(f"   finish as the file stands : {f0}")
print(f"   finish once statused      : {f1}")
print(f"   pulls in                  : {(f0-f1).days:,} days  ({(f0-f1).days/365:.1f} years)")
