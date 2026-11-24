"""The mechanisms, totalled across the critical path rather than one at a time."""
import collections, datetime as dt, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v36.xml"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
raw=load_xml(SRC)
PL={str(a.activity_id):D(a.planned_start) for a in raw.activities}
p=load_xml(SRC); compute_dates(p)
byu={a.uid:a for a in p.activities}; wbs={w.uid:w for w in p.wbs_nodes}
pre=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r)
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
fin=max((a for a in p.activities if F(a)),key=F)
chain=[]; cur=fin; seen=set()
while cur and cur.uid not in seen:
    seen.add(cur.uid); chain.append(cur)
    best=None
    for r in pre.get(cur.uid,[]):
        q=byu.get(r.predecessor_uid)
        if q and F(q) and (best is None or F(q)>F(best)): best=q
    cur=best
chain.reverse()
cset={a.uid for a in chain}

print("="*72); print("A.  LAG ON THE DRIVING CHAIN"); print("="*72)
lags=[]
for i in range(1,len(chain)):
    for r in pre.get(chain[i].uid,[]):
        if r.predecessor_uid==chain[i-1].uid and r.lag:
            lags.append((r.lag/8,chain[i-1],chain[i],r.type))
lags.sort(key=lambda x:-x[0])
print(f"   ties on the chain carrying lag: {len(lags)}")
print(f"   total lag: {sum(l for l,_,_,_ in lags):,.0f} days")
for l,q,s,t in lags[:10]:
    print(f"   {l:>+6.0f}d  {str(q.activity_id):<20}-> {str(s.activity_id):<20}{t}")
    print(f"            {str(q.name)[:34]:<36}{str(s.name)[:34]}")

print(); print("="*72); print("B.  DURATION ON THE CHAIN"); print("="*72)
tot=sum(a.planned_duration/8 for a in chain)
print(f"   sum of activity durations : {tot:,.0f} working days")
span=(F(chain[-1])-S(chain[0])).days
print(f"   calendar span of the chain: {span:,} days ({span/365:.1f} years)")
print(f"   so roughly {span-tot*7/5:,.0f} calendar days are NOT work -- they are")
print(f"   waiting: lag, gather milestones, and predecessors finishing late.")
print("\n   longest single activities on the chain:")
for a in sorted(chain,key=lambda a:-a.planned_duration)[:8]:
    print(f"      {a.planned_duration/8:>5.0f}d  {str(a.activity_id):<22}{str(a.name)[:40]}")

print(); print("="*72); print("C.  GATHER MILESTONES"); print("="*72)
gm=[(len(pre.get(a.uid,[])),a) for a in p.activities
    if a.planned_duration==0 and len(pre.get(a.uid,[]))>20]
gm.sort(key=lambda x:-x[0])
print(f"   zero-duration activities with 20+ predecessors: {len(gm)}")
print("   (each waits on its LATEST predecessor, so one late item holds the lot)")
for n,a in gm[:10]:
    oncp="  <-- ON THE DRIVING CHAIN" if a.uid in cset else ""
    print(f"   {n:>4} preds  {str(a.activity_id):<22}{str(a.name)[:38]:<40}{S(a)}{oncp}")

print(); print("="*72); print("D.  NEGATIVE FLOAT"); print("="*72)
neg=[a for a in p.activities if a.total_float is not None and a.total_float<0]
print(f"   activities with negative float: {len(neg):,}")
b=collections.Counter()
for a in neg:
    d=-a.total_float/8
    b["over 1000d" if d>1000 else "500-1000d" if d>500 else
      "100-500d" if d>100 else "under 100d"]+=1
for k in ("over 1000d","500-1000d","100-500d","under 100d"):
    if b[k]: print(f"      {b[k]:>5}  {k}")
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
print("\n   where:")
for k,v in collections.Counter(area(a) for a in neg).most_common(6):
    print(f"      {v:>5}  {k[:54]}")
print("\n   the worst:")
for a in sorted(neg,key=lambda a:a.total_float)[:6]:
    print(f"      {a.total_float/8:>8.0f}d  {str(a.activity_id):<22}{str(a.name)[:40]}")
print("\n   -> negative float means a deadline the logic cannot meet. These are")
print("      measured against MUST-FINISH or late constraints downstream.")
mf=getattr(p,'must_finish_by',None)
print(f"   project Must Finish By: {mf}")
