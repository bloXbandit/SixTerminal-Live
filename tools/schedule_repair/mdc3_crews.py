"""Per work type: what comes off the finish if that crew chain is broken.
Done with explicit uid pairs -- the first attempt keyed on id() of the
relation object and silently did nothing."""
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
def kind(n):
    n=re.sub(r'\s*[\(\-].*','',str(n)).strip()
    return re.sub(r'\d+','N',n).strip()
base=load_xml(SRC); compute_dates(base, apply_dates=False)
f0=max(F(a) for a in base.activities if F(a))
byu0={a.uid:a for a in base.activities}
groups=collections.defaultdict(list)
for r in base.relations:
    q,s=byu0.get(r.predecessor_uid),byu0.get(r.successor_uid)
    if q and s and kind(q.name)==kind(s.name) and str(q.activity_id)!=str(s.activity_id):
        groups[kind(q.name)].append((r.predecessor_uid,r.successor_uid))
def test(pairs,every_other):
    p=load_xml(SRC)
    kill=set(pairs)
    if every_other:
        kill={x for i,x in enumerate(sorted(pairs)) if i%2==0}
    p.relations=[r for r in p.relations
                 if (r.predecessor_uid,r.successor_uid) not in kill]
    compute_dates(p, apply_dates=False)
    return max(F(a) for a in p.activities if F(a)), len(kill)
print(f"baseline finish {f0}\n")
print(f"{'work type (one crew walking room to room)':<44}{'ties':>5}{'2 crews':>12}{'gain':>7}{'  unlimited':>13}{'gain':>7}")
rows=[]
for k,v in sorted(groups.items(),key=lambda x:-len(x[1])):
    if len(v)<5: continue
    f2,_=test(v,True); fa,_=test(v,False)
    rows.append((k,len(v),f2,(f2-f0).days,fa,(fa-f0).days))
rows.sort(key=lambda x:x[5])
for k,n,f2,g2,fa,ga in rows[:14]:
    print(f"   {k[:41]:<44}{n:>5}{str(f2):>12}{g2:>+6}d{str(fa):>13}{ga:>+6}d")
print()
print("="*74)
print("WHY: rooms sitting ready, waiting for the crew to arrive")
print("="*74)
p=load_xml(SRC); compute_dates(p, apply_dates=False)
byu={a.uid:a for a in p.activities}
pre=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r)
ep=[a for a in p.activities if re.search(r'Electrical Pulls and Final Conn',str(a.name),re.I)]
ep.sort(key=lambda a:D(a.early_start) or dt.date(1900,1,1))
print(f"   {'room':<10}{'crew arrives':<14}{'room itself ready':<19}{'idle'}")
for a in ep[-8:]:
    crew=room=None
    for r in pre.get(a.uid,[]):
        q=byu.get(r.predecessor_uid)
        if not q or not F(q): continue
        if kind(q.name)==kind(a.name): crew=F(q)
        else: room=F(q) if room is None else max(room,F(q))
    if crew and room:
        rid=str(a.activity_id).split(".")[-2]
        print(f"   ER {rid:<7}{str(crew):<14}{str(room):<19}{(crew-room).days:>4} days")
