"""Finish-to-Start ties whose successor is scheduled to start well BEFORE the
predecessor finishes. Under FS that is impossible, so either the tie is
backwards or it is spurious. These are what drive negative float without any
constraint being involved."""
import collections, datetime as dt, sys
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
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
bad=[]
for r in p.relations:
    if r.type!="Finish to Start": continue
    q,s=byu.get(r.predecessor_uid),byu.get(r.successor_uid)
    if not q or not s: continue
    fq,ss=F(q),S(s)
    if fq and ss and ss<fq:
        bad.append(((fq-ss).days,q,s,r))
bad.sort(key=lambda x:-x[0])
print(f"FS ties whose successor starts BEFORE the predecessor finishes: {len(bad)}")
b=collections.Counter()
for d,*_ in bad:
    b["over 90d" if d>90 else "31-90d" if d>30 else "8-30d" if d>7 else "1-7d"]+=1
for k in ("over 90d","31-90d","8-30d","1-7d"):
    if b[k]: print(f"   {b[k]:>4}  {k} out of sequence")
print(f"\nthe worst, with the status of each end:")
for d,q,s,r in bad[:16]:
    print(f"   {d:>4}d  {str(q.activity_id):<22}{str(q.name)[:30]:<32}[{q.status[:11]:<11}] fin {F(q)}")
    print(f"         -> {str(s.activity_id):<19}{str(s.name)[:30]:<32}[{s.status[:11]:<11}] start {S(s)}")
print()
print("how many have a COMPLETED successor and a NOT STARTED predecessor")
print("(the clearest sign the tie is simply backwards):")
rev=[x for x in bad if x[2].status=="Completed" and x[1].status=="Not Started"]
print(f"   {len(rev)}")
for d,q,s,r in rev[:10]:
    print(f"   {d:>4}d  {str(q.activity_id):<22}-> {str(s.activity_id)}")
