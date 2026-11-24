"""Two questions:
1. If 1,338 activities moved 92 days earlier, why is the finish unchanged?
2. Do the dates in the file as delivered still match the 11/04 export,
   BEFORE the user runs their own F9?"""
import sys, json, re, collections, datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    s=str(v or "").replace(" A","").strip()
    try: return dt.date.fromisoformat(s[:10])
    except ValueError: pass
    for f in ("%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None

print("="*66)
print("Q1  why did the finish not move?")
print("="*66)
a34=load_xml(f"{SCR}/MDC3_logic_repaired_v34.xml"); compute_dates(a34)
a36=load_xml(f"{SCR}/MDC3_logic_repaired_v36.xml"); compute_dates(a36)
def F(a): return D(a.actual_finish) or D(a.early_finish) or D(a.planned_finish)
o={str(x.activity_id):x for x in a34.activities}
n={str(x.activity_id):x for x in a36.activities}
moved=[k for k in o if k in n and F(o[k]) and F(n[k]) and F(n[k])<F(o[k])]
print(f"activities that moved earlier: {len(moved):,}")
fl=[o[k].total_float/8.0 for k in moved if o[k].total_float is not None]
print(f"their float BEFORE the change:")
print(f"   min {min(fl):.0f}d   max {max(fl):.0f}d   median {sorted(fl)[len(fl)//2]:.0f}d")
print(f"   how many were on the critical path (float <= 0): "
      f"{sum(1 for f in fl if f<=0)}")
print("\n-> none of them were driving the finish. They had float, so pulling")
print("   them earlier just handed the float back. The finish is set by a")
print("   different chain entirely:")
crit=[x for x in a36.activities if x.total_float is not None and x.total_float<=0]
w={x.uid:x for x in a36.wbs_nodes}
def area(x):
    u,acc=x.wbs_uid,[]
    while u in w: acc.append(w[u].name); u=w[u].parent_uid
    return acc[-2] if len(acc)>1 else "?"
print(f"\n   {len(crit):,} activities sit on the critical path, in:")
for k,v in collections.Counter(area(x) for x in crit).most_common(6):
    print(f"      {v:>5}  {k[:52]}")
print(f"\n   did ANY of the 1,338 that moved sit on it? "
      f"{sum(1 for k in moved if n[k].total_float is not None and n[k].total_float<=0)}")

print()
print("="*66)
print("Q2  do the delivered dates still match the 11/04 export?")
print("="*66)
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
raw=load_xml(f"{SCR}/MDC3_logic_repaired_v36.xml")      # NO compute_dates
ours={str(a.activity_id):a for a in raw.activities}
both=[k for k in ours if k in H]
ds=dsame=0; df=dfsame=0
off=[]
for k in both:
    hs,hf=D(H[k].get("start")),D(H[k].get("finish"))
    os_,of=D(ours[k].planned_start),D(ours[k].planned_finish)
    if hs and os_:
        ds+=1
        if hs==os_: dsame+=1
        else: off.append((k,str(ours[k].name)[:34],os_,hs,(os_-hs).days))
    if hf and of:
        df+=1
        if hf==of: dfsame+=1
print(f"activities in both files: {len(both):,}")
print(f"   start dates matching : {dsame:,} of {ds:,}  ({100*dsame/ds:.1f}%)")
print(f"   finish dates matching: {dfsame:,} of {df:,}  ({100*dfsame/df:.1f}%)")
off.sort(key=lambda x:-abs(x[4]))
print(f"\n   the {len(off)} starts that differ, biggest first:")
for k,nm,os_,hs,g in off[:12]:
    print(f"      {k:<22}{nm:<36}ours {os_}  1104 {hs}  {g:+d}d")
