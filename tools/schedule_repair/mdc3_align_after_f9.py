"""The alignment was measured on PLANNED dates. Does it survive F9?"""
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
    t={"ERR":"ER","MVR":"MV","HVR":"HV"}.get(m.group(1),m.group(1))
    return f"{t} {m.group(2)}"
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
hs=collections.defaultdict(list)
for k,x in H.items():
    if ".LLE." in k: continue
    r=rm(f"{k} {x.get('name','')}"); d=D(x.get("start"))
    if r and d: hs[r].append(d)
raw=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml")
pl=collections.defaultdict(list)
for a in raw.activities:
    if ".LLE." in str(a.activity_id): continue
    r=rm(f"{a.activity_id} {a.name}"); d=D(a.planned_start)
    if r and d: pl[r].append(d)
p=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml"); compute_dates(p, apply_dates=False)
f9=collections.defaultdict(list)
for a in p.activities:
    if ".LLE." in str(a.activity_id): continue
    r=rm(f"{a.activity_id} {a.name}")
    d=D(a.actual_start) or D(a.early_start)
    if r and d: f9[r].append(d)
rooms=sorted(set(hs)&set(pl)&set(f9))
print(f"{'room':<8}{'Holder':<12}{'ours planned':<14}{'gap':>6}   {'ours after F9':<15}{'gap':>6}   drift")
dp=[]; df=[]
for r in rooms:
    h=min(hs[r]); a=min(pl[r]); b=min(f9[r])
    ga,gb=(a-h).days,(b-h).days
    dp.append(ga); df.append(gb)
    print(f"{r:<8}{str(h):<12}{str(a):<14}{ga:>+5}d   {str(b):<15}{gb:>+5}d   {gb-ga:>+5}d")
print(f"\nrooms: {len(rooms)}")
def summ(g,lab):
    print(f"   {lab}")
    print(f"      same day as Holder : {sum(1 for v in g if v==0)}")
    print(f"      within 14 days     : {sum(1 for v in g if abs(v)<=14)}")
    print(f"      later than Holder  : {sum(1 for v in g if v>0)}   "
          f"(avg {sum(v for v in g if v>0)/max(1,sum(1 for v in g if v>0)):.0f}d)")
    print(f"      earlier            : {sum(1 for v in g if v<0)}")
    print(f"      mean absolute gap  : {sum(abs(v) for v in g)/len(g):.0f} days")
summ(dp,"on PLANNED dates"); summ(df,"after F9")
