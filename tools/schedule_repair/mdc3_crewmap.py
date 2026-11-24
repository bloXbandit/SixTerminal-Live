"""Two questions. Can MDC-1's crew counts be mapped onto MDC-3 by activity
name, and which MV/HV rooms are short besides the six already found."""
import collections, glob, re, statistics, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def norm(n):
    n=re.sub(r'\s*[\(\-].*','',str(n)).strip()
    n=re.sub(r'\*+\s*WBO','',n,flags=re.I)
    return re.sub(r'\s+',' ',re.sub(r'\d+','',n)).strip().lower()
m1=sorted(glob.glob(f"{SCR}/MDC1_logic_repaired_v*.xml"),
          key=lambda s:int(re.search(r'v(\d+)',s).group(1)))[-1]
A=load_xml(m1); B=load_xml(f"{SCR}/MDC3_logic_repaired_v44.xml")
crew=collections.defaultdict(list)
for a in A.activities:
    v=(a.udfs or {}).get("Number of Electricians")
    if v:
        try: crew[norm(a.name)].append(float(v))
        except ValueError: pass
print(f"MDC-1 {m1.split('/')[-1]}: {len(crew):,} distinct work types carry a crew count")
hit=miss=0; rows=[]
for a in B.activities:
    v=(a.udfs or {}).get("Number of Electricians")
    if not v: continue
    k=norm(a.name)
    if k in crew:
        hit+=1
        rows.append((k,float(v),statistics.median(crew[k])))
    else: miss+=1
print(f"MDC-3 activities with a count   : {hit+miss:,}")
print(f"   work type also in MDC-1      : {hit:,}  ({100*hit/(hit+miss):.0f}%)")
print(f"   no MDC-1 equivalent          : {miss:,}")
agg=collections.defaultdict(lambda:[0,0,[]])
for k,ours,med in rows:
    agg[k][0]+=1; agg[k][1]=ours; agg[k][2].append(med)
print(f"\n{'work type':<44}{'count':>6}{'MDC-3':>8}{'MDC-1':>8}{'delta':>8}")
for k,(n,ours,meds) in sorted(agg.items(),key=lambda x:-x[1][0])[:18]:
    md=statistics.median(meds)
    print(f"   {k[:41]:<44}{n:>6}{ours:>8.0f}{md:>8.0f}{md-ours:>+8.0f}")
print()
print("="*70); print("MV / HV ROOM COMPLETENESS SWEEP"); print("="*70)
def rm(s):
    m=re.search(r'\b(MVR?|HVR?)[\s.]?(\d{3})',str(s).upper())
    return f"{m.group(1).rstrip('R')} {m.group(2)}" if m else None
def slot(a): return str(a.activity_id).split(".")[-1]
rooms=collections.defaultdict(set)
for a in B.activities:
    if ".LLE." in str(a.activity_id): continue
    r=rm(f"{a.activity_id} {a.name}")
    if r: rooms[r].add(slot(a))
mv={k:v for k,v in rooms.items() if k.startswith("MV")}
hv={k:v for k,v in rooms.items() if k.startswith("HV")}
for lab,grp in (("MV",mv),("HV",hv)):
    if not grp: continue
    full=max(len(v) for v in grp.values())
    tpl=max(grp,key=lambda k:len(grp[k]))
    print(f"\n{lab} rooms: {len(grp)}   fullest is {tpl} with {full} slots")
    for k in sorted(grp,key=lambda k:len(grp[k])):
        n=len(grp[k]); miss=sorted(grp[tpl]-grp[k])
        flag="" if n>=full else f"   short {full-n}: {', '.join(miss[:8])}{' ...' if len(miss)>8 else ''}"
        print(f"   {k:<9}{n:>4} slots{flag}")
