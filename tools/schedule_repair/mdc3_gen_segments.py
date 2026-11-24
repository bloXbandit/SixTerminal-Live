"""Which penthouse SEGMENT does each generator room sit in? The FA penthouse
segment sheets (FA02-03x) should say."""
import pymupdf, sys, re, collections
d=pymupdf.open(sys.argv[1])
hits=collections.defaultdict(set)
for i in range(d.page_count):
    pg=d[i]
    if pg.rotation: pg.set_rotation(0)
    u=pg.get_text().upper()
    if "SHEET LIST" in u: continue
    if not re.search(r'\bPENTHOUSE\b',u): continue
    if re.search(r'\bLEVEL ONE\b|\bLEVEL TWO\b',u): continue
    m=re.search(r'FA02-03([A-H]{1,2})',u)
    if not m: continue
    seg=m.group(1)
    gens={x for x in re.findall(r'\bGEN(?:ERATOR)?[ .]?(3[0-2][0-9])\b',u)}
    if gens: hits[seg]|=gens
print("penthouse segment -> generator rooms, read off the FA sheets")
for s in sorted(hits):
    print(f"   SEGMENT {s:<4}{len(hits[s]):>3} rooms: {', '.join(sorted(hits[s]))}")
allg=set().union(*hits.values()) if hits else set()
print(f"\ncovered: {len(allg)} of 28")
print("missing:", sorted(set(f'{n}' for n in range(301,329))-allg))
# are any rooms in two segments?
dup={r:[s for s in hits if r in hits[s]] for r in allg}
dup={k:v for k,v in dup.items() if len(v)>1}
print("rooms in more than one segment:", dup if dup else "none")
