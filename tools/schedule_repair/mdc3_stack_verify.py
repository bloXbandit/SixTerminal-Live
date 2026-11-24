"""Verify the stacking claims on the basis the workbook actually uses.

The workbook is 02/04 contract vs 10/07, data date 9/25/26. My first pass
used the 11/04 export. The 10/07 Exhibit S was only ever captured at WBS
band level (237 bands), so activity-level stacking cannot be measured
against it -- the 9/25 LIVE schedule is the activity-level source that
matches the workbook's data date. Running both 9/25 and 11/04 shows whether
the finding depends on which one is used.
"""
import collections, datetime as dt, json, re, sys
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
def D(v):
    s=str(v or "").replace(" A","").strip()
    if s in ("","None"): return None
    for f in ("%Y-%m-%d","%d-%b-%y","%m/%d/%Y","%d-%b-%Y"):
        try: return dt.datetime.strptime(s,f).date()
        except ValueError: continue
    return None
TRADE=[("Electrical",   r'electric|conduit|cable|busway|switchgear|switchboard|\bMSB\b|EDP|CDB|feeder|wire|wiring|pull |term|busduct|lighting|light fixture|device trim|vesda|fire alarm|\bFA\b|grounding|tray|raceway|panel ?board|UPS|generator|gen |transformer|XFMR|RMU|LBB|skid|tapbox|energi|MEG'),
       ("Mechanical",   r'mechanical|HVAC|CRAH|chiller|pump|\bpiping\b|duct(?!bank)|fan wall|\bCDU\b|air handl|cooling|dry cooler|heat exch|insulat.*pip|hydrostatic'),
       ("Fire Prot",    r'fire protection|sprinkler|fire suppression|standpipe'),
       ("Drywall/Fin",  r'drywall|hang.*tape|tape.*finish|\bpaint\b|framing|partition|ceiling|caulk|floor finish|epoxy|door|louver'),
       ("Concrete/Str", r'concrete|rebar|\bpour\b|slab|footer|foundation|precast|erection|high steel|grout|sealer|cure|backfill|excavat|stone|vapor barrier|saw cut|IMP wall|hanger'),
       ("Commissioning",r'commission|start.?up|\btest|inspect|punch|turnover|ready to|verif|QC\b')]
def trade(n):
    for lab,rx in TRADE:
        if re.search(rx,str(n),re.I): return lab
    return None
def room(k,n):
    t=f"{k} {n}".upper()
    for lab,rx in (("ER",r'\bERR?[\s.]?(\d{3})'),("GL",r'\b(?:GL|GAL)[\s.]?(\d{3})'),
                   ("DH",r'\bDH[\s.]?(\d{3})'),("MV",r'\bMV[\s.]?(\d{3})'),
                   ("HV",r'\bHVR?[\s.]?(\d{3})'),("GEN",r'\bGEN[\s.]?(\d{3})')):
        m=re.search(rx,t)
        if m: return f"{lab} {m.group(1)}"
    return None
def load(f,act_only=False):
    out=[]
    for x in json.load(open(f)):
        if act_only and str(x.get("kind","")).lower()=="wbs": continue
        s,fi=D(x.get("start")),D(x.get("finish"))
        if not(s and fi): continue
        r=room(x.get("id") or "", x.get("name") or "")
        t=trade(x.get("name") or "")
        if r and t: out.append((r,t,s,fi))
    return out
def weekly(rows):
    m=collections.defaultdict(set)
    for r,t,s,f in rows:
        d=s-dt.timedelta(days=s.weekday())
        while d<=f:
            m[(r,d)].add(t); d+=dt.timedelta(days=7)
    return m
C =load(f"{SCR}/work/mdc3_contract_0204.json")
L =load(f"{SCR}/work/mdc3_live_0925.json",act_only=True)
N =load(f"{SCR}/work/mdc3_h1104.json")
print(f"room+trade rows   contract {len(C):,}   9/25 LIVE {len(L):,}   11/04 {len(N):,}\n")
print(f"{'':<26}{'CONTRACT':>12}{'9/25 LIVE':>12}{'11/04':>12}")
ws={k:weekly(v) for k,v in (("c",C),("l",L),("n",N))}
def pct(w,k): 
    t=len(w); 
    return 100*sum(1 for v in w.values() if len(v)>=k)/t if t else 0
def cnt(w,k): return sum(1 for v in w.values() if len(v)==k)
print(f"{'room-weeks total':<26}{len(ws['c']):>12,}{len(ws['l']):>12,}{len(ws['n']):>12,}")
for k in (2,3,4,5):
    print(f"{'weeks with '+str(k)+' trades':<26}{cnt(ws['c'],k):>12,}{cnt(ws['l'],k):>12,}{cnt(ws['n'],k):>12,}")
print(f"{'3+ trades, % of weeks':<26}{pct(ws['c'],3):>11.1f}%{pct(ws['l'],3):>11.1f}%{pct(ws['n'],3):>11.1f}%")
print(f"{'4+ trades, count':<26}{sum(1 for v in ws['c'].values() if len(v)>=4):>12,}"
      f"{sum(1 for v in ws['l'].values() if len(v)>=4):>12,}"
      f"{sum(1 for v in ws['n'].values() if len(v)>=4):>12,}")
print("\ntrade presence in 3+ trade room-weeks:")
def pres(w):
    c=collections.Counter()
    for v in w.values():
        if len(v)>=3:
            for t in v: c[t]+=1
    return c
pc,pl,pn=pres(ws['c']),pres(ws['l']),pres(ws['n'])
print(f"   {'trade':<16}{'contract':>12}{'9/25 LIVE':>12}{'11/04':>12}")
for lab,_ in TRADE:
    print(f"   {lab:<16}{pc[lab]:>12,}{pl[lab]:>12,}{pn[lab]:>12,}")
# compression on the 9/25 basis
def win(rows):
    w=collections.defaultdict(lambda:[None,None])
    for r,t,s,f in rows:
        a=w[r]; a[0]=s if a[0] is None else min(a[0],s); a[1]=f if a[1] is None else max(a[1],f)
    return w
WC,WL,WN=win(C),win(L),win(N)
for lab,W in (("9/25 LIVE",WL),("11/04",WN)):
    both=sorted(set(WC)&set(W))
    dl=[((W[r][1]-W[r][0]).days)-((WC[r][1]-WC[r][0]).days) for r in both]
    comp=[d for d in dl if d<0]
    print(f"\ncompression vs contract, on {lab}: {len(both)} rooms in both")
    print(f"   compressed {len(comp)}   longer {sum(1 for d in dl if d>0)}   "
          f"same {sum(1 for d in dl if d==0)}   avg loss {sum(comp)/len(comp):.0f}d" if comp else "")
