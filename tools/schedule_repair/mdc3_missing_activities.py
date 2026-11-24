"""Would adding the 114 missing pour activities actually TRIM anything?
Adding activities normally lengthens a chain. The only way it shortens the
answer is if their relationships reveal parallelism we are missing. Check
what they are and how they sit relative to what we already have."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    for f in ("%d-%b-%y","%m/%d/%Y","%d-%b-%Y","%Y-%m-%d"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v40.xml")
ours={str(a.activity_id) for a in p.activities}
miss=[k for k in H if k not in ours]
print(f"activities in the 11/04 export but NOT in our file: {len(miss):,}")
pref=collections.Counter(".".join(k.split(".")[:3]) for k in miss)
print("   by prefix (top 10):")
for k,v in pref.most_common(10): print(f"      {v:>5}  {k}")
# focus on one area we also have, to see the shape
print("\n" + "="*68)
print("AREA 5, LEVEL 1 -- what the export has vs what we have")
print("="*68)
ex=sorted([k for k in H if k.startswith("MDC3.ST.L1.A5.")])
print(f"{'activity':<24}{'in ours':<9}{'start':<12}{'finish':<12}name")
for k in ex:
    h=H[k]
    print(f"{k:<24}{'yes' if k in ours else 'NO ':<9}{str(D(h.get('start'))):<12}"
          f"{str(D(h.get('finish'))):<12}{str(h.get('name',''))[:38]}")
# do the missing ones sit INSIDE a chain we have, or alongside?
print("\n" + "="*68)
print("do the missing activities overlap the ones we already have?")
print("="*68)
for area in ("MDC3.ST.L1.A5.","MDC3.ST.L1.A9.","MDC3.ST.L1.A15."):
    hav=[k for k in H if k.startswith(area) and k in ours]
    mis=[k for k in H if k.startswith(area) and k not in ours]
    if not mis: continue
    hs=[D(H[k].get("start")) for k in hav if D(H[k].get("start"))]
    ms=[D(H[k].get("start")) for k in mis if D(H[k].get("start"))]
    hf=[D(H[k].get("finish")) for k in hav if D(H[k].get("finish"))]
    mf=[D(H[k].get("finish")) for k in mis if D(H[k].get("finish"))]
    print(f"\n{area}  we have {len(hav)}, missing {len(mis)}")
    if hs: print(f"   ours    {min(hs)} -> {max(hf)}")
    if ms: print(f"   missing {min(ms)} -> {max(mf)}")
    if hs and ms:
        print("   -> the missing work runs " +
              ("INSIDE the window we already have (parallel; adding it need "
               "not lengthen anything)" if min(ms)>=min(hs) and max(mf)<=max(hf)
               else "OUTSIDE or past that window (adding it would EXTEND the chain)"))
