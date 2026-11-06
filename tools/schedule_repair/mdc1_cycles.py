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
rel={}
nxt=collections.defaultdict(set)
for r in p.relations:
    nxt[r.predecessor_uid].add(r.successor_uid)
    rel.setdefault((r.predecessor_uid,r.successor_uid),r)
alive={a.uid for a in p.activities}
# strongly connected components (Tarjan, iterative)
index={}; low={}; onstk={}; stk=[]; out=[]; idx=[0]
for root in alive:
    if root in index: continue
    work=[(root,iter(nxt[root]))]
    index[root]=low[root]=idx[0]; idx[0]+=1; stk.append(root); onstk[root]=True
    while work:
        v,it=work[-1]
        adv=False
        for w in it:
            if w not in alive: continue
            if w not in index:
                index[w]=low[w]=idx[0]; idx[0]+=1; stk.append(w); onstk[w]=True
                work.append((w,iter(nxt[w]))); adv=True; break
            elif onstk.get(w): low[v]=min(low[v],index[w])
        if adv: continue
        work.pop()
        if work: low[work[-1][0]]=min(low[work[-1][0]],low[v])
        if low[v]==index[v]:
            comp=[]
            while True:
                w=stk.pop(); onstk[w]=False; comp.append(w)
                if w==v: break
            if len(comp)>1: out.append(comp)
out.sort(key=len,reverse=True)
print(f"cyclic clusters: {len(out)}   activities involved: {sum(len(c) for c in out)}")
for c in out[:6]:
    print(f"\n--- cluster of {len(c)} ---")
    folders=collections.Counter(path(byu[u].wbs_uid).replace("25-1539-INT-5 / ","") for u in c)
    for k,v in folders.most_common(4): print(f"      {v:>4}  {k}")
    if len(c)<=10:
        for u in c:
            a=byu[u]
            print(f"      {a.activity_id:<26}{str(a.name)[:46]}")
# find the back-edges: within each SCC, which relation closes the loop by date?
print("\n=== relations inside cycles that run BACKWARD in time (likely the bad ones) ===")
bad=[]
for c in out:
    s=set(c)
    for u in c:
        for v in nxt[u]&s:
            a,b=byu[u],byu[v]
            fa,sb=D(a.planned_finish),D(b.planned_start)
            if fa and sb and sb<fa:
                bad.append(((fa-sb).days,a,b,rel[(u,v)]))
bad.sort(key=lambda x:-x[0])
print(f"backward-in-time relations inside cycles: {len(bad)}")
for d,a,b,r in bad[:12]:
    print(f"   {d:>4}d back  {a.activity_id:<24}({D(a.planned_start)}..{D(a.planned_finish)}) -> "
          f"{b.activity_id:<24}({D(b.planned_start)}..{D(b.planned_finish)})  {r.type}")
