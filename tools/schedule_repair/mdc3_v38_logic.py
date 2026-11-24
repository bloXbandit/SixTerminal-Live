"""MDC-3 v38: pull the four backwards ties, and stop the v34 pins from
manufacturing negative float.

The crane permit set settles the structural question: MDC-3 has TWO tower
cranes, each on its own 570 KVA temporary generator until Dominion energises.
The schedule models that correctly -- north and south crane erection run as
two serial chains with ZERO overlap between consecutive lifts on either one.
So the crane sequence is not the problem, and no crane logic is touched here.

What the negative float was actually reporting:

1. FOUR BACKWARDS TIES. Each has a Not Started predecessor feeding a
   successor that is already complete or under way, which cannot happen:

     Area 5 Turnover South -> MEP Underground Electrical West   134d
     Area 5 Turnover North -> MEP Underground Electrical West   126d
        Precast turnover cannot precede underground MEP; the underground
        goes in before the structure. The successor is Completed.
     Site Telecom / Power (Overall) -> Telecom North West Crossing  141d
        An "(Overall)" activity feeding one of its own components. The
        components make up the overall, not the other way round. This one
        carried the worst float in the file at -145 days.
     Automatic Flush Valve Install -> Telecom South West Crossing   162d
        An irrigation flush valve has no bearing on a telecom crossing.

   Removed. Nothing else about these activities changes.

2. THE v34 START ON PINS. Eight activities were pinned with "Start On" to
   stop them collapsing onto the data date. That constraint also caps the
   late date, so four of the eight were reporting negative float purely
   because of the pin -- the same mistake the delivery pins made in v37.
   They become "Start On or After", which holds the date without inventing
   a deadline.

LEFT ALONE, deliberately: four more out-of-sequence ties inside the
slab-on-grade pours (Segment F Pour 11 -> Segment A Pour 12 and similar).
Under the corrected pour numbering F really does precede A, so the LOGIC is
right -- what contradicts it is the status, with Segment A marked complete
while Segment F has not started. That is a field records question, not a
logic fix, and deleting good logic to silence it would be wrong.
"""
import collections, datetime as dt, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v37.xml"
KILL=[("MDC3.ST.PC.A5.1020","MDC3.STR.UDG.2120"),
      ("MDC3.ST.PC.A5.1030","MDC3.STR.UDG.2120"),
      ("MDC3.EXT.SUT.1730","MDC3.EXT.SUT.1920"),
      ("MDC3.EXT.SUT.1880","MDC3.EXT.SUT.1930")]
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def stats(p):
    compute_dates(p)
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v37   finish {f0}   negative float {n0}")
p=load_xml(SRC)
ids={str(a.activity_id):a for a in p.activities}
uid={str(a.activity_id):a.uid for a in p.activities}
kill={(uid[x],uid[y]) for x,y in KILL if x in uid and y in uid}
before=len(p.relations)
p.relations=[r for r in p.relations
             if (r.predecessor_uid,r.successor_uid) not in kill]
print(f"\n1. backwards ties removed: {before-len(p.relations)}")
for x,y in KILL:
    print(f"      {x:<22}-> {y}")
f1,n1=stats(p); print(f"   -> finish {f1}   negative float {n1}")
fixed=0
for a in p.activities:
    if (a.constraint_type or "").strip().lower()=="start on":
        a.constraint_type="Start On or After"; fixed+=1
print(f"\n2. v34 'Start On' pins relaxed to 'Start On or After': {fixed}")
f2,n2=stats(p); print(f"   -> finish {f2}   negative float {n2}")
print(f"\n{'='*58}\nNET   finish {f0} -> {f2}   negative float {n0} -> {n2}")
out=f"{SCR}/MDC3_logic_repaired_v38.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"wrote {out}")
