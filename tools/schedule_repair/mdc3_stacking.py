"""Trade stacking, date for date: 02/04 contract vs the current schedule.

Stacking is more than one trade in the same ROOM at the same TIME. So the
test is per room, per week: how many distinct trades are present, and is
that worse now than the contract had it.

One caution throughout. The contract carries 1,957 rows and the current
schedule 7,574, so the current one is simply broken down finer. Counting
ACTIVITIES would show stacking that is only extra detail. Counting distinct
TRADES does not have that problem: splitting one electrical activity into
four still leaves one trade in the room.
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
def load(f,idk="id"):
    out=[]
    for x in json.load(open(f)):
        s,fi=D(x.get("start")),D(x.get("finish"))
        if not(s and fi): continue
        r=room(x.get(idk) or "", x.get("name") or "")
        t=trade(x.get("name") or "")
        if r and t: out.append((r,t,s,fi))
    return out
C=load(f"{SCR}/work/mdc3_contract_0204.json")
N=load(f"{SCR}/work/mdc3_h1104.json")
print(f"room-and-trade activities  contract {len(C):,}   current {len(N):,}\n")

def weekly(rows):
    """{(room, monday): {trades}}"""
    m=collections.defaultdict(set)
    for r,t,s,f in rows:
        d=s-dt.timedelta(days=s.weekday())
        while d<=f:
            m[(r,d)].add(t); d+=dt.timedelta(days=7)
    return m
wc,wn=weekly(C),weekly(N)
print("="*72); print("1.  HOW MANY TRADES SHARE A ROOM IN A GIVEN WEEK"); print("="*72)
def dist(w,lab):
    b=collections.Counter(len(v) for v in w.values())
    tot=sum(b.values())
    print(f"\n   {lab}  ({tot:,} room-weeks)")
    for k in sorted(b):
        print(f"      {k} trade{'s' if k>1 else ' '}: {b[k]:>6,}  {100*b[k]/tot:>5.1f}%")
    stacked=sum(v for k,v in b.items() if k>=3)
    print(f"      3 or more        : {stacked:,}  ({100*stacked/tot:.1f}% of room-weeks)")
    return b,tot
bc,tc=dist(wc,"02/04 CONTRACT"); bn,tn=dist(wn,"CURRENT (11/04)")
print("\n" + "="*72); print("2.  THE CHANGE"); print("="*72)
s_c=sum(v for k,v in bc.items() if k>=3)/tc*100
s_n=sum(v for k,v in bn.items() if k>=3)/tn*100
print(f"   room-weeks with 3+ trades: {s_c:.1f}% -> {s_n:.1f}%   ({s_n-s_c:+.1f} points)")
mc=max(bc) if bc else 0; mn=max(bn) if bn else 0
print(f"   worst case in any one room-week: {mc} trades -> {mn} trades")
print("\n" + "="*72); print("3.  WHICH TRADE IS DOING THE STACKING"); print("="*72)
def pres(w):
    c=collections.Counter()
    for v in w.values():
        if len(v)>=3:
            for t in v: c[t]+=1
    return c
pc,pn=pres(wc),pres(wn)
print(f"   {'trade':<16}{'contract':>10}{'current':>10}   appears in 3+ trade room-weeks")
for lab,_ in TRADE:
    print(f"   {lab:<16}{pc[lab]:>10,}{pn[lab]:>10,}")

print("\n" + "="*72)
print("4.  PER ROOM: same scope, contract window vs now  (COMPRESSION)")
print("="*72)
def win(rows):
    w=collections.defaultdict(lambda:[None,None,set()])
    for r,t,s,f in rows:
        a=w[r]
        a[0]=s if a[0] is None else min(a[0],s)
        a[1]=f if a[1] is None else max(a[1],f)
        a[2].add(t)
    return w
WC,WN=win(C),win(N)
both=sorted(set(WC)&set(WN))
print(f"   rooms in both schedules: {len(both)}")
rows=[]
for r in both:
    cs,cf,ct=WC[r]; ns,nf,nt=WN[r]
    cd=(cf-cs).days+1; nd=(nf-ns).days+1
    rows.append((r,cs,cf,cd,ns,nf,nd,nd-cd,len(ct),len(nt)))
comp=[x for x in rows if x[7]<0]
ext=[x for x in rows if x[7]>0]
print(f"      window SHORTER now (compressed): {len(comp)}")
print(f"      window LONGER now              : {len(ext)}")
print(f"      unchanged                      : {len(rows)-len(comp)-len(ext)}")
if comp:
    print(f"      average compression on those   : {sum(x[7] for x in comp)/len(comp):.0f} days")
comp.sort(key=lambda x:x[7])
print(f"\n   {'room':<9}{'contract window':<26}{'current window':<26}{'change':>8}  trades")
for r,cs,cf,cd,ns,nf,nd,dl,tc_,tn_ in comp[:14]:
    print(f"   {r:<9}{str(cs)+' - '+str(cf):<26}{str(ns)+' - '+str(nf):<26}{dl:>+7}d  {tc_} -> {tn_}")

print("\n" + "="*72)
print("5.  THE WORST STACKED WEEKS NOW  (for the claim)")
print("="*72)
worst=sorted([(len(v),k) for k,v in wn.items() if len(v)>=4],reverse=True)
print(f"   room-weeks with 4 or more trades: {len(worst)}")
seen=collections.Counter()
for n,(r,d) in worst[:16]:
    seen[r]+=1
    print(f"   {str(d)}  {r:<8}{n} trades: {', '.join(sorted(wn[(r,d)]))}")
print(f"\n   rooms appearing most in 4+ trade weeks:")
for r,c in collections.Counter(r for _,(r,_) in worst).most_common(8):
    print(f"      {r:<10}{c} weeks")
