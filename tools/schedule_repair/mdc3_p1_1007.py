"""Page 1 of the 10/07 proposed Exhibit S, read by eye, against the 02/04 contract.

The 10/07 came through "Microsoft: Print To PDF", which traced every character
as bezier curves -- 0 text operators on the page, so nothing can be parsed from
it. Page 1 carries the whole milestone block, so it is transcribed here by hand
from a 14x render and diffed against the parsed 02/04 contract.

Transcription only. Every date below is read off the page; nothing is derived.
The three Substantial Completion rows were re-read at 14x on a separate crop
because the entire acceleration question turns on them.
"""
import datetime as dt
import json

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
C = {r["id"]: r for r in json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))}

# id, name, start, finish  -- "A" suffix = actual, "*" = constrained
P1 = [
    ("MDC3.MIL.1000", "Design Kickoff", "01-Dec-25 A", ""),
    ("MDC3.MIL.1020", "Notice to Proceed for Procurement", "01-Dec-25 A", ""),
    ("MDC3.MIL.1170", "Preconstruction Start", "08-Dec-25 A", ""),
    ("MDC3.MIL.1340", "Mobilize", "02-Mar-26 A", ""),
    ("MDC3.MIL.1210", "Contractual Start/Start of Construction", "02-Mar-26 A", ""),
    ("MDC3.MIL.1220", "NTP With Construction", "02-Mar-26 A", ""),
    ("MDC3.MIL.1350", "Initial Trade Release", "03-Mar-26 A", ""),
    ("MDC3.MIL.1330", "Initial Funding Released", "", "03-Mar-26 A"),
    ("MDC3.MIL.1050", "GMP/Final Pricing Executed", "", "03-Mar-26 A"),
    ("MDC3.MIL.1040", "Owner Contract Executed", "", "04-Mar-26 A"),
    ("MDC3.MIL.1030", "Precast Design Complete", "", "24-Mar-26 A"),
    ("MDC3.MIL.1200", "All Major/Long Lead Equipment Released", "", "27-Mar-26 A"),
    ("MDC3.MIL.1010", "Site Development Permit Received", "", "26-Jun-26 A"),
    ("MDC3.MIL.1190", "Cold Dark Shell (F/F Precast) Received", "", "02-Jul-26 A"),
    ("MDC3.MIL.1370", "Start Foundations", "08-Jul-26 A", ""),
    ("MDC3.MIL.1360", "Building Pad Ready", "", "01-Aug-26 A"),
    ("MDC3.MIL.1365", "Foundation Condition", "", "14-Aug-26 A"),
    ("MDC3.MIL.1380", "Start Precast", "28-Sep-26", ""),
    ("MDC3.MIL.1060", "Preconstruction Complete", "", "26-Oct-26"),
    ("MDC3.MIL.1180", "100% CDs/IFCs Received", "", "11-Dec-26"),
    ("MDC3.MIL.1390", "Finish Precast", "", "16-Mar-27"),
    ("MDC3.MIL.1240", "Set First Piece of Major Equipment", "17-Mar-27", ""),
    ("MDC3.MIL.1405", "Cold Shell Condition", "", "23-Mar-27"),
    ("MDC3.MIL.1395", "Top-Out Condition", "", "06-Apr-27"),
    ("MDC3.MIL.1410", "Equipment Energization Start", "14-Jul-27", ""),
    ("MDC3.MIL.1250", "Permanent Power Available by Utility", "26-Jul-27", ""),
    ("MDC3.MIL.1260", "Permanent Power Available to Building", "26-Jul-27", ""),
    ("MDC3.MIL.1420", "Beneficial Use - Wet Utilities", "", "23-Aug-27"),
    ("MDC3.MIL.1270", "Building Systems Ready for Energization", "", "10-Sep-27"),
    ("MDC3.MIL.1280", "Start of Test and Balance", "22-Oct-27", ""),
    ("MDC3.MIL.1430", "Punchlist Completed", "", "25-Feb-28"),
    ("MDC3.MIL.1140", "Complete Construction", "", "10-Mar-28"),
    ("MDC3.MIL.1070", "Final Certificate of Occupancy", "", "17-Mar-28"),
    ("MDC3.MIL.1320", "Owner Acceptance", "", "17-Mar-28"),
    ("MDC3.MIL.1150", "Final Completion", "", "17-Mar-28"),
    ("MDC3.MIL.PH1.1000", "Level 3 Commissioning Start (PH1)", "12-Aug-27", ""),
    ("MDC3.MIL.PH1.1010", "Level 3 Commissioning Finish (PH1)", "", "14-Oct-27"),
    ("MDC3.MIL.PH1.1015", "Warm Shell Condition (PH1)", "", "14-Oct-27"),
    ("MDC3.MIL.PH1.1020", "Level 4 Commissioning Start (PH1)", "15-Oct-27", ""),
    ("MDC3.MIL.PH1.1030", "Level 4 Commissioning Finish (PH1)", "", "06-Dec-27"),
    ("MDC3.MIL.PH1.1080", "Level 5 Commissioning Start (PH1)", "07-Dec-27", ""),
    ("MDC3.MIL.PH1.1050", "Level 5 Commissioning Finish (PH1)", "", "20-Dec-27"),
    ("MDC3.MIL.PH1.1040", "Temporary Certificate of Occupancy (PH1)", "", "06-Jan-28"),
    ("MDC3.MIL.PH1.1070", "Substantial Completion (PH1)", "", "06-Jan-28*"),
    ("MDC3.MIL.PH2.9000", "Level 3 Commissioning Start (PH2)", "01-Oct-27", ""),
    ("MDC3.MIL.PH2.9010", "Level 3 Commissioning Finish (PH2)", "", "06-Dec-27"),
    ("MDC3.MIL.PH2.9015", "Warm Shell Condition (PH2)", "", "06-Dec-27"),
    ("MDC3.MIL.PH2.9020", "Level 4 Commissioning Start (PH2)", "07-Dec-27", ""),
    ("MDC3.MIL.PH2.9030", "Level 4 Commissioning Finish (PH2)", "", "28-Jan-28"),
    ("MDC3.MIL.PH2.9080", "Level 5 Commissioning Start (PH2)", "31-Jan-28", ""),
    ("MDC3.MIL.PH2.9060", "Level 5 Commissioning Finish (PH2)", "", "11-Feb-28"),
    ("MDC3.MIL.PH2.9050", "Temporary Certificate of Occupancy (PH2)", "", "25-Feb-28"),
    ("MDC3.MIL.PH2.9070", "Substantial Completion (PH2)", "", "25-Feb-28*"),
    ("MDC3.MIL.PH3.9000", "Level 3 Commissioning Start (PH3)", "05-Nov-27", ""),
    ("MDC3.MIL.PH3.9020", "Level 3 Commissioning Finish (PH3)", "", "13-Jan-28"),
    ("MDC3.MIL.PH3.9025", "Warm Shell Condition (PH3)", "", "13-Jan-28"),
    ("MDC3.MIL.PH3.9010", "Level 4 Commissioning Start (PH3)", "31-Jan-28", ""),
    ("MDC3.MIL.PH3.9030", "Level 4 Commissioning Finish (PH3)", "", "29-Feb-28"),
    ("MDC3.MIL.PH3.9080", "Level 5 Commissioning Start (PH3)", "01-Mar-28", ""),
    ("MDC3.MIL.PH3.9060", "Level 5 Commissioning Finish (PH3)", "", "10-Mar-28"),
    ("MDC3.MIL.PH3.9050", "Certificate of Occupancy (PH3)", "", "17-Mar-28"),
    ("MDC3.MIL.PH3.9070", "Substantial Completion (PH3)", "", "17-Mar-28*"),
]
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

# Holder RENUMBERED the contract milestones between the two Exhibit S issues.
# Matching on activity id silently drops all three Substantial Completion
# dates -- the only three constrained dates in the 02/04 contract and the
# whole point of the comparison. So the milestone block is matched by NAME.
#   Substantial Completion (PH1)   PH1.1060 -> PH1.1070
#   Substantial Completion (PH2)   PH2.9040 -> PH2.9070
#   Substantial Completion (PH3)   PH3.9040 -> PH3.9070


def pd(s):
    s = s.strip()
    if not s:
        return None, ""
    flag = ""
    if s.endswith(" A"):
        flag, s = "A", s[:-2]
    elif s.endswith("*"):
        flag, s = "*", s[:-1]
    d, m, y = s.split("-")
    return dt.date(2000 + int(y), MON[m], int(d)), flag


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


print("=" * 104)
print("MDC-3 MILESTONES :  02/04/2026 CURRENT CONTRACT  ->  10/07/2026 PROPOSED")
print("=" * 104)
print("02/04 Exhibit S : data date 05-Dec-25, printed 04-Feb-26, "
      "project 01-Dec-25 .. 31-Dec-27  (524d)")
print("10/07 Exhibit S : data date 25-Sep-26, printed 07-Oct-26, "
      "project 11-Apr-25 A .. 17-Mar-28  (739d)")
print()
def norm(n):
    return " ".join(str(n).split()).lower().replace("(initial energization)", "")\
        .replace(" - ", " ").replace("-", " ").strip()


BYNAME = {}
for r in C.values():
    BYNAME.setdefault(norm(r["name"]), r)
ALIAS = {  # the 10/07 wording on the left, the 02/04 wording on the right
    "permanent power available to building":
        "permanent power available to building",
}

print(f"{'10/07 id':<21}{'milestone':<42}{'02/04':<13}{'10/07':<13}"
      f"{'shift':>8}")
print("-" * 104)
rows, missing, renum = [], [], []
for aid, nm, s, f in P1:
    nd, nflag = pd(f or s)
    c = C.get(aid)
    by_id = c is not None
    if c is None:                      # fall back to the milestone's NAME
        c = BYNAME.get(norm(nm)) or BYNAME.get(ALIAS.get(norm(nm), ""))
        if c is not None:
            renum.append((aid, c["id"], nm))
    if c is None:
        missing.append((aid, nm, nd, nflag))
        continue
    od = D(c["finish"]) or D(c["start"])
    oflag = (c["finish_flag"] or c["start_flag"] or "")
    if od is None or nd is None:
        continue
    delta = (nd - od).days
    rows.append((aid, nm, od, oflag, nd, nflag, delta))
    mark = "*" if "*" in (oflag + nflag) else (
        "A" if "A" in (oflag + nflag) else " ")
    print(f"{aid:<21}{nm[:40]:<42}{od.strftime('%d-%b-%y'):<13}"
          f"{nd.strftime('%d-%b-%y'):<13}{delta:>+7}d {mark}"
          f"{'' if by_id else '  <- id renumbered, matched by name'}")

print()
print(f"milestones on page 1 that exist in the 02/04 contract : "
      f"{len(rows)} of {len(P1)}")
if renum:
    print(f"\nRENUMBERED between the two issues ({len(renum)}) -- an "
          f"id-to-id comparison would drop these silently:")
    for na, oa, nm in renum:
        print(f"   {nm[:44]:<46}{oa:<22}->  {na}")
if missing:
    print(f"\nnew in the 10/07, no 02/04 counterpart ({len(missing)}):")
    for aid, nm, nd, fl in missing:
        print(f"   {aid:<21}{nm[:46]:<48}"
              f"{nd.strftime('%d-%b-%y') if nd else '-'}{fl}")

print("\n" + "=" * 104)
print("THE THREE CONTRACT DATES  (the only constrained dates in either issue)")
print("=" * 104)
for lbl in ["Substantial Completion (PH1)", "Substantial Completion (PH2)",
            "Substantial Completion (PH3)"]:
    r = [x for x in rows if x[1] == lbl]
    if not r:
        print(f"   {lbl}: NOT MATCHED")
        continue
    _, _, od, _, nd, _, delta = r[0]
    print(f"   {lbl:<32}{od.strftime('%d-%b-%y')}*  ->  "
          f"{nd.strftime('%d-%b-%y')}*   {delta:+}d  "
          f"({'LATER' if delta > 0 else 'EARLIER'})")
print("\n   -> the end dates moved OUT by 77 to 95 days. Taken alone that")
print("      reads as relief, not acceleration. The front end is what")
print("      decides whether it actually is.")

print("\n" + "=" * 104)
print("WHAT MOVED, BY BLOCK")
print("=" * 104)


def blk(aid):
    if ".PH1." in aid:
        return "Phase 1 milestones"
    if ".PH2." in aid:
        return "Phase 2 milestones"
    if ".PH3." in aid:
        return "Phase 3 milestones"
    return "General milestones"


for b in ["General milestones", "Phase 1 milestones",
          "Phase 2 milestones", "Phase 3 milestones"]:
    g = [x for x in rows if blk(x[0]) == b]
    if not g:
        continue
    d = [x[6] for x in g]
    print(f"{b:<22}n={len(g):<4}min {min(d):+5}d   max {max(d):+5}d   "
          f"mean {sum(d) / len(d):+6.0f}d")

print("\n" + "=" * 104)
print("DURATION BETWEEN MILESTONES -- is the span itself compressed?")
print("=" * 104)
print("Both ends matched by NAME, so the renumbering cannot skew it.\n")
NEW = {nm: pd(f or s)[0] for aid, nm, s, f in P1}


def old(nm):
    r = BYNAME.get(norm(nm))
    return (D(r["finish"]) or D(r["start"])) if r else None


SPANS = [
    ("--- the handoff JER lives on ---", None, None),
    ("Finish Precast -> Set First Piece of Major Equipment",
     "Finish Precast", "Set First Piece of Major Equipment"),
    ("Start Precast -> Finish Precast",
     "Start Precast", "Finish Precast"),
    ("100% CDs/IFCs Received -> Set First Piece of Major Equipment",
     "100% CDs/IFCs Received", "Set First Piece of Major Equipment"),
    ("Set First Piece of Major Equipment -> Equipment Energization Start",
     "Set First Piece of Major Equipment", "Equipment Energization Start"),
    ("--- commissioning, where JER's hours land ---", None, None),
    ("Level 3 Cx PH1  (start -> finish)",
     "Level 3 Commissioning Start (PH1)", "Level 3 Commissioning Finish (PH1)"),
    ("Level 3 Cx PH2  (start -> finish)",
     "Level 3 Commissioning Start (PH2)", "Level 3 Commissioning Finish (PH2)"),
    ("Level 3 Cx PH3  (start -> finish)",
     "Level 3 Commissioning Start (PH3)", "Level 3 Commissioning Finish (PH3)"),
    ("Level 4 Cx PH1  (start -> finish)",
     "Level 4 Commissioning Start (PH1)", "Level 4 Commissioning Finish (PH1)"),
    ("Level 4 Cx PH2  (start -> finish)",
     "Level 4 Commissioning Start (PH2)", "Level 4 Commissioning Finish (PH2)"),
    ("Level 4 Cx PH3  (start -> finish)",
     "Level 4 Commissioning Start (PH3)", "Level 4 Commissioning Finish (PH3)"),
    ("--- energization to turnover ---", None, None),
    ("Equipment Energization Start -> Level 3 Cx Start (PH1)",
     "Equipment Energization Start", "Level 3 Commissioning Start (PH1)"),
    ("Building Systems Ready -> Substantial Completion (PH1)",
     "Building Systems Ready for Energization",
     "Substantial Completion (PH1)"),
    ("Level 3 Cx Start (PH1) -> Substantial Completion (PH1)",
     "Level 3 Commissioning Start (PH1)", "Substantial Completion (PH1)"),
    ("Level 3 Cx Start (PH2) -> Substantial Completion (PH2)",
     "Level 3 Commissioning Start (PH2)", "Substantial Completion (PH2)"),
    ("Level 3 Cx Start (PH3) -> Substantial Completion (PH3)",
     "Level 3 Commissioning Start (PH3)", "Substantial Completion (PH3)"),
    ("--- how tightly the three phases sit on each other ---", None, None),
    ("Level 3 Cx Start   PH1 -> PH3  (release stagger)",
     "Level 3 Commissioning Start (PH1)", "Level 3 Commissioning Start (PH3)"),
    ("Substantial Completion  PH1 -> PH3  (turnover stagger)",
     "Substantial Completion (PH1)", "Substantial Completion (PH3)"),
]
print(f"{'span':<58}{'02/04':>8}{'10/07':>8}{'change':>10}")
print("-" * 104)
comp = []
for lbl, a, b in SPANS:
    if a is None:
        print(f"\n{lbl}")
        continue
    oa, ob, na, nb = old(a), old(b), NEW.get(a), NEW.get(b)
    if not all((oa, ob, na, nb)):
        print(f"{lbl:<58}{'-':>8}{'-':>8}{'  not in both':>10}")
        continue
    o, n = (ob - oa).days, (nb - na).days
    tag = ("COMPRESSED" if n < o else "extended" if n > o else "same")
    if n < o:
        comp.append((lbl, o, n, n - o))
    print(f"{lbl:<58}{o:>6}d{n:>7}d{n - o:>+8}d  {tag}")

print("\n" + "=" * 104)
print("COMPRESSION, WORST FIRST")
print("=" * 104)
for lbl, o, n, d in sorted(comp, key=lambda x: x[3]):
    print(f"   {d:>+5}d   {lbl:<58}{o}d -> {n}d  "
          f"({100 * n / o:.0f}% of the original window)")

