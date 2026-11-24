"""MDC-3 v43: re-point the delivery ties that are holding work they should not.

Our file carries 920 procurement activities. Holder's working schedule carries
none, so 1,545 ties run from a delivery into site work with nothing on their
side to check against. 203 of those feed a successor Holder DOES carry and
gates on something else entirely.

Most of the 203 are right, though. 109 are Red Tag and other verification
steps, 33 are Set CDUs, 21 Set Fan Walls, 10 Term CRAH. Equipment has to be
physically present for all of those, so the delivery tie belongs. Only 23 are
wrong, mostly Overhead Rough In Fire Protection, which does not need a CRAH
unit on site to proceed.

Each of the 23 is re-pointed onto the predecessors HOLDER gives that successor,
taking the type from the dates: Finish to Start where Holder's predecessor
finishes before the successor starts, Start to Start where they overlap.

Where we hold none of Holder's predecessors the delivery tie is NOT deleted.
It becomes Finish to Finish instead, so the delivery still has to land before
the work can be called complete but stops dictating when it begins. Deleting
it would strand the successor, which is what cost twelve days when this was
measured as a straight deletion.
"""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v42.xml"
HRS=8.0
GEAR=re.compile(r'red tag|pink tag|orange tag|yellow tag|set cdu|set fan wall|'
                r'term crah|set electrical skid|testing and terminate|'
                r'mechanical piping to engine',re.I)
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def HD(v):
    s=str(v or "").replace(" A","").strip()
    for f in ("%Y-%m-%d","%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
def wd(a,b):
    if a is None or b is None: return 0
    sign=1 if b>=a else -1
    lo,hi=(a,b) if b>=a else (b,a)
    n,d=0,lo
    while d<hi:
        if d.weekday()<5: n+=1
        d+=dt.timedelta(days=1)
    return sign*n
def stats(p):
    compute_dates(p, apply_dates=False)
    f=max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if D(a.actual_finish) or D(a.early_finish))
    n=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return f,n
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
b=load_xml(SRC); f0,n0=stats(b)
print(f"BASELINE v42   finish {f0}   negative float {n0}\n")
p=load_xml(SRC)
byu={a.uid:a for a in p.activities}
ids={str(a.activity_id):a for a in p.activities}
lle={a.uid for a in p.activities if ".LLE." in str(a.activity_id)}
bad=[]
for r in p.relations:
    if r.predecessor_uid in lle and r.successor_uid not in lle:
        s=byu[r.successor_uid]; si=str(s.activity_id)
        if si in H and str(byu[r.predecessor_uid].activity_id) not in str(H[si].get("pred","")) \
           and not GEAR.search(str(s.name)):
            bad.append(r)
print(f"delivery ties to correct: {len(bad)}\n")
Rel=type(p.relations[0])
nxt=max(int(r.uid) for r in p.relations if str(r.uid).isdigit())+1
drop=set(); added=0; ffd=0; rows=[]
for r in bad:
    s=byu[r.successor_uid]; si=str(s.activity_id); q=byu[r.predecessor_uid]
    hp=[t.strip() for t in re.split(r"[,;]\s*",str(H[si].get("pred",""))) if t.strip()]
    # The export is missing 1,433 rows that its own pred/succ strings
    # reference, 614 of them procurement, so Holder DOES carry long lead
    # activities -- what we parsed is a filtered extract. Only predecessors
    # we can both hold AND date are usable, and re-pointing one delivery tie
    # at another delivery would not help, so those are skipped too.
    mine=[t for t in hp if t in ids and t in H and ".LLE." not in t
          and ids[t].uid!=s.uid]
    if not mine:
        r.type="Finish to Finish"; r.lag=0.0; ffd+=1
        rows.append((si,str(q.activity_id),"FF kept (no Holder predecessor we hold)"))
        continue
    drop.add((r.predecessor_uid,r.successor_uid))
    ss=HD(H[si].get("start"))
    for t in mine:
        pf=HD(H[t].get("finish")); ps=HD(H[t].get("start"))
        if pf and ss and ss>=pf:
            typ,lag="Finish to Start",wd(pf,ss)
        else:
            typ,lag="Start to Start",max(0,wd(ps,ss))
        p.relations.append(Rel(uid=str(nxt),predecessor_uid=ids[t].uid,
                               successor_uid=s.uid,type=typ,
                               lag=float(lag)*HRS))
        nxt+=1; added+=1
        rows.append((si,t,f"{typ.replace('Finish to Start','FS').replace('Start to Start','SS')}"
                        f"{'' if not lag else f'+{lag}d'}  (from Holder)"))
    s.udfs=dict(s.udfs or {})
    s.udfs["COMMENTS"]=(
        f"v43: delivery predecessor {q.activity_id} removed - this work does not "
        f"need that equipment on site. Re-pointed to Holder's own predecessor(s): "
        f"{', '.join(mine[:2])}.  " + s.udfs.get("COMMENTS",""))[:248]
p.relations=[r for r in p.relations if (r.predecessor_uid,r.successor_uid) not in drop]
print(f"   delivery ties removed and re-pointed : {len(drop)}")
print(f"   replacement ties from Holder's logic : {added}")
print(f"   kept but changed to Finish to Finish : {ffd}")
print(f"\n{'successor':<24}{'now driven by':<24}how")
for a,bb,c in rows[:26]: print(f"   {a:<24}{bb:<24}{c}")
pre={r.successor_uid for r in p.relations}
orph=[a for a in p.activities if a.uid not in pre and a.status=="Not Started"
      and not a.constraint_type]
print(f"\nactivities left with no predecessor and no pin: {len(orph)}")
f1,n1=stats(p)
print(f"\nfinish {f0} -> {f1}   ({(f1-f0).days:+d} days)   negative float {n0} -> {n1}")
out=f"{SCR}/MDC3_logic_repaired_v43.xml"
write_p6_xml(p,out,include_resources=False,include_udfs=True)
print(f"wrote {out}")
