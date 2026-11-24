"""Trace the chain that actually carries the finish to 2030."""
import sys, datetime as dt, collections
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml"); compute_dates(p)
byu={a.uid:a for a in p.activities}
wbs={w.uid:w for w in p.wbs_nodes}
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
def F(a): return D(a.actual_finish) or D(a.early_finish) or D(a.planned_finish)
def S(a): return D(a.actual_start) or D(a.early_start) or D(a.planned_start)
last=max((a for a in p.activities if F(a)), key=F)
print(f"driving finish: {last.activity_id}  {last.name}  {F(last)}\n")
preds=collections.defaultdict(list)
for r in p.relations: preds[r.successor_uid].append(r)
chain=[]; cur=last; seen=set()
while cur and cur.uid not in seen:
    seen.add(cur.uid); chain.append(cur)
    best=None
    for r in preds.get(cur.uid,[]):
        q=byu.get(r.predecessor_uid)
        if q and F(q) and (best is None or F(q)>F(best)): best=q
    cur=best
chain.reverse()
print(f"driving chain: {len(chain)} activities, {S(chain[0])} -> {F(chain[-1])}")
print(f"span: {(F(chain[-1])-S(chain[0])).days:,} calendar days\n")
print("by area:")
for k,v in collections.Counter(area(a) for a in chain).most_common():
    print(f"   {v:>4}  {k[:58]}")
print("\nbiggest date jumps along the chain (where time is actually lost):")
gaps=[]
for i in range(1,len(chain)):
    g=(S(chain[i])-F(chain[i-1])).days
    if g>5: gaps.append((g,chain[i-1],chain[i]))
gaps.sort(reverse=True,key=lambda x:x[0])
for g,a,b in gaps[:12]:
    print(f"   {g:>4}d  {str(a.activity_id):<24}{str(a.name)[:34]:<36} {F(a)}")
    print(f"           -> {str(b.activity_id):<21}{str(b.name)[:34]:<36} {S(b)}")
print(f"\ntotal dead time in gaps >5d: {sum(g for g,_,_ in gaps):,} days")
print("\nlast 14 of the chain:")
for a in chain[-14:]:
    print(f"   {str(a.activity_id):<26}{str(a.name)[:40]:<42}{S(a)} -> {F(a)}  [{a.status}]")
