import collections,json,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
E=json.load(open(f"{SCR}/work/mdc1_exs.json"))
p=load_xml(f"{SCR}/MDC1_logic_repaired_v16.xml")
wbs={w.uid:w for w in p.wbs_nodes}; byu={a.uid:a for a in p.activities}
def path(u):
    o,g=[],0
    while u in wbs and g<40:
        o.append(wbs[u].name); u=wbs[u].parent_uid; g+=1
    return " / ".join(reversed(o))
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
DD=D(p.data_date)
WBO=re.compile(r"\*+\s*WBO|by others",re.I)
ELEC=re.compile(r"conduit|raceway|tray|wire|pull|\bterm\b|terminat|switchgear|"
    r"panel|light|device|feeder|busway|ground|fire alarm|\bFA\b|vesda|\bUPS\b|"
    r"transformer|\bXFMR\b|\bMSB\b|\bRMU\b|\bLBB\b|\bSCR\b|skid|energi|start.?up|"
    r"isolator|electrical|rough.?in|\bEDP\b|\bPDP\b|high steel|hanger",re.I)
print("="*76); print("A. SCOPE COVERAGE - do we carry the work at all?"); print("="*76)
def room(s):
    s=str(s).upper().replace("."," ")
    m=re.search(r"\b(ER|ERR|MV|HV|GEN|GL|GAL|DH|DAT|ELEC|POE)\s*R?\s?(\d{3})\b",s)
    if not m: return ""
    k=m.group(1); k="GL" if k in("GAL",) else ("DH" if k=="DAT" else k)
    k="ERR" if k=="ERR" else k
    return f"{k}{m.group(2)}"
gc=collections.Counter(); ou=collections.Counter()
for a in E:
    if ELEC.search(a["name"]):
        r=room(a["id"]+" "+a["name"])
        if r: gc[r]+=1
for a in p.activities:
    if WBO.search(str(a.name)) or not ELEC.search(str(a.name)): continue
    r=room(str(a.activity_id)+" "+str(a.name)) or room(path(a.wbs_uid))
    if r: ou[r]+=1
have=[r for r in gc if ou.get(r)]
miss=[r for r in gc if not ou.get(r)]
gchave=sum(gc[r] for r in have); gcmiss=sum(gc[r] for r in miss)
print(f"rooms/areas where the GC has JER-electrical work: {len(gc)}")
print(f"   we carry something there:  {len(have)}  ({gchave} of their activities)")
print(f"   we carry nothing there:    {len(miss)}  ({gcmiss} of their activities)")
print(f"   -> scope coverage by room: {100*len(have)//max(len(gc),1)}%   "
      f"by their activity count: {100*gchave//max(gchave+gcmiss,1)}%")
print(f"   rooms we miss: {sorted(miss)[:14]}")
print("\n"+"="*76); print("B. STATUS TRUTH - how is each completion backed?"); print("="*76)
src=collections.Counter()
for a in p.activities:
    c=str((a.udfs or {}).get("COMMENTS",""))
    if a.status=="Completed":
        if "Exhibit S" in c and "inference" not in c: src["GC reported"]+=1
        elif "by inference" in c: src["inferred from logic"]+=1
        elif "daily report" in c: src["JER daily report"]+=1
        else: src["pre-existing (unknown provenance)"]+=1
    elif a.status=="In Progress":
        if "daily report" in c: src["in progress - daily report"]+=1
        else: src["in progress - pre-existing"]+=1
tot=sum(v for k,v in src.items())
for k,v in src.most_common(): print(f"   {v:>5}  {k}")
print("\n"+"="*76); print("C. DATE CREDIBILITY - are the dates a forecast or a fossil?"); print("="*76)
ns=[a for a in p.activities if a.status=="Not Started" and D(a.planned_start)]
stale=[a for a in ns if D(a.planned_start)<DD]
pinned=[a for a in ns if a.constraint_type and D(a.constraint_date) and D(a.constraint_date)<DD]
fut=[a for a in ns if D(a.planned_start)>=DD]
print(f"not-started with a date: {len(ns)}")
print(f"   start date already passed (stale):      {len(stale)}  ({100*len(stale)//len(ns)}%)")
print(f"   of those, pinned by a stale constraint: {len(pinned)}")
print(f"   start date still ahead of the data date:{len(fut)}  ({100*len(fut)//len(ns)}%)")
print(f"\n-> only {100*len(fut)//len(ns)}% of remaining work carries a date that is even "
      f"arithmetically possible")
print("\n"+"="*76); print("D. COMPLETION - ours vs theirs, same yardstick"); print("="*76)
oe=[a for a in p.activities if not WBO.search(str(a.name)) and ELEC.search(str(a.name))]
od=sum(1 for a in oe if a.status=="Completed")
ge=[a for a in E if ELEC.search(a["name"])]
gd=sum(1 for a in ge if a["finished"])
print(f"ours : {od:>5} of {len(oe):>5} electrical activities complete = {100*od//len(oe)}%")
print(f"GC   : {gd:>5} of {len(ge):>5} electrical activities complete = {100*gd//len(ge)}%")
