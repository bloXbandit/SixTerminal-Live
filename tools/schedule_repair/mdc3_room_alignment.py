"""OH rough in is not JER. So the question is only whether OUR work in each
room starts when Holder's schedule says the room starts. Compare, per room."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    if s in ("","None"): return None
    for f in ("%Y-%m-%d","%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
def rm(s):
    m=re.search(r'\b(ERR|ER|MVR|MV|HVR|HV)[\s.]?(\d{3})',str(s).upper())
    if not m: return None
    t=m.group(1); t={"ERR":"ER","MVR":"MV","HVR":"HV"}.get(t,t)
    return f"{t} {m.group(2)}"
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml"); compute_dates(p, apply_dates=False)
# Holder's room start = earliest activity in that room
hs=collections.defaultdict(list)
for k,x in H.items():
    if ".LLE." in k: continue
    r=rm(f"{k} {x.get('name','')}")
    d=D(x.get("start"))
    if r and d: hs[r].append((d,k,str(x.get("name",""))))
os_=collections.defaultdict(list)
for a in p.activities:
    # deliveries are procurement, not work in the room; they sit on the
    # supplier's date and would make every room look like it starts in 2026
    if ".LLE." in str(a.activity_id): continue
    r=rm(f"{a.activity_id} {a.name}")
    d=D(a.planned_start)
    if r and d: os_[r].append((d,str(a.activity_id),str(a.name)))
print(f"{'room':<8}{'Holder starts':<14}{'with':<34}{'JER starts':<13}{'with':<30}{'gap':>7}")
rows=[]
for r in sorted(set(hs)&set(os_)):
    h=min(hs[r]); o=min(os_[r])
    gap=(o[0]-h[0]).days
    rows.append((r,h,o,gap))
for r,h,o,gap in sorted(rows,key=lambda x:-abs(x[3]))[:26]:
    print(f"{r:<8}{str(h[0]):<14}{h[2][:32]:<34}{str(o[0]):<13}{o[2][:28]:<30}{gap:>+6}d")
print(f"\nrooms compared: {len(rows)}")
g=[x[3] for x in rows]
print(f"   JER starts LATER than Holder's room start: {sum(1 for x in g if x>0)}")
print(f"   JER starts EARLIER                       : {sum(1 for x in g if x<0)}")
print(f"   same day                                 : {sum(1 for x in g if x==0)}")
late=[x for x in g if x>0]
if late: print(f"   average lateness where later: {sum(late)/len(late):.0f} days")
print("\nrooms where we have NO activity at all:")
print("   ", ", ".join(sorted(set(hs)-set(os_))) or "none")
