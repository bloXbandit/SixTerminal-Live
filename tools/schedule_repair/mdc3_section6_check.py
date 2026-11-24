"""Are the section 6 pairs -- electrical rough in and drywall framing starting
the same day in the same room -- actually tied Start to Start in our XML?"""
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
EL=re.compile(r'Overhead Rough In Electrical',re.I); DW=re.compile(r'Drywall Framing',re.I)
pairs=[]
for k,x in H.items():
    for t in re.split(r"[,;]\s*", str(x.get("succ") or "")):
        t=t.strip()
        if t not in H: continue
        na,nb=str(x.get("name","")),str(H[t].get("name",""))
        if not((EL.search(na) and DW.search(nb)) or (DW.search(na) and EL.search(nb))): continue
        if D(x.get("start"))==D(H[t].get("start")): pairs.append((k,t))
print(f"section 6 pairs (from the export): {len(pairs)}\n")
p=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml")
uid={str(a.activity_id):a.uid for a in p.activities}
nm={str(a.activity_id):str(a.name) for a in p.activities}
rel={}
for r in p.relations: rel[(r.predecessor_uid,r.successor_uid)]=r
print(f"{'predecessor':<24}{'successor':<24}{'in our XML?':<14}{'type now'}")
c=collections.Counter()
for a,b in sorted(pairs):
    ua,ub=uid.get(a),uid.get(b)
    if ua is None or ub is None:
        c["one end not in our file"]+=1
        print(f"{a:<24}{b:<24}{'MISSING END':<14}-"); continue
    r=rel.get((ua,ub)) or rel.get((ub,ua))
    if r is None:
        c["no relationship at all"]+=1
        print(f"{a:<24}{b:<24}{'NO TIE':<14}-"); continue
    t=r.type.replace("Start to Start","SS").replace("Finish to Start","FS").replace("Finish to Finish","FF")
    c[t]+=1
    print(f"{a:<24}{b:<24}{'yes':<14}{t}{'' if not r.lag else f'  lag {r.lag/8:+.0f}d'}")
print("\nsummary:", dict(c))
