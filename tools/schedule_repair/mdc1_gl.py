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
print("=== 1. the ERR rooms I said were missing ===")
err=[a for a in p.activities if re.search(r"ER\s?R\s?\d{3}",path(a.wbs_uid)+" "+str(a.activity_id),re.I)]
print(f"   we carry {len(err)}: folder(s) {sorted({path(a.wbs_uid).split(' / ')[-1] for a in err})}")
print("   -> my earlier regex looked for 'ERR 202'; the folder is 'ER R202'. My error.")
for a in err[:6]: print(f"      {str(a.activity_id):<24}{str(a.name)[:44]}")
print("\n=== 2. our Data Hall branch - does it cover gallery work? ===")
dh=[a for a in p.activities if ".DA.HA." in str(a.activity_id)]
print(f"   {len(dh)} activities under DA.HA")
for k,v in collections.Counter(re.sub(r"\s*\(.*?\)","",str(a.name)).strip() for a in dh).most_common(18):
    print(f"      {v:>4}  {k}")
print("\n=== 3. GC's gallery work - what KINDS, and is it our scope? ===")
gl=[a for a in E if re.search(r"GAL\d{3}",a["id"])]
ELEC=re.compile(r"conduit|raceway|tray|wire|pull|term|light|fire alarm|\bFA\b|"
    r"electrical|device|\bEDP\b|fan wall|busway|ground",re.I)
ours_=[a for a in gl if ELEC.search(a["name"])]
print(f"   GC gallery activities {len(gl)}, of which JER-electrical-looking {len(ours_)}")
for k,v in collections.Counter(re.sub(r"\s*\(.*?\)","",a["name"]).strip() for a in gl).most_common(16):
    print(f"      {v:>4}  {k}")
print("\n=== 4. what the GC's gallery electrical looks like, and its dates ===")
for a in sorted(ours_,key=lambda x:str(x['start']))[:12]:
    print(f"      {a['id']:<26}{a['name'][:46]:<48}{a['start']}..{a['finish']}{' A' if a['finished'] else ''}")
d=[D(a['finish']) for a in ours_ if D(a['finish'])]
print(f"   electrical gallery work runs {min(d)} .. {max(d)}; "
      f"{sum(1 for a in ours_ if a['finished'])} already finished")
print("\n=== 5. in OUR schedule, what sits around where gallery work would go? ===")
fw=[a for a in p.activities if re.search(r"fan wall",str(a.name),re.I)]
print(f"   our fan wall activities: {len(fw)}")
for a in fw[:8]:
    print(f"      {str(a.activity_id):<26}{str(a.name)[:44]:<46}{a.status:<12}{D(a.planned_start)}")
