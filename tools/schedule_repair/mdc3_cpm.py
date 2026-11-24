"""MDC-3 critical path: what actually pushes the date when F9 runs.

Planned dates are captured BEFORE the forward pass, because compute_dates
overwrites them. Slip is then measured per activity, and the driving chain is
walked so we can see where slip is INTRODUCED rather than merely inherited --
an activity that is 200 days late because its predecessor was 200 days late is
a passenger, not a driver.
"""
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
PL={str(a.activity_id):(D(a.planned_start),D(a.planned_finish)) for a in raw.activities}
p=load_xml(SRC); compute_dates(p)
byu={a.uid:a for a in p.activities}; ids={str(a.activity_id):a for a in p.activities}
wbs={w.uid:w for w in p.wbs_nodes}
pre=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r)
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
def slip(a):
    pl=PL.get(str(a.activity_id),(None,None))[0]
    s=S(a)
    return (s-pl).days if pl and s else None

print("="*74); print("1.  HEADLINE"); print("="*74)
fin=max((a for a in p.activities if F(a)),key=F)
print(f"   F9 finish      {F(fin)}   {fin.activity_id}  {fin.name}")
crit=[a for a in p.activities if a.total_float is not None and a.total_float<=0]
neg=[a for a in crit if a.total_float<0]
print(f"   critical path  {len(crit):,} activities   of which NEGATIVE float: {len(neg):,}")
if neg:
    print(f"   worst negative float: {min(a.total_float for a in neg)/8:.0f} days")

print(); print("="*74); print("2.  THE DRIVING CHAIN"); print("="*74)
chain=[]; cur=fin; seen=set()
while cur and cur.uid not in seen:
    seen.add(cur.uid); chain.append(cur)
    best=None
    for r in pre.get(cur.uid,[]):
        q=byu.get(r.predecessor_uid)
        if q and F(q) and (best is None or F(q)>F(best)): best=q
    cur=best
chain.reverse()
print(f"   {len(chain)} activities, {S(chain[0])} -> {F(chain[-1])}")
for k,v in collections.Counter(area(a) for a in chain).most_common():
    print(f"      {v:>4}  {k[:56]}")

print(); print("="*74)
print("3.  WHERE SLIP IS INTRODUCED  (not inherited)")
print("="*74)
intro=[]
prev=0
for a in chain:
    s=slip(a)
    if s is None: continue
    intro.append((s-prev,a,s)); prev=s
intro.sort(key=lambda x:-x[0])
print(f"   {'added':>7}  {'cumulative':>10}  activity")
for add,a,cum in intro[:14]:
    if add<=0: break
    print(f"   {add:>+6}d  {cum:>+9}d  {str(a.activity_id):<22}{str(a.name)[:40]}")
    print(f"              {'':>10}  planned {PL[str(a.activity_id)][0]}  ->  F9 {S(a)}   [{a.status}]")
tot=sum(x[0] for x in intro if x[0]>0)
print(f"\n   total slip introduced along the chain: {tot:,} days")

print(); print("="*74)
print("4.  WHAT CAUSES IT -- mechanism behind each driver")
print("="*74)
for add,a,cum in intro[:8]:
    if add<=0: break
    print(f"\n   +{add}d  {a.activity_id}  {str(a.name)[:44]}")
    print(f"        constraint : {a.constraint_type or 'none'} {a.constraint_date or ''}")
    print(f"        duration   : {a.planned_duration/8:.0f}d   status {a.status}")
    ps=pre.get(a.uid,[])
    print(f"        predecessors: {len(ps)}")
    ranked=sorted([(byu[r.predecessor_uid],r) for r in ps if r.predecessor_uid in byu],
                  key=lambda x:-(F(x[0]) or dt.date(1900,1,1)).toordinal())
    for q,r in ranked[:3]:
        lag=r.lag/8
        print(f"           <- {str(q.activity_id):<22}{str(q.name)[:30]:<32}"
              f"{r.type.replace('Finish to Start','FS').replace('Start to Start','SS').replace('Finish to Finish','FF')}"
              f"{'' if lag==0 else f' {lag:+.0f}d'}  finishes {F(q)}")
