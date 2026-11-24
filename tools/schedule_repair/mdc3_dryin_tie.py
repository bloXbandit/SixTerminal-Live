"""Temporary Dry In waits on all 15 precast areas including the last one.
Dry-in is progressive -- you dry in areas as they are erected. Measure what
relaxing that tie does, against the contractual planned dates."""
import sys, re, datetime as dt, collections
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
SRC=f"{SCR}/MDC3_logic_repaired_v35.xml"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
raw=load_xml(SRC)
PL={str(a.activity_id):(D(a.planned_start),D(a.planned_finish)) for a in raw.activities}
print(f"Temporary Dry In  PLANNED {PL['MDC3.ROOF.1870'][0]} -> {PL['MDC3.ROOF.1870'][1]}")
print("its 9 predecessors, by PLANNED finish -- note none finishes before the")
print("planned start, so the tie set contradicts the plan on its own terms:\n")
q=load_xml(SRC); byu={a.uid:a for a in q.activities}
t=next(a for a in q.activities if str(a.activity_id)=="MDC3.ROOF.1870")
ps=sorted([byu[r.predecessor_uid] for r in q.relations if r.successor_uid==t.uid],
          key=lambda a: PL[str(a.activity_id)][1])
for a in ps: print(f"   {str(a.activity_id):<22}{str(a.name)[:30]:<32}planned finish {PL[str(a.activity_id)][1]}")

def run(drop=(), label=""):
    p=load_xml(SRC); byu={a.uid:a for a in p.activities}
    ids={str(a.activity_id):a for a in p.activities}
    t=ids["MDC3.ROOF.1870"]
    p.relations=[r for r in p.relations
                 if not (r.successor_uid==t.uid
                         and str(byu[r.predecessor_uid].activity_id) in drop)]
    compute_dates(p)
    fa=[a for a in p.activities if re.match(r'MDC3\.FA\.GEN\.',str(a.activity_id))]
    fin=max((D(a.early_finish) or D(a.planned_finish)) for a in p.activities
            if D(a.early_finish) or D(a.planned_finish))
    print(f"\n{label}")
    print(f"   Temporary Dry In     {D(t.early_start)} -> {D(t.early_finish)}"
          f"   (slip {(D(t.early_start)-PL['MDC3.ROOF.1870'][0]).days:+d}d vs plan)")
    print(f"   Gen 315 High Steel   {D(ids['MDC3.GEN.315.1010'].early_start)}"
          f"   (slip {(D(ids['MDC3.GEN.315.1010'].early_start)-PL['MDC3.GEN.315.1010'][0]).days:+d}d)")
    print(f"   Scaffold             {D(ids['MDC3.FA.L1.SCAF.1000'].early_start)}"
          f" -> {D(ids['MDC3.FA.L1.SCAF.1000'].early_finish)}")
    print(f"   first FA device      {min(D(a.early_start) for a in fa)}")
    print(f"   last FA device       {max(D(a.early_finish) for a in fa)}")
    print(f"   PROJECT FINISH       {fin}")
    return fin

print("\n"+"="*66)
run((), "A  as it stands - all 9 ties")
run(("MDC3.ST.PC.A15.1010",), "B  drop ONLY the Area 15 Turnover tie")
run(("MDC3.ST.PC.A15.1010","MDC3.ST.PC.A10.1020","MDC3.ST.PC.A10.1030"),
    "C  drop Area 15 and Area 10 (keep areas 5, 6, 9)")
