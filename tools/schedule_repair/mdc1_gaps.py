import collections,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
p=load_xml(f"{SCR}/MDC1_logic_repaired_v15_final.xml")
wbs={w.uid:w for w in p.wbs_nodes}
def path(u):
    o,g=[],0
    while u in wbs and g<40:
        o.append(wbs[u].name); u=wbs[u].parent_uid; g+=1
    return " / ".join(reversed(o))
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
def find(label,rx,show=6):
    hits=[a for a in p.activities if re.search(rx,str(a.name)+" "+path(a.wbs_uid),re.I)]
    op=[a for a in hits if a.status!="Completed"]
    print(f"\n{label}: {len(hits)} activities ({len(op)} unfinished)")
    for a in op[:show]:
        print(f"      {str(a.activity_id):<24}{str(a.name)[:42]:<44}{a.status:<12}"
              f"{D(a.planned_start)}  {path(a.wbs_uid)[-34:]}")
    if not hits: print("      >>> NOTHING IN THE SCHEDULE <<<")
    return hits
print("="*78); print("WHAT THE FIELD IS DOING - IS IT IN THE SCHEDULE?"); print("="*78)
find("HEAT TRACE (6 reports, roof)", r"heat.?trace")
find("LBB to LBB jumpers (Penthouse South, 7 reports)", r"LBB to LBB|jumper")
find("LBB work generally", r"\bLBB\b")
find("GEN to LBB ladder tray", r"gen to lbb|tray.*gen|gen.*tray")
find("PENTHOUSE", r"penthouse")
find("VESDA racks", r"vesda")
find("horn/strobe devices", r"horn|strobe")
print("\n"+"="*78); print("ROOMS THE FIELD NAMES - DO WE CARRY THEM?"); print("="*78)
allp=" ".join(path(a.wbs_uid)+" "+str(a.name) for a in p.activities)
for r in ["ER 212","ER 213","ER 214","ERR 202","ERR 208","ERR 209","ERR 211",
          "ERR 213","ERR 214","GL 202","GL 204","POE 203","POE 204","DH 201","ER 203","ER 209"]:
    pat=r.replace(" ",r"\s*")
    n=len([a for a in p.activities if re.search(pat,path(a.wbs_uid)+" "+str(a.name),re.I)])
    print(f"   {r:<10}{n:>5} activities" + ("   <<< NOT IN SCHEDULE" if n==0 else ""))
print("\n"+"="*78); print("3RD FLOOR / 2ND FLOOR WORK"); print("="*78)
for lab,rx in (("3rd floor",r"3rd floor|third floor|level 3|\bL3\b"),
               ("2nd floor",r"2nd floor|second floor|level 2|\bL2\b")):
    hits=[a for a in p.activities if re.search(rx,str(a.name)+" "+path(a.wbs_uid),re.I)]
    op=[a for a in hits if a.status!="Completed"]
    print(f"   {lab}: {len(hits)} activities, {len(op)} unfinished")
