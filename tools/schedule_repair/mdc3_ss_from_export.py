"""Where our logic runs two activities in series but the 11/04 reference has
them starting together, the relationship type is wrong, not the dates. Find
every FS tie whose two ends overlap in the reference schedule."""
import collections, datetime as dt, json, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    for f in ("%d-%b-%y","%m/%d/%Y","%d-%b-%Y","%Y-%m-%d"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    try: return dt.date.fromisoformat(s[:10])
    except ValueError: return None
def wd(a,b):
    if a is None or b is None: return None
    sign=1 if b>=a else -1
    lo,hi=(a,b) if b>=a else (b,a)
    n,d=0,lo
    while d<hi:
        if d.weekday()<5: n+=1
        d+=dt.timedelta(days=1)
    return sign*n
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v38.xml")
byu={a.uid:a for a in p.activities}
fs=[r for r in p.relations if r.type=="Finish to Start"]
print(f"relations {len(p.relations):,}   of them FS {len(fs):,}")
both=same=overlap=0
cand=[]
for r in fs:
    q,s=byu.get(r.predecessor_uid),byu.get(r.successor_uid)
    if not q or not s: continue
    # A delivery must ARRIVE before the work that installs it, so an
    # LLE predecessor is correctly Finish to Start and is never converted.
    if ".LLE." in str(q.activity_id): continue
    hq,hs=H.get(str(q.activity_id)),H.get(str(s.activity_id))
    if not hq or not hs: continue
    both+=1
    qs,qf=D(hq.get("start")),D(hq.get("finish"))
    ss=D(hs.get("start"))
    if not(qs and qf and ss): continue
    if ss==qs:
        same+=1; cand.append(("SAME START",0,q,s,r,qs,qf,ss))
    elif ss<qf:
        overlap+=1
        cand.append(("OVERLAP",wd(qs,ss),q,s,r,qs,qf,ss))
print(f"FS ties with BOTH ends in the 11/04 export: {both:,}")
print(f"   reference shows them starting the SAME DAY : {same:,}")
print(f"   reference shows the successor starting before")
print(f"   the predecessor finishes (overlap)         : {overlap:,}")
print(f"   -> {same+overlap:,} FS ties the reference contradicts")
print()
print("sample, SAME START (these want SS+0):")
for t,lag,q,s,r,qs,qf,ss in [c for c in cand if c[0]=="SAME START"][:10]:
    print(f"   {str(q.activity_id):<22}-> {str(s.activity_id):<22}both start {qs}")
    print(f"      {str(q.name)[:34]:<36}{str(s.name)[:34]}")
print()
print("sample, OVERLAP (these want SS with the lag the reference implies):")
ov=[c for c in cand if c[0]=="OVERLAP"]
ov.sort(key=lambda c:(D(H[str(c[2].activity_id)].get('finish'))-c[7]).days,reverse=True)
for t,lag,q,s,r,qs,qf,ss in ov[:10]:
    print(f"   {str(q.activity_id):<22}-> {str(s.activity_id):<22}SS{lag:+d}d   "
          f"pred {qs}..{qf}  succ starts {ss}  (overlap {(qf-ss).days}d)")
json.dump([{"pred":str(c[2].activity_id),"succ":str(c[3].activity_id),
            "kind":c[0],"ss_lag":c[1]} for c in cand],
          open(f"{SCR}/work/mdc3_ss_candidates.json","w"),indent=0)
print(f"\nwrote {SCR}/work/mdc3_ss_candidates.json")
