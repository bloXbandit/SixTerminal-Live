"""The schedule already dries in segment by segment. Tie each generator room
to ITS OWN segment's dry-in instead of the building-wide one."""
import sys, re, collections, datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
SRC=f"{SCR}/MDC3_logic_repaired_v35.xml"
SEG={**{n:"C" for n in range(301,305)}, **{n:"D" for n in range(305,310)},
     **{n:"E" for n in range(310,315)}, **{n:"H" for n in range(315,320)},
     **{n:"G" for n in range(320,324)}, **{n:"F" for n in range(324,329)}}
DRY={"C":"MDC3.ROOF.SYS.1160","D":"MDC3.ROOF.SYS.1120","E":"MDC3.ROOF.SYS.1080",
     "F":"MDC3.ROOF.SYS.1140","G":"MDC3.ROOF.SYS.1100","H":"MDC3.ROOF.SYS.1060"}
def D_(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
raw=load_xml(SRC)
PL={str(a.activity_id):(D_(a.planned_start),D_(a.planned_finish)) for a in raw.activities}
print("segment dry-in dates (planned):")
for s in sorted(DRY):
    print(f"   Segment {s}  {DRY[s]:<20}{PL[DRY[s]][0]} -> {PL[DRY[s]][1]}   "
          f"gen rooms {min(k for k,v in SEG.items() if v==s)}-{max(k for k,v in SEG.items() if v==s)}")
p=load_xml(SRC); ids={str(a.activity_id):a for a in p.activities}
byu={a.uid:a for a in p.activities}
gen_first={}   # the room's own first activity = Install High Steel
for n in SEG:
    a=ids.get(f"MDC3.GEN.{n}.1010")
    if a: gen_first[n]=a
old=ids["MDC3.ROOF.1870"]
removed=0
for n,a in gen_first.items():
    before=len(p.relations)
    p.relations=[r for r in p.relations
                 if not (r.successor_uid==a.uid and r.predecessor_uid==old.uid)]
    removed+=before-len(p.relations)
Rel=type(p.relations[0]); nx=max(int(r.uid) for r in p.relations if str(r.uid).isdigit())+1
added=0
for n,a in gen_first.items():
    src=ids[DRY[SEG[n]]]
    p.relations.append(Rel(uid=str(nx),predecessor_uid=src.uid,successor_uid=a.uid,
                           type="Finish to Start",lag=0.0)); nx+=1; added+=1
print(f"\nre-pointed: {removed} generic ties removed, {added} segment ties added")
compute_dates(p)
print(f"\n{'room':<8}{'seg':<5}{'planned HS':<14}{'now':<14}slip")
for n in sorted(gen_first):
    a=gen_first[n]; pl=PL[str(a.activity_id)][0]; now=D_(a.early_start)
    print(f"   {n:<5}{SEG[n]:<5}{str(pl):<14}{str(now):<14}{(now-pl).days:+d}d")
fa=[a for a in p.activities if re.match(r'MDC3\.FA\.GEN\.',str(a.activity_id))]
fin=max((D_(a.early_finish) or D_(a.planned_finish)) for a in p.activities
        if D_(a.early_finish) or D_(a.planned_finish))
print(f"\nscaffold        {D_(ids['MDC3.FA.L1.SCAF.1000'].early_start)} -> {D_(ids['MDC3.FA.L1.SCAF.1000'].early_finish)}")
print(f"first FA device {min(D_(a.early_start) for a in fa)}")
print(f"last FA device  {max(D_(a.early_finish) for a in fa)}")
print(f"PROJECT FINISH  {fin}")
