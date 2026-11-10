import collections,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
p=load_xml(f"{SCR}/MDC1_logic_repaired_v16.xml")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
DD=D(p.data_date)
ns=[a for a in p.activities if a.status=="Not Started"]
print(f"data date {DD}   not-started activities {len(ns)}\n")
print(f"{'field':<26}{'has value':>10}{'before DD':>11}{'on/after DD':>12}{'earliest':>12}{'latest':>12}")
for f in ("planned_start","early_start","late_start","remaining_early_start",
          "planned_finish","early_finish"):
    v=[D(getattr(a,f,None)) for a in ns]
    v=[x for x in v if x]
    if not v:
        print(f"{f:<26}{'(empty)':>10}"); continue
    print(f"{f:<26}{len(v):>10}{sum(1 for x in v if x<DD):>11}"
          f"{sum(1 for x in v if x>=DD):>12}{str(min(v)):>12}{str(max(v)):>12}")
print("\n=== so which field is the live forecast? ===")
ps=[D(a.planned_start) for a in ns if D(a.planned_start)]
es=[D(a.early_start) for a in ns if D(a.early_start)]
print(f"planned_start before the data date: {100*sum(1 for x in ps if x<DD)//len(ps)}%")
print(f"early_start   before the data date: {100*sum(1 for x in es if x<DD)//len(es)}%")
print("\n=== sample: a few not-started activities, both fields ===")
for a in ns[:12]:
    print(f"   {str(a.activity_id):<26}{str(a.name)[:30]:<32}"
          f"planned {D(a.planned_start)}   early {D(a.early_start)}   "
          f"late {D(a.late_start)}")
print("\n=== do early dates respect the data date? ===")
bad=[a for a in ns if D(a.early_start) and D(a.early_start)<DD]
print(f"not-started with an EARLY start before the data date: {len(bad)}")
for a in bad[:8]:
    print(f"   {str(a.activity_id):<26}{str(a.name)[:34]:<36}early {D(a.early_start)}"
          f"  constraint {a.constraint_type} {D(a.constraint_date)}")
