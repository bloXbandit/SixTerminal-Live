"""MDC-3 v41: remove the 129 reversed ties.

Each of these says a successor cannot start until its predecessor finishes,
while the 11/04 reference has the successor already COMPLETE before the
predecessor begins. The clearest case:

    our logic : Saw Cut 3rd Floor (Area 12) waits on Area 12 Turnover South
    Holder    : saw cut 12/04/26 - 12/07/26, turnover starts 02/05/27

The saw cut is finished two months before the turnover begins, so the tie
parks it doing nothing. 68 of the 129 sit on the critical path.

Either the tie was wrong or Holder's dates were. The user has confirmed the
tie is wrong -- the saw cut does not depend on that turnover -- so they are
removed rather than retyped. Retyping to SS with a negative lead reaches the
same date but keeps a dependency that does not exist; a lead of minus 45 days
is a fiction dressed as logic.

Every removal is written into the successor's COMMENTS so the file carries
its own audit trail and any family can be put back.
"""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v40.xml"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def stats(p):
    compute_dates(p, apply_dates=False)
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
C=[x for x in json.load(open(f"{SCR}/work/mdc3_ss_candidates.json"))
   if x["kind"]=="OVERLAP" and x["ss_lag"]<0]
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v40   finish {f0}   negative float {n0}\n")

def fam(pred,succ):
    def tag(k):
        p=k.split(".")
        return ".".join(p[1:3]) if len(p)>2 else k
    return f"{tag(pred)} -> {tag(succ)}"
g=collections.Counter(fam(x["pred"],x["succ"]) for x in C)
print(f"the {len(C)} reversed ties, grouped by pattern:")
for k,v in g.most_common():
    print(f"   {v:>4}  {k}")

p=load_xml(SRC)
uid={str(a.activity_id):a.uid for a in p.activities}
ids={str(a.activity_id):a for a in p.activities}
kill={}
for x in C:
    k=(uid.get(x["pred"]),uid.get(x["succ"]))
    if None not in k: kill[k]=x
before=len(p.relations)
p.relations=[r for r in p.relations
             if (r.predecessor_uid,r.successor_uid) not in kill]
print(f"\nties removed: {before-len(p.relations)}")
for (pu,su),x in kill.items():
    a=ids.get(x["succ"])
    if a is None: continue
    a.udfs=dict(a.udfs or {})
    a.udfs["COMMENTS"]=(
        f"v41: removed predecessor {x['pred']} - the 11/04 reference has this "
        f"activity complete {abs(x['ss_lag'])} working days BEFORE that "
        f"predecessor starts, so the dependency does not exist. Confirmed "
        f"with the field.  " + a.udfs.get("COMMENTS",""))[:248]
# Removing a tie can leave its successor with no predecessor at all, and a
# Not Started activity with nothing driving it collapses onto the data date.
# Those get the same treatment the deliveries got: "Start On or After" at the
# date the schedule already expects, which holds the date without capping the
# late date and inventing negative float.
have={r.successor_uid for r in p.relations}
pinned=0
for a in p.activities:
    if (a.uid in have or a.status!="Not Started" or a.constraint_type
            or not D(a.planned_start)):
        continue
    a.constraint_type="Start On or After"
    a.constraint_date=D(a.planned_start).isoformat()
    a.udfs=dict(a.udfs or {})
    a.udfs["COMMENTS"]=(
        f"v41: left with no predecessor when a reversed tie was removed. "
        f"Pinned at its own planned start so it holds instead of collapsing "
        f"onto the data date. CONFIRM what really drives it.  "
        + a.udfs.get("COMMENTS",""))[:248]
    pinned+=1
print(f"orphaned by the removals, pinned at their planned start: {pinned}")

f1,n1=stats(p)
print(f"   -> finish {f1}   negative float {n1}   ({(f1-f0).days:+,} days)")
raw={str(a.activity_id):D(a.planned_finish) for a in load_xml(SRC).activities}
ids={str(a.activity_id):a for a in p.activities}
print("\ncontract milestones:")
for k in ("MDC3.MIL.PH1.1070","MDC3.MIL.PH2.9070","MDC3.MIL.PH3.9070"):
    a=ids[k]
    print(f"   {str(a.name)[:34]:<36}contract {raw[k]}   F9 {D(a.early_finish)}"
          f"   {(D(a.early_finish)-raw[k]).days:+d}d")
out=f"{SCR}/MDC3_logic_repaired_v41.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"\nwrote {out}")
