"""The 11/30/26 scaffold date came from MDC-1. What does holding it cost on
MDC-3, and where would the scaffold land if logic drove it instead?"""
import sys, re, datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml"); compute_dates(p)
ids={str(a.activity_id):a for a in p.activities}
sc=ids["MDC3.FA.L1.SCAF.1000"]
fa=[a for a in p.activities if re.match(r'MDC3\.FA\.GEN\.',str(a.activity_id))]
first=min(fa,key=lambda a:D(a.early_start))
print("AS BUILT (Start On 11/30/26, the MDC-1 date)")
print(f"   scaffold            {D(sc.early_start)} -> {D(sc.early_finish)}")
print(f"   first FA device     {D(first.early_start)}  ({first.name})")
idle=(D(first.early_start)-D(sc.early_finish)).days
print(f"   scaffold stands idle {idle} days  ({idle/30.4:.1f} months of rental)")
print(f"   last FA device      {max(D(a.early_finish) for a in fa)}")
# what gates the penthouse? earliest gen-room high steel
hs=[a for a in p.activities if re.search(r'Install High Steel',str(a.name),re.I)
    and re.search(r'GEN\.',str(a.activity_id))]
e=min(hs,key=lambda a:D(a.early_start))
print(f"\nearliest penthouse structure: {e.activity_id}  {e.name}")
print(f"   planned  {D(e.planned_start)} -> {D(e.planned_finish)}")
print(f"   computed {D(e.early_start)} -> {D(e.early_finish)}")
print("\nIF LOGIC DROVE THE SCAFFOLD INSTEAD")
for lab,typ,anchor in (("B  starts with that high steel (SS)","Start to Start",e),
                       ("C  follows that high steel (FS)","Finish to Start",e)):
    q=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
    qi={str(a.activity_id):a for a in q.activities}
    s=qi["MDC3.FA.L1.SCAF.1000"]; s.constraint_type=None; s.constraint_date=None
    Rel=type(q.relations[0])
    n=max(int(r.uid) for r in q.relations if str(r.uid).isdigit())+1
    q.relations.append(Rel(uid=str(n),predecessor_uid=qi[str(anchor.activity_id)].uid,
                           successor_uid=s.uid,type=typ,lag=0.0))
    compute_dates(q)
    qfa=[a for a in q.activities if re.match(r'MDC3\.FA\.GEN\.',str(a.activity_id))]
    f2=min(qfa,key=lambda a:D(a.early_start))
    gap=(D(f2.early_start)-D(s.early_finish)).days
    fin=max((D(a.early_finish) or D(a.planned_finish)) for a in q.activities
            if D(a.early_finish) or D(a.planned_finish))
    print(f"\n{lab}")
    print(f"   scaffold            {D(s.early_start)} -> {D(s.early_finish)}")
    print(f"   first FA device     {D(f2.early_start)}   idle {gap}d")
    print(f"   last FA device      {max(D(a.early_finish) for a in qfa)}")
    print(f"   project finish      {fin}")
