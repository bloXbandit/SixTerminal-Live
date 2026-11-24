"""Holder's own relationships are evidence of intent. Of the pairs the 11/04
export starts on the SAME DAY, how many put two DIFFERENT trades in the SAME
room? That is planned stacking, stated by the GC's own logic rather than
inferred from dates."""
import collections, datetime as dt, json, re, sys
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
sys.path.insert(0,"/home/user/SixTerminal-Live")
exec(open(f"{SCR}/work/mdc3_stacking.py").read().split("C=load(")[0])   # reuse trade()/room()/D()
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
pairs=[]
for k,x in H.items():
    for t in re.split(r"[,;]\s*", str(x.get("succ") or "")):
        t=t.strip()
        if t in H: pairs.append((k,t))
print(f"predecessor->successor pairs in the export: {len(pairs):,}")
same=[]
for a,b in pairs:
    sa,sb=D(H[a].get("start")),D(H[b].get("start"))
    if sa and sb and sa==sb: same.append((a,b,sa))
print(f"   starting the SAME DAY: {len(same):,}")
stack=[]
for a,b,d in same:
    ra,rb=room(a,H[a].get("name","")),room(b,H[b].get("name",""))
    ta,tb=trade(H[a].get("name","")),trade(H[b].get("name",""))
    if ra and ra==rb and ta and tb and ta!=tb:
        stack.append((ra,ta,tb,d,H[a].get("name",""),H[b].get("name","")))
print(f"   of those, SAME ROOM but DIFFERENT TRADE: {len(stack):,}")
print("\n   which trade pairs Holder starts together:")
for k,v in collections.Counter(tuple(sorted((a,b))) for _,a,b,_,_,_ in stack).most_common(12):
    print(f"      {v:>4}x  {k[0]} + {k[1]}")
print("\n   where:")
for k,v in collections.Counter(r.split()[0] for r,*_ in stack).most_common(8):
    print(f"      {v:>4}  {k} rooms")
print("\n   examples:")
for r,ta,tb,d,na,nb in stack[:10]:
    print(f"      {d}  {r:<8}{ta} + {tb}")
    print(f"                  {str(na)[:46]}")
    print(f"                  {str(nb)[:46]}")
