"""Where do the commissioning ladder lags come from? Check every version we
have and every source, rather than assume."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
MIL=re.compile(r'Commissioning Start|Substantial Completion',re.I)
print("THE LADDER TIES, VERSION BY VERSION")
print("(v32 predates everything I did in this session)\n")
for tag in ("v32","v36","v42","v43"):
    try: p=load_xml(f"{SCR}/MDC3_logic_repaired_{tag}.xml")
    except Exception as e: print(f"   {tag}: {e}"); continue
    byu={a.uid:a for a in p.activities}
    ties=[(str(byu[r.predecessor_uid].activity_id),
           str(byu[r.successor_uid].activity_id),r.type,r.lag/8)
          for r in p.relations
          if byu.get(r.predecessor_uid) and byu.get(r.successor_uid)
          and MIL.search(str(byu[r.predecessor_uid].name))
          and MIL.search(str(byu[r.successor_uid].name))]
    print(f"   {tag}: {len(ties)} ladder ties, {sum(t[3] for t in ties):.0f} days of lag total, "
          f"types {dict(collections.Counter(t[2][:2] for t in ties))}")
print()
print("the ties as they stand in v43:")
p=load_xml(f"{SCR}/MDC3_logic_repaired_v43.xml")
byu={a.uid:a for a in p.activities}
for r in sorted(p.relations,key=lambda r:-r.lag):
    q,s=byu.get(r.predecessor_uid),byu.get(r.successor_uid)
    if q and s and MIL.search(str(q.name)) and MIL.search(str(s.name)) and r.lag:
        print(f"   {r.lag/8:>+4.0f}d  {str(q.activity_id):<20}{str(q.name)[:34]:<36}"
              f"-> {str(s.name)[:34]}")
print()
print("="*72)
print("DO THE SOURCE SCHEDULES HAVE THESE?")
print("="*72)
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
C={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))}
ids=[str(a.activity_id) for a in p.activities if MIL.search(str(a.name))
     and str(a.activity_id).startswith("MDC3.MIL")]
print(f"   our ladder milestones: {len(ids)}")
print(f"   present in the 11/04 export : {sum(1 for k in ids if k in H)}")
print(f"   present in the 02/04 contract: {sum(1 for k in ids if k in C)}")
print(f"   referenced anywhere in 11/04 pred/succ strings: ", end="")
ref=set()
for x in H.values():
    for f in ("pred","succ"):
        for t in re.split(r"[,;]\s*",str(x.get(f) or "")):
            if t.strip(): ref.add(t.strip())
print(sum(1 for k in ids if k in ref))
print()
print("   the 02/04 contract's own commissioning milestones and dates:")
for k,x in sorted(C.items()):
    if MIL.search(str(x.get("name",""))):
        print(f"      {k:<22}{str(x.get('name',''))[:38]:<40}{x.get('start')} -> {x.get('finish')}")
