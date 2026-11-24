"""The export only covers 2,595 of our 4,920 activities, so most FS ties could
not be checked against it. Our own planned dates match the export at 99.8%
where they overlap, so they stand in for the reference on the rest: same test,
wider coverage."""
import collections, datetime as dt, json, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def wd(a,b):
    if a is None or b is None: return None
    sign=1 if b>=a else -1
    lo,hi=(a,b) if b>=a else (b,a)
    n,d=0,lo
    while d<hi:
        if d.weekday()<5: n+=1
        d+=dt.timedelta(days=1)
    return sign*n
H={x["id"] for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v39.xml")
byu={a.uid:a for a in p.activities}
fs=[r for r in p.relations if r.type=="Finish to Start"]
print(f"FS ties remaining: {len(fs):,}")
cov=sum(1 for r in fs if str(byu[r.predecessor_uid].activity_id) in H
        and str(byu[r.successor_uid].activity_id) in H)
print(f"   both ends in the 11/04 export (already checked): {cov:,}")
print(f"   at least one end NOT in it (unchecked)         : {len(fs)-cov:,}")
cand=[]
for r in fs:
    q,s=byu[r.predecessor_uid],byu[r.successor_uid]
    # A delivery must ARRIVE before the work that installs it, so an
    # LLE predecessor is correctly Finish to Start and is never converted.
    if ".LLE." in str(q.activity_id): continue
    if str(q.activity_id) in H and str(s.activity_id) in H: continue
    qs,qf,ss=D(q.planned_start),D(q.planned_finish),D(s.planned_start)
    if not(qs and qf and ss): continue
    if ss==qs: cand.append(("SAME START",0,q,s))
    elif qs<ss<qf: cand.append(("OVERLAP",wd(qs,ss),q,s))
print(f"\nof the unchecked ties, contradicted by our own planned dates: {len(cand):,}")
print(f"   same start : {sum(1 for c in cand if c[0]=='SAME START'):,}")
print(f"   stacked    : {sum(1 for c in cand if c[0]=='OVERLAP'):,}")
b=collections.Counter(c[1] for c in cand if c[0]=="OVERLAP")
print(f"   stacked lag spread: {dict(sorted(b.items())[:8])}")
w={x.uid:x for x in p.wbs_nodes}
def area(a):
    u,o=a.wbs_uid,[]
    while u in w: o.append(w[u].name); u=w[u].parent_uid
    return o[-2] if len(o)>1 else "?"
print("\n   where they are:")
for k,v in collections.Counter(area(c[2]) for c in cand).most_common(6):
    print(f"      {v:>5}  {k[:50]}")
print("\n   sample:")
for t,lag,q,s in cand[:8]:
    print(f"      {t:<11}SS{lag:+d}d  {str(q.activity_id):<22}-> {str(s.activity_id)}")
    print(f"                        {str(q.name)[:34]:<36}{str(s.name)[:34]}")
json.dump([{"pred":str(c[2].activity_id),"succ":str(c[3].activity_id),
            "kind":c[0],"ss_lag":c[1]} for c in cand],
          open(f"{SCR}/work/mdc3_ss_candidates2.json","w"),indent=0)
print(f"\nwrote mdc3_ss_candidates2.json")
