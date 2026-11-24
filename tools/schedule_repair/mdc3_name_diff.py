import sys, json, re, collections, difflib
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
H={x['id']:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
ours={str(a.activity_id):a for a in p.activities}
diff=[(k,str(ours[k].name).strip(),str(H[k].get('name','')).strip())
      for k in ours if k in H and str(ours[k].name).strip()!=str(H[k].get('name','')).strip()]
print(f"name differences: {len(diff)}\n")
cat=collections.Counter(); ex=collections.defaultdict(list)
for k,o,h in diff:
    if re.sub(r'\s+',' ',o).lower()==re.sub(r'\s+',' ',h).lower(): c="whitespace/case only"
    elif re.search(r'Pour\s*\d+',o,re.I) and re.search(r'Pour\s*\d+',h,re.I) \
         and re.search(r'Pour\s*(\d+)',o,re.I).group(1)!=re.search(r'Pour\s*(\d+)',h,re.I).group(1): c="POUR NUMBER differs"
    elif o.replace('**WBO','').strip()==h.replace('**WBO','').strip(): c="**WBO marker differs"
    elif o.replace('*','').strip()==h.replace('*','').strip(): c="asterisk marker differs"
    elif h.startswith(o) or o.startswith(h): c="one is truncated / longer"
    elif difflib.SequenceMatcher(None,o,h).ratio()>0.85: c="minor wording"
    else: c="DIFFERENT TEXT"
    cat[c]+=1; ex[c].append((k,o,h))
for c,n in cat.most_common():
    print(f"   {n:>4}  {c}")
print()
for c in cat:
    print(f"--- {c} ({cat[c]}) ---")
    for k,o,h in ex[c][:4]:
        print(f"   {k}")
        print(f"      ours: {o[:72]}")
        print(f"      1104: {h[:72]}")
    print()
