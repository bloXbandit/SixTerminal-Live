"""Which phase owns which MV / HV / HVR room, read off the WBS."""
import collections, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p=load_xml(f"{SCR}/MDC3_logic_repaired_v45.xml")
wbs={w.uid:w for w in p.wbs_nodes}
def path(u):
    o=[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return list(reversed(o))
def phase(u):
    for n in path(u):
        m=re.match(r'Phase (\d)',str(n))
        if m: return f"Phase {m.group(1)}"
    return "(no phase)"
rows=collections.defaultdict(lambda: collections.defaultdict(int))
for a in p.activities:
    m=re.match(r'MDC3\.(MV|HV)\.(R?\d{3})\.',str(a.activity_id))
    if not m: continue
    typ,rm=m.group(1),m.group(2)
    rows[(typ,rm)][phase(a.wbs_uid)]+=1
print(f"{'room':<10}{'phase':<12}{'activities':>11}   WBS folder")
for (typ,rm),ph in sorted(rows.items()):
    for k,v in sorted(ph.items()):
        node=next((a.wbs_uid for a in p.activities
                   if str(a.activity_id).startswith(f"MDC3.{typ}.{rm}.")
                   and phase(a.wbs_uid)==k),None)
        pp=" / ".join(path(node)[1:]) if node else ""
        print(f"{typ+' '+rm:<10}{k:<12}{v:>11}   {pp}")
print()
print("="*66); print("SUMMARY BY PHASE"); print("="*66)
byph=collections.defaultdict(lambda: collections.defaultdict(list))
for (typ,rm),ph in rows.items():
    for k in ph: byph[k][typ].append(rm)
for k in sorted(byph):
    print(f"\n{k}")
    for typ in ("MV","HV"):
        if byph[k].get(typ):
            print(f"   {typ}: {', '.join(sorted(byph[k][typ]))}")
