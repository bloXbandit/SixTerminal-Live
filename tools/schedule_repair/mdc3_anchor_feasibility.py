"""What does Holder tie each OH rough in to, and which of those ends exist in
our file? Replicating Holder's own relationships avoids me guessing a
sequence -- high steel plainly comes BEFORE overhead rough in, so anchoring
our first room activity behind it would be wrong."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    if s in ("","None"): return None
    for f in ("%Y-%m-%d","%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml")
ours={str(a.activity_id):a for a in p.activities}
plan=json.load(open(f"{SCR}/work/mdc3_anchor_plan.json"))
seen=set(); keep=[]
for a in plan:                      # the export lists some rooms twice, under
    if a["room"] in seen: continue  # two phase prefixes; take the earlier one
    seen.add(a["room"]); keep.append(a)
print(f"anchors after de-duplicating rooms: {len(keep)}\n")
tot_p=tot_s=0
for a in keep:
    k=a["oh_id"]; x=H[k]
    pre=[t.strip() for t in re.split(r"[,;]\s*",str(x.get("pred") or "")) if t.strip()]
    suc=[t.strip() for t in re.split(r"[,;]\s*",str(x.get("succ") or "")) if t.strip()]
    mp=[t for t in pre if t in ours]; ms=[t for t in suc if t in ours]
    tot_p+=len(mp); tot_s+=len(ms)
    print(f"{a['room']:<8}{k:<22}starts {a['oh_start']}   dur {x.get('dur')}")
    print(f"   Holder gives it {len(pre)} predecessors / {len(suc)} successors; "
          f"we hold {len(mp)} / {len(ms)}")
    for t in mp[:3]:
        print(f"      PRED  {t:<24}{str(ours[t].name)[:44]}")
    for t in ms[:4]:
        print(f"      SUCC  {t:<24}{str(ours[t].name)[:44]}")
print(f"\ntotals: {tot_p} predecessors and {tot_s} successors of the anchors "
      f"already exist in our file")
json.dump(keep,open(f"{SCR}/work/mdc3_anchor_plan2.json","w"),indent=1)
