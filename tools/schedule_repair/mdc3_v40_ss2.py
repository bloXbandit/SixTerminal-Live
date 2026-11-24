"""MDC-3 v40: extend the relationship-type correction to the rest of the file.

v39 could only check the 2,595 activities that appear in Holder's 11/04
export. That left most FS ties unverifiable. Our own planned dates match the
export at 99.8% where the two overlap, so they stand in for the reference on
everything else -- same test, full coverage.

581 further FS ties are contradicted by the dates on their own two ends:
299 start on the same day, 282 are stacked by one to eight days. They sit
almost entirely in the build-out phases, which is exactly where the forward
pass was accumulating +22 to +27 day slips room by room.

Converted to Start-to-Start carrying the implied lag. No relationship is
removed; only type and lag change.
"""
import collections, datetime as dt, json, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v39.xml"
HRS=8.0
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def stats(p):
    compute_dates(p, apply_dates=False)
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
C=json.load(open(f"{SCR}/work/mdc3_ss_candidates2.json"))
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v39   finish {f0}   negative float {n0}")
p=load_xml(SRC)
uid={str(a.activity_id):a.uid for a in p.activities}
rel={(r.predecessor_uid,r.successor_uid):r for r in p.relations
     if r.type=="Finish to Start"}
n=0
for x in C:
    r=rel.get((uid.get(x["pred"]),uid.get(x["succ"])))
    if r is None: continue
    r.type="Start to Start"; r.lag=float(x["ss_lag"])*HRS; n+=1
print(f"\nconverted to SS: {n}")
f1,n1=stats(p)
print(f"   -> finish {f1}   negative float {n1}   ({(f1-f0).days:+,} days)")
ids={str(a.activity_id):a for a in p.activities}
raw={str(a.activity_id):D(a.planned_finish) for a in load_xml(SRC).activities}
print("\ncontract milestones:")
for k in ("MDC3.MIL.PH1.1070","MDC3.MIL.PH2.9070","MDC3.MIL.PH3.9070"):
    a=ids[k]; print(f"   {str(a.name)[:34]:<36}contract {raw[k]}   F9 "
                    f"{D(a.early_finish)}   {(D(a.early_finish)-raw[k]).days:+d}d")
print(f"\nrelation types: {dict(collections.Counter(r.type for r in p.relations))}")
out=f"{SCR}/MDC3_logic_repaired_v40.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"\nwrote {out}")
