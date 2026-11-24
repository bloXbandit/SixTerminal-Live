"""Two tests. Where are the big WAITS on the chain, and what comes off if the
single-crew assumptions are broken with a second crew."""
import collections, datetime as dt, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC=f"{SCR}/MDC3_logic_repaired_v43.xml"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def F(a): return D(a.actual_finish) or D(a.early_finish)
def S(a): return D(a.actual_start) or D(a.early_start)
def kind(n):
    n=re.sub(r'\s*[\(\-].*','',str(n)).strip()
    return re.sub(r'\d+','N',n).strip()
def chain_of(p):
    byu={a.uid:a for a in p.activities}
    pre=collections.defaultdict(list)
    for r in p.relations: pre[r.successor_uid].append(r)
    fin=max((a for a in p.activities if F(a)),key=F)
    ch=[]; cur=fin; seen=set()
    while cur and cur.uid not in seen:
        seen.add(cur.uid); ch.append(cur)
        best=None
        for r in pre.get(cur.uid,[]):
            q=byu.get(r.predecessor_uid)
            if q and F(q) and (best is None or F(q)>F(best)): best=q
        cur=best
    return list(reversed(ch)),pre,byu
base=load_xml(SRC); compute_dates(base, apply_dates=False)
ch,pre,byu=chain_of(base)
f0=F(ch[-1])
print("="*74); print("THE BIG WAITS ON THE CHAIN"); print("="*74)
gaps=[]
for i in range(1,len(ch)):
    g=(S(ch[i])-F(ch[i-1])).days
    if g>7:
        lag=0
        for r in pre.get(ch[i].uid,[]):
            if r.predecessor_uid==ch[i-1].uid: lag=r.lag/8
        gaps.append((g,ch[i-1],ch[i],lag))
gaps.sort(key=lambda x:-x[0])
print(f"   gaps over a week: {len(gaps)}   totalling {sum(g for g,_,_,_ in gaps):,} days")
for g,a,b,lag in gaps[:10]:
    print(f"   {g:>4}d  {str(a.name)[:36]:<38}-> {str(b.name)[:34]}")
    print(f"          {F(a)} -> {S(b)}   {'LAG '+str(int(lag))+'d on the tie' if lag else 'no lag; waiting on other predecessors'}")
print()
print("="*74); print("ADD A SECOND CREW: break the room-to-room chains"); print("="*74)
def split(pat,label):
    p=load_xml(SRC)
    byu2={a.uid:a for a in p.activities}
    cand=[]
    for r in p.relations:
        q,s=byu2.get(r.predecessor_uid),byu2.get(r.successor_uid)
        if not q or not s: continue
        if kind(q.name)==kind(s.name) and re.search(pat,str(q.name),re.I):
            cand.append(r)
    # keep every other link: one crew becomes two
    drop={id(r) for i,r in enumerate(sorted(cand,key=lambda r:str(byu2[r.successor_uid].activity_id))) if i%2==0}
    p.relations=[r for r in p.relations if id(r) not in drop]
    compute_dates(p, apply_dates=False)
    f=max(F(a) for a in p.activities if F(a))
    return len(cand),len(drop),f
print(f"   {'work type':<42}{'ties':>6}{'cut':>5}{'finish':>13}{'gain':>8}")
for pat,lab in ((r'Electrical Pulls and Final Connections','Electrical Pulls & Final Conns'),
                (r'Overhead Rough In Fire Protection','OH Rough In Fire Protection'),
                (r'FA Device Install','FA Device Install'),
                (r'Install High Steel','Install High Steel'),
                (r'Chiller Lineup','Chiller Lineup'),
                (r'Area \d+ Erection','Crane erection (needs a 3rd crane)')):
    n,c,f=split(pat,lab)
    print(f"   {lab:<42}{n:>6}{c:>5}{str(f):<13}{(f-f0).days:>+6}d")
