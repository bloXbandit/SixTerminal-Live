"""Scope level coverage, not activity level.

The previous check matched activity names and counted "Engine Start Up and
Burn-ins (Gen 315)" as missing because the contract calls the same work
"Start ups of Equipment". That was wrong. This asks a different question:
for each scope of work, does our schedule carry it, and how much.
"""
import collections, json, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR=("/tmp/claude-0/-home-user-SixTerminal-Live/"
     "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SCOPE=[
 ("Underground / in-slab",  r'underground|in-?slab|duct ?bank|stub up|sleeve'),
 ("Overhead rough in",      r'overhead rough in electric|oh branch|overhead branch|'
                            r'branch raceway|ladder tray|cable tray|oh ladder'),
 ("Conduit & raceway",      r'conduit|raceway|\bemt\b|\brmc\b'),
 ("Wall rough in",          r"wall rough|in-?wall|wall penetration"),
 ("Feeders & pulls",        r'\bpull\b|feeder|pull mc|lbb to'),
 ("Terminations",           r'\bterm\b|terminat|final connection|tie-?in|tapbox'),
 ("Switchgear & MV",        r'switchgear|\bMSB\b|\bRMU\b|medium voltage|\bMV\b|'
                            r'transformer|XFMR|\bGIS\b|CSB|CDB'),
 ("Busway / bus duct",      r'busway|bus ?duct'),
 ("Generators",             r'generator|\bgen \d|engine start|burn.?in|\bLBB\b|radiator'),
 ("Equipment skids",        r'skid'),
 ("UPS / DC power",         r'\bUPS\b|battery|DC power'),
 ("Lighting",               r'lighting|light fixture'),
 ("Devices & trim out",     r'device trim|trim out|receptacle|wiring device'),
 ("Grounding",              r'grounding|ground ring|lightning'),
 ("Fire alarm",             r'fire alarm|\bFA\b|FA device'),
 ("VESDA / incipient",      r'vesda|incipient|air sampling'),
 ("Security / telecom / BAS",r'security|telecom|wifi|\bDAS\b|\bBAS\b|ethernet|controls rack'),
 ("Testing & QC",           r'\bMEG\b|\bQC\b|testing|inspection|megger'),
 ("Startup & energisation", r'start.?up|energiz|energis|ready to energize|red tag|'
                            r'pink tag|orange tag|yellow tag'),
 ("Commissioning support",  r'commission|\bcx\b|level [345]'),
]
def bucket(n):
    for lab,rx in SCOPE:
        if re.search(rx,str(n),re.I): return lab
    return None
C=[x for x in json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))]
H=[x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))]
p=load_xml(f"{SCR}/MDC3_logic_repaired_v44.xml")
c=collections.Counter(); h=collections.Counter()
o=collections.Counter(); oh=collections.Counter()
for x in C:
    b=bucket(x.get("name",""))
    if b: c[b]+=1
for x in H:
    b=bucket(x.get("name",""))
    if b: h[b]+=1
for a in p.activities:
    b=bucket(a.name)
    if b: o[b]+=1; oh[b]+=a.planned_labor_units
print(f"{'scope':<28}{'contract':>9}{'Holder':>8}{'ours':>7}{'our hours':>11}   carried?")
for lab,_ in SCOPE:
    mark = "yes" if o[lab] else ("NOT CARRIED" if (c[lab] or h[lab]) else "-")
    print(f"{lab:<28}{c[lab]:>9}{h[lab]:>8}{o[lab]:>7}{oh[lab]:>11,.0f}   {mark}")
print()
cov=[lab for lab,_ in SCOPE if o[lab]]
gap=[lab for lab,_ in SCOPE if not o[lab] and (c[lab] or h[lab])]
print(f"scopes carried          : {len(cov)} of {len(SCOPE)}")
print(f"scopes NOT carried      : {len(gap)}   {gap if gap else ''}")
tot=sum(a.planned_labor_units for a in p.activities)
print(f"\nhours in the file       : {tot:,.0f}")
print(f"hours in a named scope  : {sum(oh.values()):,.0f}  ({100*sum(oh.values())/tot:.0f}%)")
print(f"pricing basis 11/8      : 605,707   -> file is {100*tot/605707:.0f}% of it")
