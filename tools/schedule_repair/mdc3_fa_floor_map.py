import pymupdf, sys, re, collections
d=pymupdf.open(sys.argv[1])
rows=[]
for i in range(d.page_count):
    pg=d[i]
    if pg.rotation: pg.set_rotation(0)
    u=pg.get_text().upper()
    if "SHEET LIST" in u and "LEGEND AND ABBREVIATIONS" in u:
        rows.append((i+1,"SHEET LIST",u)); continue
    lv=set()
    if re.search(r'\bLEVEL ONE\b',u): lv.add("LEVEL ONE")
    if re.search(r'\bLEVEL TWO\b',u): lv.add("LEVEL TWO")
    if re.search(r'\bPENTHOUSE\b',u): lv.add("PENTHOUSE")
    rows.append((i+1, list(lv)[0] if len(lv)==1 else ("AMBIG:"+"/".join(sorted(lv)) if lv else "NONE"), u))
c=collections.Counter(l for _,l,_ in rows)
print("pages by level key:",dict(c))
print("unresolved:",[n for n,l,_ in rows if l in ("NONE",) or l.startswith("AMBIG")])
for n,l,_ in rows:
    if l.startswith("AMBIG") or l=="NONE": print(f"   p{n}: {l}")
bylvl=collections.defaultdict(lambda: collections.defaultdict(set))
RX=[("GEN",r'\bGEN(?:ERATOR)?[ .]?(\d{3})\b'),("ER",r'\bER[ .]?(\d{3})\b'),
    ("DH",r'\bDH[ .]?(\d{3})\b'),("GL",r'\bGL[ .]?(\d{3})\b'),
    ("MV",r'\bMV[ .]?(\d{3})\b'),("HV",r'\bHVR?[ .]?(\d{3})\b'),
    ("VALVE",r'VALVE\s*(?:ROOM|RM)?[ .]?(\d{3})\b')]
for n,l,u in rows:
    if l in ("SHEET LIST","NONE") or l.startswith("AMBIG"): continue
    for lab,rx in RX:
        for mm in re.finditer(rx,u): bylvl[l][lab].add(mm.group(1))
print("\n"+"="*72+"\nFLOOR MAPPING (all resolvable sheets)\n"+"="*72)
for l in ("LEVEL ONE","LEVEL TWO","PENTHOUSE"):
    print(f"\n--- {l} ---")
    for lab,_ in RX:
        v=bylvl[l].get(lab)
        if v: print(f"   {lab:<6}{len(v):>3}: {', '.join(sorted(v))}")
seen=collections.defaultdict(set)
for l in bylvl:
    for lab,v in bylvl[l].items():
        for r in v: seen[(lab,r)].add(l)
bad={f"{k[0]} {k[1]}":sorted(v) for k,v in seen.items() if len(v)>1}
print("\nrooms on MORE THAN ONE level:", bad if bad else "none - clean")
# compare against our schedule
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
p=load_xml("/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad/MDC3_logic_repaired_v34.xml")
ours=collections.defaultdict(set)
for a in p.activities:
    t=f"{a.activity_id} {a.name}"
    for lab,rx in RX:
        for mm in re.finditer(rx,t.upper()): ours[lab].add(mm.group(1))
print("\n"+"="*72+"\nSCHEDULE vs FA SET\n"+"="*72)
fa=collections.defaultdict(set)
for l in bylvl:
    for lab,v in bylvl[l].items(): fa[lab]|=v
for lab,_ in RX:
    o,f=ours.get(lab,set()),fa.get(lab,set())
    print(f"  {lab:<6} schedule {len(o):>3}  FA set {len(f):>3}   only-in-schedule {sorted(o-f) or '-'}   only-in-FA {sorted(f-o) or '-'}")
