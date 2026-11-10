import sys,collections,datetime as dt,json,re
import openpyxl
wb=openpyxl.load_workbook(sys.argv[1],data_only=True)
ws=wb["Daily Reports"]
rows=list(ws.iter_rows(values_only=True))
H={str(x).strip():i for i,x in enumerate(rows[0]) if x}
print("columns:",list(H))
def g(r,k):
    i=H.get(k); return r[i] if i is not None and i<len(r) else None
def D(v):
    if isinstance(v,dt.datetime): return v.date()
    if isinstance(v,dt.date): return v
    try: return dt.date.fromisoformat(str(v)[:10])
    except: return None
recs=[]
for r in rows[1:]:
    pj=str(g(r,"Project") or "").strip()
    if not pj: continue
    d=D(g(r,"Report Date"))
    recs.append({"project":pj,"date":d,"day":g(r,"Day"),"foreman":g(r,"Foreman"),
                 "shift":g(r,"Shift"),"workers":g(r,"Number of Workers"),
                 "hours":g(r,"Total Labor Hours"),"status":g(r,"Work Status"),
                 "tasks":str(g(r,"Tasks") or ""),"comments":str(g(r,"Comments") or "")})
print(f"\ntotal daily reports: {len(recs)}")
print("by project:",dict(collections.Counter(x["project"] for x in recs)))
m1=[x for x in recs if x["project"].strip().upper().startswith("MDC 1")]
print(f"\nMDC 1 reports: {len(m1)}")
ds=sorted(x["date"] for x in m1 if x["date"])
print(f"   date range {ds[0]} .. {ds[-1]}")
print(f"   after 09/26: {sum(1 for d in ds if d>dt.date(2026,9,26))}")
print(f"   in 10/24-10/31 (the unstatused week): {sum(1 for d in ds if dt.date(2026,10,24)<=d<=dt.date(2026,10,31))}")
print(f"   by month: {dict(collections.Counter((d.year,d.month) for d in ds))}")
print(f"\n   foremen: {dict(collections.Counter(str(x['foreman']) for x in m1).most_common(8))}")
print(f"   work status: {dict(collections.Counter(str(x['status']) for x in m1))}")
th=sum(float(x['hours'] or 0) for x in m1)
print(f"   total labour hours logged: {th:,.0f}")
print(f"\n=== most recent 12 MDC-1 reports ===")
for x in sorted([y for y in m1 if y['date']],key=lambda y:y['date'])[-12:]:
    t=re.sub(r'\s+',' ',x['tasks'])[:150]
    print(f"\n   {x['date']} {str(x['foreman'])[:20]:<22}workers {x['workers']} hrs {x['hours']} | {x['status']}")
    print(f"      TASKS: {t}")
    if x['comments'] and str(x['comments']).lower() not in ('none','n/a','',' '):
        print(f"      NOTES: {re.sub(chr(92)+'s+',' ',str(x['comments']))[:140]}")
json.dump([{**x,"date":str(x["date"])} for x in m1],open("work/mdc1_daily.json","w"),indent=1,default=str)
print(f"\nwrote work/mdc1_daily.json")
