"""Full MV room breakdown: what each room carries, against the fullest room
in our own file and against Holder's count for the same room."""
import collections, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
H=[x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))]
p=load_xml(f"{SCR}/MDC3_logic_repaired_v44.xml")
def mv(s):
    m=re.search(r'\bMVR?[\s.]?(\d{3})',str(s).upper())
    return m.group(1) if m else None
ours=collections.defaultdict(list); hold=collections.defaultdict(list)
for a in p.activities:
    if ".LLE." in str(a.activity_id): continue
    r=mv(f"{a.activity_id} {a.name}")
    if r: ours[r].append(a)
for x in H:
    if ".LLE." in str(x.get("id","")): continue
    r=mv(f"{x.get('id','')} {x.get('name','')}")
    if r: hold[r].append(x)
rooms=sorted(set(ours)|set(hold))
print(f"MV ROOMS IN MDC-3: {len(rooms)}   {', '.join('MV '+r for r in rooms)}\n")
print(f"{'room':<9}{'ours':>6}{'Holder':>8}{'our hours':>11}   status")
for r in rooms:
    n=len(ours.get(r,[])); hn=len(hold.get(r,[]))
    hrs=sum(a.planned_labor_units for a in ours.get(r,[]))
    st=("NO ACTIVITIES" if n==0 else
        "thin" if n<8 else
        "partial" if n<14 else "looks complete")
    print(f"MV {r:<6}{n:>6}{hn:>8}{hrs:>11,.0f}   {st}")
# the fullest room in our file as the template
best=max(ours,key=lambda r:len(ours[r]))
print(f"\nFULLEST ROOM WE CARRY: MV {best}  ({len(ours[best])} activities) -- use as the template")
for a in sorted(ours[best],key=lambda a:str(a.activity_id)):
    print(f"   {str(a.activity_id):<24}{str(a.name)[:52]:<54}{a.planned_duration/8:>4.0f}d")
# what Holder has that no MV room of ours has
print(f"\nWORK TYPES HOLDER HAS IN MV ROOMS THAT WE CARRY IN NONE OF THEM")
def kind(n): return re.sub(r'\s*[-(].*','',str(n)).strip()
ok={kind(a.name) for v in ours.values() for a in v}
hk=collections.Counter(kind(x.get("name","")) for v in hold.values() for x in v)
miss=[(k,v) for k,v in hk.most_common() if k not in ok]
for k,v in miss[:18]: print(f"   {v:>4} rooms   {k[:56]}")
print(f"   ({len(miss)} work types)")
