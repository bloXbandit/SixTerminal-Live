"""MDC-1 v21: load the handful of finished work that genuinely lacks hours,
and carry JER's own comments on Holder's Exhibit S.

THE 366 WAS WRONG, AND THIS IS THE CORRECTION.

The earlier count of "366 unloaded completions, 45% of finished work" came
from a filter that was too weak. Of the 919 finished non-WBO activities, 447
are design, funding, procurement and owner-approval items -- Site Plan
Development, Precast OAA, LLE Specs, Issue for Pricing, 100% CDs/IFCs
Received -- which are supposed to carry no field hours. A further 45 sit in
the Long Lead Equipment folders and are equipment deliveries, not installs;
they only matched because their names contain UPS and Switchgear.

Filtering positively for electrical field work instead, and dropping the
procurement folders, leaves 1,358 field activities carrying 229,745 hours.
Of the 427 finished ones, 56 carry no hours, and they are all MEP Site
Underground, in a repeating four-step set per area: Excavations, Power,
Telecom, Plumbing. Only Power, the two Electrical East/West runs and the
Ductbanks are JER's; Excavations, Plumbing and Telecom belong to other
trades and are left at zero deliberately.

So the loading gap is about 16 activities and some 2,500 hours, not 366 and
58,267. And the headline that went with it was also wrong: field earned
value is 26% by hours (59,392 of 229,745), not 21%, and the CPI of 0.23 was
an artifact of dividing whole-file budget by a spend that includes the
payroll branch. 26% sits one point off the 27% Holder reports, so the
percent complete was not lying after all.

WHAT ELSE IS IN HERE. Holder's 11/03 export came back carrying 52 JER review
comments that name what is actually holding the work: a Holder permit issue
on wall close-ins and device trim-outs across nine rooms, a DEV issue with no
path forward on the HV rooms, MC Dean skids not on site, Southland blocking
the chiller sets, Siemens not setting GIS, and nine electrical tie-ins the GC
carries at zero days. Those are dated JER positions and the file should hold
them. The two schedules share only 74 of 2,538 activity IDs, so they are
matched on room plus work stage and written to COMMENTS. A rough-in is not a
close-in, so the stage word has to agree before anything is written -- token
overlap alone scores 0.50 on Wall Close-Ins against Wall Rough-Ins, which is
a different activity in a different month.
"""
import collections
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
ELEC = re.compile(
    r"conduit|raceway|\btray\b|wire|\bpull|\bterm\b|terminat|switchgear|"
    r"panel|\blight|device|feeder|busway|ground|fire alarm|\bFA\b|vesda|\bUPS\b|"
    r"transformer|\bXFMR\b|\bMSB\b|\bRMU\b|\bLBB\b|skid|energi|start.?up|"
    r"isolator|rough.?in|high steel|hanger|trim out|box pen[ae]tration|"
    r"set .*(gear|equip|crac|crah|gis)|install|inspection|testing|meg\b", re.I)
ADMIN = re.compile(
    r"\bOAA\b|\bLOI\b|\bBSU\b|\bIFC|\bCDs?\b|spec(s)?\b|pricing|submittal|"
    r"procure|deliver|approv|\bRFI\b|permit|design|funding|\bVDC\b|award|"
    r"buyout|fabricat|lead time|milestone|\bNTP\b|\bLOA\b|logistics|planning|"
    r"selection|site plan|precon|turnover|\bOFCI\b|release", re.I)
NONJER = re.compile(r"plumbing|excavation|telecom", re.I)
ROOM = re.compile(
    r"\b(ERR|HVR|ELEC|POE|ER|MV|HV|GEN|GL|DH|ADM|GAL|CUP)"
    r"[\s\.\-_]*R?[\s\.\-_]*(\d{3})\b", re.I)

p = load_xml(f"{SCR}/MDC1_logic_repaired_v20.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


def toks(n):
    return {t for t in norm(n).split() if len(t) > 2}


def room(a):
    m = ROOM.search(f"{a.activity_id} {a.name} {path(a.wbs_uid)}")
    return f"{m.group(1).upper()}{m.group(2)}" if m else ""


def isfield(a):
    if str(a.activity_id).startswith("MDC1.LAB"):
        return False
    if WBO.search(str(a.name)) or ADMIN.search(str(a.name)):
        return False
    if "Milestone" in str(a.activity_type or ""):
        return False
    if re.search(r"/ LLE|Procurement|Pre-Construction|Milestones",
                 path(a.wbs_uid)):
        return False
    return bool(ELEC.search(str(a.name)))


field = [a for a in p.activities if isfield(a)]
TOT = sum(a.planned_labor_units or 0 for a in field)
E0 = sum(a.planned_labor_units or 0 for a in field if a.status == "Completed")
print("=" * 78)
print("THE POPULATION, CORRECTED")
print("=" * 78)
allc = [a for a in p.activities if a.status == "Completed"
        and not str(a.activity_id).startswith("MDC1.LAB")
        and not WBO.search(str(a.name))]
print(f"finished non-WBO activities in the file : {len(allc)}")
print(f"   of those, electrical field work      : "
      f"{sum(1 for a in allc if isfield(a))}")
print(f"   admin / design / procurement / LLE   : "
      f"{len(allc) - sum(1 for a in allc if isfield(a))}   "
      f"(correctly carry no field hours)")
print(f"\nJER electrical field activities: {len(field)}   budget {TOT:,.0f} h")
print(f"field earned value: {E0:,.0f} / {TOT:,.0f} = {100 * E0 / TOT:.0f}% "
      f"complete by hours   (Holder reports 27%)")

# ===== 1. load the genuine gap =======================================
print("\n" + "=" * 78)
print("1. THE GENUINE LOADING GAP")
print("=" * 78)
z = [a for a in field if a.status == "Completed"
     and not (a.planned_labor_units or 0)]
lf = [a for a in field if a.status == "Completed" and (a.planned_labor_units or 0)]
AVG = sum(a.planned_labor_units or 0 for a in lf) / max(len(lf), 1)
mine = [a for a in z if not NONJER.search(str(a.name))]
theirs = [a for a in z if NONJER.search(str(a.name))]
print(f"finished field activities with no hours: {len(z)}  "
       f"(all MEP Site Underground)")
print(f"   JER's own (Power / Electrical / Ductbanks): {len(mine)}  <- loaded")
print(f"   other trades (Plumbing / Telecom / Excavation): {len(theirs)}  "
       f"<- left at zero")
print(f"field average of loaded finished work: {AVG:,.0f} h")

peers = collections.defaultdict(list)
for a in lf:
    peers[norm(a.name)].append(a.planned_labor_units or 0)
fixed = []
for a in mine:
    pr = peers.get(norm(a.name))
    h = round((sum(pr) / len(pr)) if pr else AVG, 1)
    src = (f"the {len(pr)} loaded activities of the same name" if pr
           else f"the {len(lf)}-activity average of loaded finished field work")
    a.planned_labor_units = h
    a.remaining_labor_units = 0.0
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
        f"Budget of {h:,.0f} h applied in v21: finished JER underground work "
        f"that carried no budgeted hours. Taken from {src}. Actual hours "
        f"unchanged - the spend sits on the weekly payroll record."))[:248])
    fixed.append((str(a.activity_id), str(a.name), h))
print(f"\nloaded {len(fixed)}, adding {sum(x[2] for x in fixed):,.0f} h")
for i, n, h in fixed:
    print(f"   {i:<24}{n[:44]:<46}{h:>7,.0f} h")

# ===== 2. JER's comments on Exhibit S ================================
print("\n" + "=" * 78)
print("2. JER'S 52 REVIEW COMMENTS ON HOLDER'S EXHIBIT S")
print("=" * 78)
R = json.load(open(f"{SCR}/work/mdc1_exs2.json"))
notes = [d for d in R if d.get("NOTES")]
grp = collections.defaultdict(list)
for a in p.activities:
    r = room(a)
    if r:
        grp[(r, norm(a.name))].append(a)
surplus = set()
for k, v in grp.items():
    if len(v) > 1:
        for x in sorted(v, key=lambda x: -(x.planned_labor_units or 0))[1:]:
            surplus.add(x.uid)
byroom = collections.defaultdict(list)
for a in p.activities:
    r = room(a)
    if r:
        byroom[r].append(a)
STAGE = ("rough", "close", "term", "test", "pull", "set", "trim",
         "startup", "energize", "skid")
placed, unplaced = [], []
for d in notes:
    m = ROOM.search(f"{d['Activity ID']} {d['Activity Name']}")
    r = f"{m.group(1).upper()}{m.group(2)}" if m else ""
    gt = toks(d["Activity Name"])
    best, score = None, 0.0
    for a in byroom.get(r, []):
        at = toks(a.name)
        if not at or not gt:
            continue
        if any((w in gt) != (w in at) for w in STAGE):
            continue
        j = len(at & gt) / len(at | gt)
        if j > score or (j == score and best is not None
                         and best.uid in surplus and a.uid not in surplus):
            best, score = a, j
    if best is not None and score >= 0.6:
        c = (best.udfs or {}).get("COMMENTS", "")
        best.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
            f"JER comment on Holder Exhibit S 11/03/26 "
            f"({d['Activity ID']}): {str(d['NOTES']).strip()}"))[:248])
        placed.append((str(d["Activity ID"]), str(best.activity_id),
                       str(best.name)[:34], score))
    else:
        unplaced.append((str(d["Activity ID"]),
                         str(d["Activity Name"])[:44], r))
print(f"written onto our activities: {len(placed)}")
for g, o, n, s in placed:
    print(f"   {g:<24}-> {o:<22}{n:<36}({s:.2f})")
print(f"\nno activity of ours to land on: {len(unplaced)}")
th = collections.Counter()
for d in notes:
    n = str(d["NOTES"]).lower()
    for k, rx in (("Holder permit issue", r"permit"),
                  ("DEV issue, no path forward", r"\bdev\b"),
                  ("MC Dean skids not on site", r"skid"),
                  ("Southland blocking chiller set", r"southland|chiller"),
                  ("Siemens GIS not set", r"siemen"),
                  ("VFDs not set", r"vfd"),
                  ("GC carries 0-day tie-in duration", r"0 day"),
                  ("LBB breaker testing", r"lbb|breaker test")):
        if re.search(rx, n):
            th[k] += 1
print("\nWHAT THE 52 COMMENTS SAY IS HOLDING THE WORK:")
for k, v in th.most_common():
    print(f"   {v:>4}  {k}")

A = sum(a.planned_labor_units or 0 for a in field)
E = sum(a.planned_labor_units or 0 for a in field if a.status == "Completed")
print("\n" + "=" * 78)
print(f"{'':<30}{'v20':>13}{'v21':>13}")
print(f"{'field budget':<30}{TOT:>13,.0f}{A:>13,.0f}")
print(f"{'field earned':<30}{E0:>13,.0f}{E:>13,.0f}")
print(f"{'field percent complete':<30}{100 * E0 / TOT:>12,.0f}%"
      f"{100 * E / A:>12,.0f}%")
print(f"{'whole-file budget':<30}"
      f"{338091:>13,.0f}"
      f"{sum(a.planned_labor_units or 0 for a in p.activities):>13,.0f}")
print(f"\nHolder reports 27% electrical complete at their 10/23 data date.")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v21.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
