"""How much of the JER electrical scope do we actually carry, against the
02/04 contract and against Holder's current 11/04 schedule.

Counted three ways, because activity count alone is misleading: the
contract is coarse (1,957 rows), the current schedule is fine (7,574), and
ours sits between them.
"""
import collections, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
ELEC=re.compile(r'electric|conduit|cable|busway|switchgear|switchboard|\bMSB\b|'
                r'EDP|CDB|feeder|wire|wiring|\bpull\b|\bterm|busduct|lighting|'
                r'light fixture|device trim|vesda|fire alarm|\bFA\b|grounding|'
                r'cable tray|raceway|panel ?board|UPS|transformer|XFMR|RMU|LBB|'
                r'skid|tapbox|energi|\bMEG\b|generator|gen ',re.I)
NOT=re.compile(r'rough in mechanical|rough in fire protection|drywall|hang tape|'
               r'\bpaint\b|floor finish|ceiling|framing|holder building control|'
               r'mechanical final|mechanical piping|insulat',re.I)
def is_elec(n):
    return bool(ELEC.search(str(n))) and not NOT.search(str(n))
C={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))
   if x.get("id") and str(x["id"])!="None"}
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v44.xml")
O={str(a.activity_id):a for a in p.activities}
ce={k for k,x in C.items() if is_elec(x.get("name",""))}
he={k for k,x in H.items() if is_elec(x.get("name",""))}
oe={k for k,a in O.items() if is_elec(a.name)}
print("JER ELECTRICAL ACTIVITIES, BY SOURCE")
print(f"   02/04 contract      {len(C):>6,} rows   {len(ce):>5,} electrical")
print(f"   11/04 Holder        {len(H):>6,} rows   {len(he):>5,} electrical")
print(f"   our v44             {len(O):>6,} rows   {len(oe):>5,} electrical")
print()
print("COVERAGE, matching on activity id")
print(f"   contract electrical we carry : {len(ce&oe):>5,} of {len(ce):,}"
      f"   {100*len(ce&oe)/len(ce):>5.1f}%")
print(f"   Holder electrical we carry   : {len(he&oe):>5,} of {len(he):,}"
      f"   {100*len(he&oe)/len(he):>5.1f}%")
print(f"   ours not in Holder's list    : {len(oe-he):>5,}")
print()
print("CONTRACT ELECTRICAL WE DO NOT CARRY")
miss=sorted(ce-oe)
print(f"   {len(miss)} activities")
c=collections.Counter(re.sub(r'\s*[-(].*','',str(C[k].get("name",""))).strip() for k in miss)
for k,v in c.most_common(12): print(f"      {v:>4}x  {k[:56]}")
print()
print("BY HOURS")
tot=sum(a.planned_labor_units for a in p.activities)
el=sum(a.planned_labor_units for a in p.activities if is_elec(a.name))
print(f"   hours in our file, all        : {tot:>9,.0f} h")
print(f"   of which electrical by name   : {el:>9,.0f} h   {100*el/tot:.0f}%")
print(f"   pricing basis, projected 11/8 : {605707:>9,} h")
print(f"   our file against that basis   : {100*tot/605707:>9.0f}%")
