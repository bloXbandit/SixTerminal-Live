"""Are the 237 'different text' renames the same activity, or has Holder
reused the activity ID for different work? Dates and duration are the test:
the same activity renamed keeps roughly its dates."""
import sys, json, re, collections, datetime as dt, difflib
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
def D(v):
    s=str(v or "").replace(" A","").strip()
    try: return dt.date.fromisoformat(s[:10])
    except ValueError: pass
    for f in ("%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")   # BEFORE the rename
ours={str(a.activity_id):a for a in p.activities}
rows=[]
for k,a in ours.items():
    h=H.get(k)
    if not h: continue
    o,n=str(a.name).strip(),str(h.get("name","")).strip()
    if o==n or difflib.SequenceMatcher(None,o,n).ratio()>0.85: continue
    hs=D(h.get("start")); os_=D(a.planned_start)
    gap=abs((hs-os_).days) if hs and os_ else None
    rows.append((k,o,n,os_,hs,gap))
print(f"'different text' renames examined: {len(rows)}")
g=collections.Counter()
for *_ ,gap in rows:
    g["no date to compare" if gap is None else
      "same date (<=3d)" if gap<=3 else
      "close (<=14d)" if gap<=14 else
      "far apart (>14d)"]+=1
for k,v in g.most_common(): print(f"   {v:>4}  {k}")
print("\nthe ones whose dates are FAR APART -- these are the risky ones:")
far=[r for r in rows if r[5] is not None and r[5]>14]
far.sort(key=lambda r:-r[5])
for k,o,n,os_,hs,gap in far[:15]:
    print(f"   {k:<22}gap {gap:>4}d   ours {os_}  export {hs}")
    print(f"      ours : {o[:66]}")
    print(f"      1104 : {n[:66]}")
print(f"\n   {len(far)} of {len(rows)} are more than 14 days apart")
