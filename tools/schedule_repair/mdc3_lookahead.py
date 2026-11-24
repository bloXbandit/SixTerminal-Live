"""After F9, is the near-term work credible enough to hand a foreman?
Data date is 10/31/26, so look at what starts in the first 30, 60 and 90
days past it and sanity check the dates against the 11/04 export."""
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
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v44.xml"); compute_dates(p, apply_dates=False)
dd=D(p.data_date); print(f"data date {dd}\n")
wbs={w.uid:w for w in p.wbs_nodes}
def area(a):
    u,o=a.wbs_uid,[]
    while u in wbs: o.append(wbs[u].name); u=wbs[u].parent_uid
    return o[-2] if len(o)>1 else "?"
up=[a for a in p.activities
    if a.status=="Not Started" and D(a.early_start)
    and ".LLE." not in str(a.activity_id)]
for win in (30,60,90):
    sel=[a for a in up if dd<D(a.early_start)<=dd+dt.timedelta(days=win)]
    hrs=sum(a.planned_labor_units for a in sel)
    print(f"starting in the next {win:>2} days: {len(sel):>4} activities, {hrs:>8,.0f} h")
sel=[a for a in up if dd<D(a.early_start)<=dd+dt.timedelta(days=90)]
print(f"\nthe 90 day window, by area:")
for k,v in collections.Counter(area(a) for a in sel).most_common(8):
    print(f"   {v:>4}  {k[:54]}")
print(f"\nhow do those dates compare with Holder's for the same activities?")
both=[(a,HD(H[str(a.activity_id)].get("start"))) for a in sel
      if str(a.activity_id) in H and HD(H[str(a.activity_id)].get("start"))]
g=[(D(a.early_start)-h).days for a,h in both]
print(f"   {len(both)} of the {len(sel)} are in Holder's export")
if g:
    print(f"      same day            : {sum(1 for v in g if v==0)}")
    print(f"      within a week       : {sum(1 for v in g if abs(v)<=7)}")
    print(f"      within two weeks    : {sum(1 for v in g if abs(v)<=14)}")
    print(f"      we are EARLIER      : {sum(1 for v in g if v<0)}")
    print(f"      we are LATER        : {sum(1 for v in g if v>0)}")
    print(f"      mean absolute gap   : {sum(abs(v) for v in g)/len(g):.0f} days")
print(f"\nfirst 20 activities up, after the data date:")
sel.sort(key=lambda a:(D(a.early_start),str(a.activity_id)))
print(f"   {'start':<12}{'finish':<12}{'float':>6}  {'activity':<22}name")
for a in sel[:20]:
    print(f"   {str(D(a.early_start)):<12}{str(D(a.early_finish)):<12}"
          f"{(a.total_float or 0)/8:>5.0f}d  {str(a.activity_id):<22}{str(a.name)[:40]}")
