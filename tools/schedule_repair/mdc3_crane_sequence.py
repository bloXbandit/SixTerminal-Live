"""Two tower cranes, per the temp-power permit set. A crane can only be in one
place at a time, so each crane's areas must be serial. Does the schedule model
that, and is it what the negative float is reporting?"""
import collections, datetime as dt, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v37.xml"); compute_dates(p)
byu={a.uid:a for a in p.activities}
pre=collections.defaultdict(list); suc=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r); suc[r.predecessor_uid].append(r)
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
cr=[a for a in p.activities if re.search(r'Crane',str(a.name),re.I)]
print(f"activities mentioning a crane: {len(cr)}")
print(f"   {dict(collections.Counter(('NORTH' if re.search(r'north',str(a.name),re.I) else 'SOUTH' if re.search(r'south',str(a.name),re.I) else 'other') for a in cr))}")
for side in ("North","South"):
    g=[a for a in cr if re.search(side,str(a.name),re.I) and re.search(r'Erection',str(a.name),re.I)]
    g.sort(key=lambda a:S(a) or dt.date(2030,1,1))
    tot=sum(a.planned_duration for a in g)/8
    print(f"\n{side.upper()} CRANE -- {len(g)} erection activities, {tot:,.0f} working days of lifting")
    print(f"   {'activity':<22}{'start':<12}{'finish':<12}{'dur':>5}  {'float':>7}  overlap with previous")
    prev=None
    ov=0
    for a in g:
        o=""
        if prev is not None and S(a) and F(prev) and S(a)<=F(prev):
            d=(F(prev)-S(a)).days
            ov+=d
            o=f"OVERLAPS {d}d"
        print(f"   {str(a.activity_id):<22}{str(S(a)):<12}{str(F(a)):<12}"
              f"{a.planned_duration/8:>4.0f}d  {(a.total_float or 0)/8:>6.0f}d  {o}")
        prev=a
    print(f"   -> total overlap between consecutive lifts on this crane: {ov} days")
    if ov: print(f"      a crane cannot be in two places at once; this is the crane double-booked")
