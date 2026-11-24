"""MDC-3 v42: give the stranded activities real logic where the export has it.

v41 pinned 11 activities that lost their only predecessor. A pin holds a date
but says nothing about what drives the work, so it is a last resort. The
11/04 export names predecessors, and five of the eleven can be tied properly:

  two take a clean Finish to Start -- the reference has the predecessor
  finishing before they begin
  three are HES Stub Up Verification, which the reference overlaps with its
  own Install Underground Shallow Electrical, so they take Start to Start

The other six name predecessors that do not exist in our file at all, so
they keep the pin and the comment asking what drives them.
"""
import datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v41.xml"
HRS=8.0
FS=[("MDC3.PH1.GL203E.1020","MDC3.PH1.GL203E.1090"),
    ("MDC3.PH1.GAL204.1190","MDC3.PH1.GAL204.1160")]
SS=[("MDC3.ST.L1.A25.1050","MDC3.ST.L1.A25.1110"),
    ("MDC3.ST.L1.A42.1050","MDC3.ST.L1.A42.1110"),
    ("MDC3.ST.L1.A43.1050","MDC3.ST.L1.A43.1110")]
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def stats(p):
    compute_dates(p, apply_dates=False)
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v41   finish {f0}   negative float {n0}")
p=load_xml(SRC)
ids={str(a.activity_id):a for a in p.activities}
Rel=type(p.relations[0])
nxt=max(int(r.uid) for r in p.relations if str(r.uid).isdigit())+1
added=0
for pairs,typ in ((FS,"Finish to Start"),(SS,"Start to Start")):
    for x,y in pairs:
        q,s=ids.get(x),ids.get(y)
        if not q or not s: continue
        p.relations.append(Rel(uid=str(nxt),predecessor_uid=q.uid,
                               successor_uid=s.uid,type=typ,lag=0.0))
        nxt+=1; added+=1
        s.constraint_type=None; s.constraint_date=None
        s.udfs=dict(s.udfs or {})
        s.udfs["COMMENTS"]=(
            f"v42: real predecessor {x} ({typ}) taken from the 11/04 export; "
            f"the v41 pin is removed.  "
            + re.sub(r"v41: left with no predecessor.*?CONFIRM what really drives it\.\s*","",
                     s.udfs.get("COMMENTS","")))[:248]
        print(f"   {typ:<17}{x:<24}-> {y}")
print(f"\nreal predecessors restored: {added}   pins removed: {added}")
pre={r.successor_uid for r in p.relations}
left=[a for a in p.activities if a.uid not in pre and a.status=="Not Started"
      and a.constraint_type]
print(f"still pinned, predecessor not in our file: "
      f"{sum(1 for a in left if 'v41: left with no predecessor' in str((a.udfs or {}).get('COMMENTS','')))}")
f1,n1=stats(p)
print(f"\n   -> finish {f1}   negative float {n1}   ({(f1-f0).days:+,} days)")
out=f"{SCR}/MDC3_logic_repaired_v42.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"wrote {out}")
