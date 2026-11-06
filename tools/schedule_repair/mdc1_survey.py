import collections,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
p=load_xml(sys.argv[1])
wbs={w.uid:w for w in p.wbs_nodes}; byu={a.uid:a for a in p.activities}
def path(u):
    o,g=[],0
    while u in wbs and g<40:
        o.append(wbs[u].name); u=wbs[u].parent_uid; g+=1
    return " / ".join(reversed(o))
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
dd=D(p.data_date)
print(f"=== MDC-1 ===")
print(f"activities {len(p.activities)}   relationships {len(p.relations)}   "
      f"WBS {len(p.wbs_nodes)}   calendars {len(p.calendars)}")
print(f"data date {dd}")
print(f"status: "+"  ".join(f"{k} {v}" for k,v in collections.Counter(a.status for a in p.activities).most_common()))
print(f"constraints {sum(1 for a in p.activities if a.constraint_type)}")
print(f"labour: planned {sum(a.planned_labor_units or 0 for a in p.activities):,.0f}  "
      f"actual {sum(a.actual_labor_units or 0 for a in p.activities):,.0f}  "
      f"remaining {sum(a.remaining_labor_units or 0 for a in p.activities):,.0f}")
prv=collections.defaultdict(set); nxt=collections.defaultdict(set); adj=collections.defaultdict(set)
for r in p.relations:
    prv[r.successor_uid].add(r.predecessor_uid); nxt[r.predecessor_uid].add(r.successor_uid)
    adj[r.predecessor_uid].add(r.successor_uid); adj[r.successor_uid].add(r.predecessor_uid)
alive={a.uid for a in p.activities}
nop=[a for a in p.activities if not prv[a.uid]]
nos=[a for a in p.activities if not nxt[a.uid]]
print(f"\nopen ends: no predecessor {len(nop)} (Not Started {sum(1 for a in nop if a.status=='Not Started')})   "
      f"no successor {len(nos)}")
seen,comp,big=set(),0,0
for u in alive:
    if u in seen: continue
    comp+=1; c,q=0,collections.deque([u]); seen.add(u)
    while q:
        x=q.popleft(); c+=1
        for y in adj[x]-seen: seen.add(y); q.append(y)
    big=max(big,c)
print(f"islands {comp}   largest network {100*big//len(alive)}%")
ind=collections.Counter(); s2=collections.defaultdict(list)
for r in p.relations:
    if r.predecessor_uid in alive and r.successor_uid in alive:
        ind[r.successor_uid]+=1; s2[r.predecessor_uid].append(r.successor_uid)
q=collections.deque(u for u in alive if not ind[u]); i2,n=dict(ind),0
while q:
    u=q.popleft(); n+=1
    for v in s2[u]:
        i2[v]-=1
        if not i2[v]: q.append(v)
print(f"acyclic: {'yes' if n==len(alive) else f'NO ({len(alive)-n} in cycles)'}")
print(f"dangling relations {sum(1 for r in p.relations if r.predecessor_uid not in alive or r.successor_uid not in alive)}")
print(f"duplicate ids {sum(1 for k,v in collections.Counter(str(a.activity_id) for a in p.activities).items() if v>1)}")
print(f"\nrel types: "+"  ".join(f"{k} {v}" for k,v in collections.Counter(r.type for r in p.relations).most_common()))
print(f"\ntop-level WBS:")
root=min(p.wbs_nodes,key=lambda w:len(path(w.uid)))
for w in p.wbs_nodes:
    if w.parent_uid==root.uid:
        n=sum(1 for a in p.activities if path(a.wbs_uid).startswith(path(w.uid)))
        print(f"   {n:>5}  {w.name}")
print(f"\nactivity id prefixes:")
for k,v in collections.Counter(".".join(str(a.activity_id).split(".")[:2]) for a in p.activities).most_common(14):
    print(f"   {v:>5}  {k}")
print(f"\nactual dates range: ", end="")
af=[D(a.actual_finish) for a in p.activities if D(a.actual_finish)]
asd=[D(a.actual_start) for a in p.activities if D(a.actual_start)]
print(f"starts {min(asd) if asd else '-'} .. {max(asd) if asd else '-'}   "
      f"finishes {min(af) if af else '-'} .. {max(af) if af else '-'}")
print(f"actuals after 10/31: starts {sum(1 for x in asd if x>dt.date(2026,10,31))}  "
      f"finishes {sum(1 for x in af if x>dt.date(2026,10,31))}")
print(f"\nUDFs: ", dict(collections.Counter(k for a in p.activities for k in (a.udfs or {}))))
mx=max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()),default=0)
print(f"longest UDF value {mx}")
