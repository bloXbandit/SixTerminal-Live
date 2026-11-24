"""Your idea: make the delivery ties Finish to Finish instead of Finish to
Start. The delivery still has to land before the work can be COMPLETE, which
is the real constraint, but it stops dictating when the work can begin.
Compare doing nothing, FF, and deleting them outright."""
import collections, datetime as dt, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except (TypeError,ValueError): return None
def HD(v):
    s=str(v or "").replace(" A","").strip()
    for f in ("%Y-%m-%d","%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
def F(a): return D(a.actual_finish) or D(a.early_finish)
def S(a): return D(a.actual_start) or D(a.early_start)
def rm(s):
    m=re.search(r'\b(ERR|ER|MVR|MV|HVR|HV)[\s.]?(\d{3})',str(s).upper())
    if not m: return None
    t={"ERR":"ER","MVR":"MV","HVR":"HV"}.get(m.group(1),m.group(1))
    return f"{t} {m.group(2)}"
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
hs=collections.defaultdict(list)
for k,x in H.items():
    if ".LLE." in k: continue
    r=rm(f"{k} {x.get('name','')}"); d=HD(x.get("start"))
    if r and d: hs[r].append(d)
def targets(proj):
    byu={a.uid:a for a in proj.activities}
    lle={a.uid for a in proj.activities if ".LLE." in str(a.activity_id)}
    out=set()
    for r in proj.relations:
        if r.predecessor_uid in lle and r.successor_uid not in lle:
            si=str(byu[r.successor_uid].activity_id)
            if si in H and str(byu[r.predecessor_uid].activity_id) not in str(H[si].get("pred","")):
                out.add((r.predecessor_uid,r.successor_uid))
    return out
def run(mode):
    p=load_xml(f"{SCR}/MDC3_logic_repaired_v42.xml")
    tg=targets(p)
    if mode=="delete":
        p.relations=[r for r in p.relations if (r.predecessor_uid,r.successor_uid) not in tg]
    elif mode=="ff":
        for r in p.relations:
            if (r.predecessor_uid,r.successor_uid) in tg:
                r.type="Finish to Finish"; r.lag=0.0
    compute_dates(p, apply_dates=False)
    fin=max(F(a) for a in p.activities if F(a))
    mine=collections.defaultdict(list)
    for a in p.activities:
        if ".LLE." in str(a.activity_id): continue
        r=rm(f"{a.activity_id} {a.name}"); d=S(a)
        if r and d: mine[r].append(d)
    rooms=sorted(set(hs)&set(mine))
    g=[(min(mine[r])-min(hs[r])).days for r in rooms]
    neg=sum(1 for a in p.activities if a.total_float is not None and a.total_float<0)
    return fin,len(tg),sum(1 for v in g if abs(v)<=14),sum(abs(v) for v in g)/len(g),neg,p
print(f"{'option':<34}{'finish':<13}{'rooms within 14d':>18}{'mean gap':>10}{'neg float':>11}")
res={}
for mode,lab in (("none","leave as is (FS)"),
                 ("ff","make them Finish to Finish"),
                 ("delete","delete them")):
    fin,n,w14,mg,neg,p=run(mode)
    res[mode]=(fin,p)
    print(f"{lab:<34}{str(fin):<13}{w14:>14} of 38{mg:>9.0f}d{neg:>11}")
print(f"\nties affected: {run('none')[1]}")
b=res["none"][1]; f=res["ff"][1]
o={str(a.activity_id):F(a) for a in b.activities}
n={str(a.activity_id):F(a) for a in f.activities}
mv=[(k,(n[k]-o[k]).days) for k in o if k in n and o[k] and n[k] and o[k]!=n[k]]
e=[x for x in mv if x[1]<0]; l=[x for x in mv if x[1]>0]
print(f"FF vs now: {len(mv):,} activities move   earlier {len(e):,}"
      f"{f' (avg {sum(x[1] for x in e)/len(e):.0f}d)' if e else ''}   later {len(l):,}"
      f"{f' (avg +{sum(x[1] for x in l)/len(l):.0f}d)' if l else ''}")
