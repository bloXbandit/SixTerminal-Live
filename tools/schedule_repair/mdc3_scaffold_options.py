"""With the Area 15 tie relaxed, where should the scaffold hang? Test each
realistic activity in the generator-room breakdown."""
import sys, re, datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
SRC=f"{SCR}/MDC3_logic_repaired_v35.xml"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
print("the generator-room breakdown, in sequence (Gen 315):")
r0=load_xml(SRC)
for a in sorted([a for a in r0.activities if ".GEN.315." in str(a.activity_id)],
                key=lambda a:str(a.activity_id))[:9]:
    print(f"   {str(a.activity_id):<22}{a.name}")
def run(pat,label):
    p=load_xml(SRC); byu={a.uid:a for a in p.activities}
    ids={str(a.activity_id):a for a in p.activities}
    t=ids["MDC3.ROOF.1870"]
    p.relations=[r for r in p.relations
                 if not (r.successor_uid==t.uid and
                         str(byu[r.predecessor_uid].activity_id)=="MDC3.ST.PC.A15.1010")]
    sc=ids["MDC3.FA.L1.SCAF.1000"]
    p.relations=[r for r in p.relations if r.successor_uid!=sc.uid]
    def S(a): return D(a.actual_start) or D(a.early_start) or D(a.planned_start)
    cand=[a for a in p.activities if re.search(pat,str(a.name),re.I)
          and re.search(r'GEN\.\d{3}\.',str(a.activity_id)) and S(a)]
    anchor=min(cand,key=S)
    Rel=type(p.relations[0])
    n=max(int(r.uid) for r in p.relations if str(r.uid).isdigit())+1
    p.relations.append(Rel(uid=str(n),predecessor_uid=anchor.uid,
                           successor_uid=sc.uid,type="Start to Start",lag=0.0))
    compute_dates(p)
    fa=[a for a in p.activities if re.match(r'MDC3\.FA\.GEN\.',str(a.activity_id))]
    f1=min(D(a.early_start) for a in fa)
    idle=(f1-D(sc.early_finish)).days
    fin=max((D(a.early_finish) or D(a.planned_finish)) for a in p.activities
            if D(a.early_finish) or D(a.planned_finish))
    print(f"\n{label}  (anchored on {anchor.activity_id})")
    print(f"   scaffold        {D(sc.early_start)} -> {D(sc.early_finish)}")
    print(f"   first FA device {f1}    idle {idle:>4}d")
    print(f"   last FA device  {max(D(a.early_finish) for a in fa)}")
    print(f"   finish          {fin}")
print("\n"+"="*62)
for pat,lab in ((r'Install High Steel',"1  High Steel"),
                (r'OH Ladder Tray',   "2  OH Ladder Tray"),
                (r'OH Branch Raceway',"3  OH Branch Raceway"),
                (r'Hang SCR',         "4  Hang SCR"),
                (r'IMP Wall Install', "5  IMP Wall Install  (room enclosed)"),
                (r'Epoxy Floor',      "6  Epoxy Floor")):
    run(pat,lab)
