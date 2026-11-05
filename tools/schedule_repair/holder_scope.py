"""Of Holder's 5,955 activities we do not carry, which are JER electrical scope?

That is the only question that matters here. Holder schedules the whole
building; we only need the parts we are on the hook for, plus whatever gates
them. So the 5,955 get sorted into:

  OURS        electrical work JER performs -- conduit, raceway, tray, pulls,
              terminations, gear, lighting, devices, grounding, fire alarm.
  GATES US    not ours, but it releases or blocks our work in the same room --
              drywall, ceilings, floor finishes, equipment pads.
  NOT OURS    everything else: plumbing, HVAC ductwork, sprinkler, structural
              finishes with no electrical hand-off.

Then the same filter runs over our own file to see whether our side is
actually covered where Holder shows electrical work.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
H = json.load(open(f"{SCR}/work/holder_mdc3.json"))
hact = {a["id"]: a for a in H["acts"]}
p = load_xml(f"{SCR}/MDC3_logic_repaired_v22.xml")
ours = {str(a.activity_id).strip(): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


ELEC = re.compile(
    r"conduit|raceway|cable tray|\btray\b|wire|pull|terminat|\bterm\b|"
    r"switchgear|\bgear\b|panel|lighting|\blight\b|device|feeder|busway|"
    r"bus duct|in.?slab elect|ground|fire alarm|\bFA\b|VESDA|UPS|"
    r"transformer|\bXFMR\b|\bMSB\b|\bMSG\b|\bEDP\b|\bCDB\b|\bRMU\b|\bGIS\b|"
    r"\bLBB\b|\bSCR\b|generator|\bgen\b|skid|energi|start.?up|\bMEG\b|"
    r"\bQA/QC\b|isolator|penetration layout|stub.?up|electrical|\bCRAH\b|"
    r"\bCDU\b|\bPDU\b|load bank|batter|whip|j.?box|junction", re.I)
WBO = re.compile(
    r"drywall|hang tape|tape finish|\bpaint\b|caulk|ceiling|floor finish|"
    r"epoxy|\bIMP\b|framing|insulation|\bdoor\b|hardware|millwork|"
    r"equipment pad|\bF/R/P\b|curtain wall|glaz|roofing|membrane|"
    r"waterproof|flashing", re.I)
NOT = re.compile(
    r"plumbing|sanitary|\bductwork\b|\bduct\b(?!bank)|sprinkler|"
    r"fire protection|mechanical pip|chilled water|\bCHW\b|\bHVAC\b|"
    r"\bTAB\b|flush|\bfill\b|cooling tower|dry cooler|louver|\bVAV\b", re.I)


def classify(nm):
    if NOT.search(nm) and not ELEC.search(nm):
        return "NOT OURS"
    if ELEC.search(nm):
        return "OURS"
    if WBO.search(nm):
        return "GATES US"
    return "unclear"


theirs_only = sorted(set(hact) - set(ours))
print(f"Holder activities we do not carry: {len(theirs_only)}\n")
buck = collections.defaultdict(list)
for i in theirs_only:
    buck[classify(hact[i]["name"])].append(i)
print(f"{'bucket':<12}{'count':>7}")
for k in ("OURS", "GATES US", "NOT OURS", "unclear"):
    print(f"{k:<12}{len(buck[k]):>7}")

print(f"\n--- OURS: electrical work Holder schedules and we do not carry "
      f"({len(buck['OURS'])}) ---")
print("by id prefix:")
for k, v in collections.Counter(
        ".".join(i.split(".")[:3]) for i in buck["OURS"]).most_common(20):
    print(f"   {v:>5}  {k}")
print("\nby kind of work:")


def kind(nm):
    n = re.sub(r"\s*\([^)]*\)\s*$", "", str(nm)).strip()
    n = re.sub(r"\s*-\s*[A-Z]{2,4}\s*\d{3}.*$", "", n)
    return n[:46]


for k, v in collections.Counter(kind(hact[i]["name"])
                                for i in buck["OURS"]).most_common(26):
    print(f"   {v:>5}  {k}")

print(f"\n--- GATES US ({len(buck['GATES US'])}), by kind ---")
for k, v in collections.Counter(kind(hact[i]["name"])
                                for i in buck["GATES US"]).most_common(14):
    print(f"   {v:>5}  {k}")

print(f"\n--- unclear ({len(buck['unclear'])}), by kind ---")
for k, v in collections.Counter(kind(hact[i]["name"])
                                for i in buck["unclear"]).most_common(16):
    print(f"   {v:>5}  {k}")

# is our side covered where Holder shows electrical?
print("\n" + "=" * 74)
print("IS OUR SIDE COVERED?  electrical activity counts by room/area")
print("=" * 74)


def room(i, nm):
    m = re.search(r"\b((?:ER|MV|HV|HVR|GEN|Gen|DH|GL|ELC|ADM)\s*R?\d{3})\b",
                  f"{i} {nm}")
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()
    m = re.search(r"\.(ER|MV|HV|GEN|GN|GL|P|L|ELC|ADM)\.?(\d{3})", i)
    return f"{m.group(1)}{m.group(2)}".upper() if m else ""


hcount = collections.Counter()
for i, a in hact.items():
    if classify(a["name"]) == "OURS":
        r = room(i, a["name"])
        if r:
            hcount[r] += 1
ocount = collections.Counter()
for i, a in ours.items():
    if classify(str(a.name)) == "OURS":
        r = room(i, str(a.name)) or room(path(a.wbs_uid), "")
        if r:
            ocount[r] += 1
keys = sorted(set(hcount) | set(ocount))
gap = [(k, hcount[k], ocount[k]) for k in keys if hcount[k] - ocount[k] >= 8]
gap.sort(key=lambda x: -(x[1] - x[2]))
print(f"{'room/area':<12}{'Holder':>8}{'ours':>7}{'gap':>7}")
for k, h, o in gap[:30]:
    print(f"{k:<12}{h:>8}{o:>7}{h - o:>7}")
print(f"\n{len(gap)} rooms/areas where Holder carries 8+ more electrical "
      f"activities than we do")
json.dump({k: v for k, v in buck.items()},
          open(f"{SCR}/work/holder_scope.json", "w"), indent=1)
