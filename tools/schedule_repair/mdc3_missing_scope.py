"""The 2,654 activities Holder carries that we do not: whose trade are they?
Our file is JER's electrical scope plus the structural/shell backbone it
depends on. If the extras are other trades, they are not ours to price."""
import collections, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
H={x["id"]:x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p=load_xml(f"{SCR}/MDC3_logic_repaired_v41.xml")
ours={str(a.activity_id) for a in p.activities}
miss=[H[k] for k in H if k not in ours]
print(f"in Holder's 11/04 export, absent from our file: {len(miss):,}\n")
TRADE=[("ELECTRICAL  (ours)",  r'electric|conduit|cable|busway|switchgear|switchboard|MSB|EDP|CDB|panel|feeder|wire|pull |term |lighting|UPS|generator|transformer|XFMR|RMU|LBB|device trim|vesda|fire alarm|\bFA\b|tapbox|MEG|energi'),
       ("MECHANICAL",          r'mechanical|HVAC|CRAH|chiller|pump|piping|duct|fan wall|CDU|air handl|cooling|dry cooler|heat exch'),
       ("PLUMBING",            r'plumbing|sanitary|water|sprinkler|fire protection|flush valve'),
       ("DRYWALL / FINISHES",  r'drywall|tape|paint|ceiling|floor finish|caulk|sealant|door|louver|epoxy'),
       ("CONCRETE / STRUCT",   r'concrete|rebar|pour|slab|footer|foundation|precast|erection|steel|grout|sealer|cure|form |backfill|excavat|stone|vapor barrier|sleeve|saw cut'),
       ("ROOFING / ENVELOPE",  r'roof|waterproof|IMP|panel wall|dry.?in|flashing|membrane'),
       ("COMMISSIONING",       r'commission|start.?up|test|inspect|punch|turnover|verification|QC'),
       ("SITE / CIVIL",        r'site|grading|paving|utility|utilities|ductbank|trench|landscap|fence')]
hit=collections.Counter(); unc=[]
for x in miss:
    n=str(x.get("name",""))
    for lab,rx in TRADE:
        if re.search(rx,n,re.I): hit[lab]+=1; break
    else: hit["unclassified"]+=1; unc.append(n)
tot=len(miss)
for lab,_ in TRADE:
    if hit[lab]: print(f"   {hit[lab]:>5}  {100*hit[lab]/tot:>5.1f}%  {lab}")
print(f"   {hit['unclassified']:>5}  {100*hit['unclassified']/tot:>5.1f}%  unclassified")
print("\nsample of the ELECTRICAL ones -- these WOULD be ours:")
e=[x for x in miss if re.search(TRADE[0][1],str(x.get("name","")),re.I)]
for x in e[:12]:
    print(f"   {x['id']:<24}{str(x.get('name',''))[:52]}")
print(f"\n   {len(e):,} electrical-looking activities absent from our file")
print("\nsample unclassified:")
for n in unc[:8]: print(f"   {n[:62]}")
