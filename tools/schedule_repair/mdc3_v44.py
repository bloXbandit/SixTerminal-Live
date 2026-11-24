"""MDC-3 v44: put the phase-to-phase commissioning stagger back to contract.

Two ties. Our file staggers the phases 55 and 43 working days apart; the
02/04 contract's own milestone dates imply 18 and 17. The within-phase
lags are left alone -- they are already shorter than contract and cutting
them further would understate commissioning.
"""
import datetime as dt, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v43.xml"
FIX={("MDC3.MIL.PH1.1000","MDC3.MIL.PH2.9000"):18,
     ("MDC3.MIL.PH2.9000","MDC3.MIL.PH3.9000"):17}
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def F(a): return D(a.actual_finish) or D(a.early_finish)
b=load_xml(SRC); compute_dates(b, apply_dates=False)
f0=max(F(a) for a in b.activities if F(a))
p=load_xml(SRC)
rev={a.uid:str(a.activity_id) for a in p.activities}
ids={str(a.activity_id):a for a in p.activities}
n=0
for r in p.relations:
    k=(rev.get(r.predecessor_uid),rev.get(r.successor_uid))
    if k in FIX:
        old=r.lag/8; r.lag=FIX[k]*8.0; n+=1
        print(f"   {k[0]} -> {k[1]}   {old:.0f}d -> {FIX[k]}d")
        s=ids[k[1]]; s.udfs=dict(s.udfs or {})
        s.udfs["COMMENTS"]=(
            f"v44: phase stagger restored to the 02/04 contract. Was SS+{old:.0f}d, "
            f"now SS+{FIX[k]}d, which is the gap the contract's own milestone "
            f"dates imply.  " + s.udfs.get("COMMENTS",""))[:248]
compute_dates(p, apply_dates=False)
f1=max(F(a) for a in p.activities if F(a))
print(f"\nties changed {n}   finish {f0} -> {f1}   ({(f1-f0).days:+d} days)")
for k in ("MDC3.MIL.PH1.1070","MDC3.MIL.PH2.9070","MDC3.MIL.PH3.9070"):
    print(f"   {str(ids[k].name)[:34]:<36}{F(ids[k])}")
neg=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
pre={r.successor_uid for r in p.relations}
print(f"   negative float {neg}   orphans "
      f"{sum(1 for a in p.activities if a.uid not in pre and a.status=='Not Started' and not a.constraint_type)}")
out=f"{SCR}/MDC3_logic_repaired_v44.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"wrote {out}")
