import collections,json,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
E=json.load(open(f"{SCR}/work/mdc1_exs.json"))
p=load_xml(f"{SCR}/MDC1_logic_repaired_v15_final.xml")
wbs={w.uid:w for w in p.wbs_nodes}; byu={a.uid:a for a in p.activities}
def path(u):
    o,g=[],0
    while u in wbs and g<40:
        o.append(wbs[u].name); u=wbs[u].parent_uid; g+=1
    return " / ".join(reversed(o))
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
GAPS=[("Heat trace", r"heat.?trace"),
      ("Horn / strobe devices", r"horn|strobe"),
      ("ERR rooms", r"\bERR\s?\d{3}|\bERR\b"),
      ("GL / gallery rooms", r"\bGL\s?\d{3}|gallery|\bGAL\d{3}"),
      ("POE rooms", r"\bPOE\s?\d{3}|\bPOE\b")]
print("="*78); print("DOES THE GC CARRY WHAT WE DO NOT?"); print("="*78)
print(f"{'scope':<26}{'ours':>7}{'GC':>7}   verdict")
res={}
for lab,rx in GAPS:
    o=[a for a in p.activities if re.search(rx,str(a.name)+" "+path(a.wbs_uid),re.I)]
    g=[a for a in E if re.search(rx,a["name"]+" "+a["id"],re.I)]
    v=("BOTH MISSING - no one schedules it" if not o and not g
       else "GC HAS IT, WE DO NOT" if not o else "we have it")
    print(f"{lab:<26}{len(o):>7}{len(g):>7}   {v}")
    res[lab]=(o,g)
for lab,(o,g) in res.items():
    if g and not o:
        print(f"\n--- GC's {lab} ({len(g)}), what it is and when ---")
        done=sum(1 for a in g if a["finished"])
        print(f"    {done} finished, {len(g)-done} not")
        for a in sorted(g,key=lambda x:str(x['start']))[:10]:
            print(f"      {a['id']:<26}{a['name'][:46]:<48}{a['start']}..{a['finish']}"
                  f"{' A' if a['finished'] else ''}")
print("\n"+"="*78)
print("WHAT OUR SCHEDULE RUNS INSTEAD - does the chain still connect?")
print("="*78)
# the rooms we DO carry on level 2, and whether anything depends on ERR/GL/POE
print("\nour Phase-level room folders:")
for k,v in collections.Counter(path(a.wbs_uid).split(" / ")[-1] for a in p.activities
        if re.search(r"Electrical Rooms|Data Hall|MV Room|HV Room",path(a.wbs_uid))).most_common(30):
    print(f"   {v:>4}  {k}")
