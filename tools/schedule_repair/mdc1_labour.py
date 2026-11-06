import collections,re,sys,datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
p=load_xml(sys.argv[1])
wbs={w.uid:w for w in p.wbs_nodes}
def path(u):
    o,g=[],0
    while u in wbs and g<40:
        o.append(wbs[u].name); u=wbs[u].parent_uid; g+=1
    return " / ".join(reversed(o))
def D(v):
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
lab=[a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
print(f"=== the {len(lab)} Labour Record activities ===")
print(f"{'id':<20}{'name':<30}{'status':<12}{'start':<12}{'finish':<12}"
      f"{'dur':>5}{'planned':>10}{'actual':>10}{'remain':>9}")
for a in sorted(lab,key=lambda x:(str(D(x.actual_start) or D(x.planned_start)))):
    print(f"{str(a.activity_id):<20}{str(a.name)[:28]:<30}{a.status:<12}"
          f"{str(D(a.actual_start) or D(a.planned_start)):<12}"
          f"{str(D(a.actual_finish) or D(a.planned_finish)):<12}"
          f"{(a.planned_duration or 0)/8:>5.0f}{a.planned_labor_units or 0:>10,.0f}"
          f"{a.actual_labor_units or 0:>10,.0f}{a.remaining_labor_units or 0:>9,.0f}")
print(f"\ntotals  planned {sum(a.planned_labor_units or 0 for a in lab):,.0f}"
      f"  actual {sum(a.actual_labor_units or 0 for a in lab):,.0f}"
      f"  remaining {sum(a.remaining_labor_units or 0 for a in lab):,.0f}")
rest=[a for a in p.activities if not str(a.activity_id).startswith("MDC1.LAB")]
print(f"everything else  planned {sum(a.planned_labor_units or 0 for a in rest):,.0f}"
      f"  actual {sum(a.actual_labor_units or 0 for a in rest):,.0f}"
      f"  remaining {sum(a.remaining_labor_units or 0 for a in rest):,.0f}")
# spread hours over time to see the histogram shape
WORK=frozenset({0,1,2,3,4,5})
def spread(acts,key):
    out=collections.Counter()
    for a in acts:
        u=getattr(a,key,0) or 0
        if not u: continue
        s=D(a.actual_start) or D(a.planned_start); f=D(a.actual_finish) or D(a.planned_finish)
        if not s or not f or f<s: 
            if s: out[(s.year,s.month)]+=u
            continue
        days=[s+dt.timedelta(days=i) for i in range((f-s).days+1)]
        days=[d for d in days if d.weekday() in WORK] or [s]
        per=u/len(days)
        for d in days: out[(d.year,d.month)]+=per
    return out
labh=spread(lab,"planned_labor_units"); resth=spread(rest,"planned_labor_units")
months=sorted(set(labh)|set(resth))
print(f"\n=== planned labour hours by month (where the jolt would show) ===")
print(f"{'month':<10}{'LAB record':>12}{'real work':>12}{'TOTAL':>12}   chart")
mx=max((labh[m]+resth[m]) for m in months) or 1
for m in months:
    t=labh[m]+resth[m]
    if t<1: continue
    bar="#"*int(40*t/mx)
    flag="  <== LAB spike" if labh[m]>resth[m] and labh[m]>1000 else ""
    print(f"{m[0]}-{m[1]:02d}   {labh[m]:>12,.0f}{resth[m]:>12,.0f}{t:>12,.0f}   {bar}{flag}")
