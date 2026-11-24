"""The long-lead equipment queue. 145 LLE activities carry 171-333 day
durations and are tied nose to tail. Equipment deliveries happen in parallel
at the supplier -- one switchboard does not wait for the previous one to
arrive -- so if these are genuinely serial that is a large false driver."""
import collections, datetime as dt, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
p=load_xml(f"{SCR}/MDC3_logic_repaired_v36.xml"); compute_dates(p)
byu={a.uid:a for a in p.activities}
suc=collections.defaultdict(list); pre=collections.defaultdict(list)
for r in p.relations: suc[r.predecessor_uid].append(r); pre[r.successor_uid].append(r)
lle=[a for a in p.activities if ".LLE." in str(a.activity_id)]
print(f"LLE activities: {len(lle)}")
print(f"   durations: min {min(a.planned_duration for a in lle)/8:.0f}d  "
      f"max {max(a.planned_duration for a in lle)/8:.0f}d  "
      f"total {sum(a.planned_duration for a in lle)/8:,.0f} working days")
lset={a.uid for a in lle}
# chains entirely inside LLE
heads=[a for a in lle if not any(r.predecessor_uid in lset for r in pre[a.uid])]
print(f"\n   chain heads (no LLE predecessor): {len(heads)}")
chains=[]
for h in heads:
    c=[h]; cur=h; seen={h.uid}
    while True:
        nxt=[byu[r.successor_uid] for r in suc[cur.uid] if r.successor_uid in lset
             and r.successor_uid not in seen]
        if not nxt: break
        cur=nxt[0]; seen.add(cur.uid); c.append(cur)
    chains.append(c)
chains.sort(key=len,reverse=True)
print(f"   chains found: {len(chains)}")
print(f"\n   {'len':>4}  {'work days':>10}  {'starts':<12}{'ends':<12}head")
for c in chains[:8]:
    wd=sum(a.planned_duration for a in c)/8
    print(f"   {len(c):>4}  {wd:>10,.0f}  {str(D(c[0].early_start)):<12}"
          f"{str(D(c[-1].early_finish)):<12}{str(c[0].activity_id)}")
longest=chains[0]
print(f"\n   longest chain, {len(longest)} deliveries nose to tail:")
for a in longest[:10]:
    print(f"      {str(a.activity_id):<22}{str(a.name)[:40]:<42}"
          f"{a.planned_duration/8:>4.0f}d  {D(a.early_start)} -> {D(a.early_finish)}")
if len(longest)>10: print(f"      ... and {len(longest)-10} more")
print(f"\n   this one chain spans "
      f"{(D(longest[-1].early_finish)-D(longest[0].early_start)).days:,} calendar days")

# what if they ran in parallel instead?
print("\n" + "="*68)
print("TEST: if the deliveries ran in PARALLEL instead of nose to tail")
print("="*68)
q=load_xml(f"{SCR}/MDC3_logic_repaired_v36.xml")
qs={a.uid for a in q.activities if ".LLE." in str(a.activity_id)}
before=len(q.relations)
q.relations=[r for r in q.relations
             if not (r.predecessor_uid in qs and r.successor_uid in qs)]
print(f"   LLE-to-LLE ties removed: {before-len(q.relations)}")
compute_dates(q)
f0=max(D(a.early_finish) for a in p.activities if D(a.early_finish))
f1=max(D(a.early_finish) for a in q.activities if D(a.early_finish))
print(f"   finish now        : {f0}")
print(f"   finish in parallel: {f1}   ({(f1-f0).days:+,} days)")
n0=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
n1=sum(1 for a in q.activities if a.total_float is not None and a.total_float<0)
print(f"   negative-float activities: {n0} -> {n1}")
