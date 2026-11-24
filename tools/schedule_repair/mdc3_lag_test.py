"""For each lag on the driving chain: is there real work inside the gap it
creates? A lag that spans a window already full of work is padding -- the
work itself sequences the two ends. A lag over an empty window is standing
in for elapsed time and should be left alone (or replaced by an activity)."""
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
byu={a.uid:a for a in p.activities}; ids={str(a.activity_id):a for a in p.activities}
wbs={w.uid:w for w in p.wbs_nodes}
def S(a): return D(a.actual_start) or D(a.early_start)
def F(a): return D(a.actual_finish) or D(a.early_finish)
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
LAGS=[("MDC3.MIL.PH3.9000","MDC3.MIL.PH3.9010",71,"PH3 Cx L3 -> L4"),
      ("MDC3.EXT.SUT.1830","MDC3.STR.DP.1000",65,"Backfill -> Deep Foundations H"),
      ("MDC3.MIL.PH1.1000","MDC3.MIL.PH2.9000",55,"PH1 Cx L3 -> PH2 Cx L3"),
      ("MDC3.MIL.PH2.9000","MDC3.MIL.PH3.9000",43,"PH2 Cx L3 -> PH3 Cx L3"),
      ("MDC3.ST.SHF.1490","MDC3.ST.PC.A1.1010",39,"Seg E Pour 1 Cure -> Area 1 Erect"),
      ("MDC3.MIL.PH3.9010","MDC3.MIL.PH3.9080",29,"PH3 Cx L4 -> L5"),
      ("MDC3.MIL.PH3.9080","MDC3.MIL.PH3.9070",14,"PH3 Cx L5 -> Sub Comp")]
for pk,sk,lag,label in LAGS:
    a,b=ids.get(pk),ids.get(sk)
    if not a or not b: continue
    g0,g1=F(a),S(b)
    inside=[x for x in p.activities if S(x) and F(x) and S(x)>=g0 and F(x)<=g1
            and x.planned_duration>0]
    print(f"\n{'='*66}\n{label}   lag {lag:+}d")
    print(f"   window {g0} -> {g1}   ({(g1-g0).days} days)")
    print(f"   activities working inside it: {len(inside)}")
    if inside:
        ar=collections.Counter(area(x) for x in inside)
        for k,v in ar.most_common(4): print(f"      {v:>5}  {k[:50]}")
        print(f"   -> the window is NOT empty; the lag duplicates work already there")
    else:
        print(f"   -> window EMPTY: the lag is standing in for real elapsed time")
