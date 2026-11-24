"""The 11 pinned orphans: does the 11/04 export name a real predecessor for
them? A pin is a last resort -- real logic is better."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    for f in ("%d-%b-%y","%m/%d/%Y","%d-%b-%Y","%Y-%m-%d"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    try: return dt.date.fromisoformat(s[:10])
    except ValueError: return None
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v41.xml")
ours={str(a.activity_id):a for a in p.activities}
pre={r.successor_uid for r in p.relations}
orph=[a for a in p.activities
      if a.uid not in pre and a.status=="Not Started"
      and "v41: left with no predecessor" in str((a.udfs or {}).get("COMMENTS",""))]
print(f"the {len(orph)} activities stranded by the v41 removals:\n")
fix=[]
for a in orph:
    k=str(a.activity_id)
    h=H.get(k)
    print(f"{k}  {str(a.name)[:46]}")
    print(f"   planned {D(a.planned_start)} -> {D(a.planned_finish)}")
    if not h:
        print("   not in the 11/04 export - no predecessor available\n"); continue
    toks=[t.strip() for t in re.split(r"[,;]\s*", str(h.get("pred") or "")) if t.strip()]
    usable=[t for t in toks if t in ours and t!=k]
    print(f"   export names {len(toks)} predecessor(s); {len(usable)} exist in our file")
    for t in usable:
        q=ours[t]
        hq=H.get(t,{})
        qf=D(hq.get("finish")); ss=D(h.get("start"))
        ok = qf and ss and ss>=qf
        print(f"      {t:<24}{str(q.name)[:32]:<34}ref finishes {qf}  "
              f"-> this starts {ss}   {'OK (FS works)' if ok else 'OVERLAP - would need SS'}")
        if ok: fix.append((t,k))
    print()
print(f"clean FS predecessors available for: "
      f"{len({k for _,k in fix})} of {len(orph)}")
json.dump([{"pred":a,"succ":b} for a,b in fix],
          open(f"{SCR}/work/mdc3_orphan_fix.json","w"),indent=0)
