"""Why does Install High Steel (Gen 315) move from 12/04/26 to 06/10/27?"""
import sys, re, datetime as dt, collections
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
raw=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
planned={str(a.activity_id):(D(a.planned_start),D(a.planned_finish)) for a in raw.activities}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml"); compute_dates(p)
byu={a.uid:a for a in p.activities}
ids={str(a.activity_id):a for a in p.activities}
pre=collections.defaultdict(list)
for r in p.relations: pre[r.successor_uid].append(r)
def F(a): return D(a.actual_finish) or D(a.early_finish)
def S(a): return D(a.actual_start) or D(a.early_start)
tgt=ids["MDC3.GEN.315.1010"]
print(f"{tgt.activity_id}  {tgt.name}")
print(f"   planned  {planned[str(tgt.activity_id)][0]}")
print(f"   computed {S(tgt)}      slip {(S(tgt)-planned[str(tgt.activity_id)][0]).days:+d} days\n")
print("its predecessors:")
for r in pre[tgt.uid]:
    q=byu.get(r.predecessor_uid)
    if q:
        pl=planned.get(str(q.activity_id),(None,None))
        sl=(F(q)-pl[1]).days if F(q) and pl[1] else None
        print(f"   {str(q.activity_id):<22}{str(q.name)[:34]:<36}{r.type} lag={r.lag/8:.0f}d")
        print(f"      planned finish {pl[1]}   computed {F(q)}   slip {sl:+d}d" if sl is not None else "")
# walk the driving chain back
print("\ndriving chain back from it (worst predecessor each step):")
cur, chain, seen = tgt, [], set()
while cur and cur.uid not in seen:
    seen.add(cur.uid); chain.append(cur)
    best=None
    for r in pre.get(cur.uid,[]):
        q=byu.get(r.predecessor_uid)
        if q and F(q) and (best is None or F(q)>F(best)): best=q
    cur=best
chain.reverse()
print(f"   {len(chain)} activities\n")
for a in chain:
    pl=planned.get(str(a.activity_id),(None,None))
    sl=(S(a)-pl[0]).days if S(a) and pl[0] else 0
    mark="  <<< slip starts here" if sl>30 and abs(sl-(0 if a is chain[0] else 0))>0 else ""
    print(f"   {str(a.activity_id):<24}{str(a.name)[:34]:<36}pl {pl[0]}  f9 {S(a)}  {sl:+5d}d")
