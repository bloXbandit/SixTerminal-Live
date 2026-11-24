"""What is actually pushing the finish on v43, and which of it is a crew
assumption rather than a physical constraint.

Serial repetition is the thing to look for: the same work type, in different
rooms or areas, chained one after another. That is one crew moving room to
room. It is a resource decision, not a physical dependency, so it can be
broken by putting more crews on.
"""
import collections, datetime as dt, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v43.xml"); compute_dates(p, apply_dates=False)
byu={a.uid:a for a in p.activities}; wbs={w.uid:w for w in p.wbs_nodes}
pre=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r)
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
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
print(f"DRIVING CHAIN  {len(chain)} activities   {S(chain[0])} -> {F(chain[-1])}")
span=(F(chain[-1])-S(chain[0])).days
work=sum(a.planned_duration/8 for a in chain)
print(f"   span {span:,} calendar days, of which {work:,.0f} working days are actual work\n")
print("="*74); print("1.  WHERE THE TIME GOES ON THE CHAIN"); print("="*74)
for k,v in collections.Counter(area(a) for a in chain).most_common():
    d=sum(a.planned_duration/8 for a in chain if area(a)==k)
    print(f"   {v:>4} activities  {d:>6.0f} work days   {k[:50]}")
print()
print("="*74); print("2.  SERIAL REPETITION ON THE CHAIN  (one crew, room to room)"); print("="*74)
def kind(n):
    n=re.sub(r'\s*[\(\-].*','',str(n)).strip()
    return re.sub(r'\d+','N',n).strip()
runs=[]; i=0
while i<len(chain):
    j=i
    while j+1<len(chain) and kind(chain[j+1].name)==kind(chain[i].name): j+=1
    if j>i: runs.append((j-i+1,chain[i:j+1]))
    i=j+1
runs.sort(key=lambda x:-sum(a.planned_duration for a in x[1]))
print(f"   consecutive same-type runs on the chain: {len(runs)}")
for n,seg in runs[:10]:
    d=sum(a.planned_duration/8 for a in seg)
    print(f"   {n:>3} in a row, {d:>5.0f} work days   {kind(seg[0].name)[:46]}")
    print(f"        {S(seg[0])} -> {F(seg[-1])}   {', '.join(str(a.activity_id).split('.')[-2] for a in seg[:6])}")
print()
print("="*74); print("3.  THE SAME WORK TYPE, CHAINED ACROSS THE WHOLE FILE"); print("="*74)
print("   (not just on the chain -- these are the crew-flow assumptions)")
link=collections.Counter(); dur=collections.Counter()
for r in p.relations:
    q,s=byu.get(r.predecessor_uid),byu.get(r.successor_uid)
    if not q or not s: continue
    if kind(q.name)==kind(s.name) and str(q.activity_id)!=str(s.activity_id):
        link[kind(q.name)]+=1
        dur[kind(q.name)]+=s.planned_duration/8
print(f"   {'work type':<44}{'ties':>6}{'work days held in series':>26}")
for k,v in link.most_common(14):
    print(f"   {k[:43]:<44}{v:>6}{dur[k]:>20.0f} days")
