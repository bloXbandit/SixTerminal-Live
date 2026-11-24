"""MDC-3 v39: match the relationship TYPE to what the 11/04 reference shows.

603 days between our forward pass and Holder's own projection is too much to
be real slip. The forecast START dates agree at 99.8%, so the disagreement is
not about when work happens -- it is about how the work is tied together.
Run serially what the reference runs in parallel and the duration is
manufactured by the logic.

631 Finish-to-Start ties have both ends in the 11/04 export and are
contradicted by it:

   347  the reference starts both activities on the SAME DAY
   155  the successor starts after the predecessor starts but before it
        finishes -- real stacking, with a lag the reference implies
   129  the successor starts BEFORE its own predecessor

The first two are converted to Start-to-Start carrying the lag the reference
implies. No relationship is removed and no activity is touched; only the type
and lag change, so every tie the user set is still there.

The last 129 are NOT converted. A successor starting before its predecessor
is reversed logic, not stacking, and inventing a negative lead to paper over
it would bury the problem. They are listed for the field.
"""
import collections, datetime as dt, json, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v38.xml"
HRS=8.0
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def stats(p):
    compute_dates(p, apply_dates=False)        # never overwrite planned dates
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
C=json.load(open(f"{SCR}/work/mdc3_ss_candidates.json"))
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v38   finish {f0}   negative float {n0}")
p=load_xml(SRC)
uid={str(a.activity_id):a.uid for a in p.activities}
rel={(r.predecessor_uid,r.successor_uid):r for r in p.relations
     if r.type=="Finish to Start"}
def apply(rows,label):
    n=0
    for x in rows:
        k=(uid.get(x["pred"]),uid.get(x["succ"]))
        r=rel.get(k)
        if r is None: continue
        r.type="Start to Start"
        r.lag=float(x["ss_lag"])*HRS
        n+=1
    f,neg=stats(p)
    print(f"\n{label}: {n} ties converted to SS")
    print(f"   -> finish {f}   negative float {neg}")
    return f,neg
f1,_=apply([x for x in C if x["kind"]=="SAME START"],
           "1. same start in the reference -> SS+0")
f2,n2=apply([x for x in C if x["kind"]=="OVERLAP" and x["ss_lag"]>=0],
            "2. stacked in the reference -> SS with the implied lag")
neg=[x for x in C if x["kind"]=="OVERLAP" and x["ss_lag"]<0]
print(f"\n3. NOT converted, successor starts before its predecessor: {len(neg)}")
print("   (reversed logic, for the field -- worst ten)")
for x in sorted(neg,key=lambda x:x["ss_lag"])[:10]:
    print(f"      {x['pred']:<24}-> {x['succ']:<24}reference implies SS{x['ss_lag']:+d}d")
print(f"\n{'='*60}")
print(f"NET   finish {f0} -> {f2}   ({(f2-f0).days:+,} days)")
print(f"      negative float {n0} -> {n2}")
out=f"{SCR}/MDC3_logic_repaired_v39.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"wrote {out}")
