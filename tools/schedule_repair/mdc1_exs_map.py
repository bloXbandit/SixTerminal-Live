"""What the GC's Exhibit S (11/03/2026) can and cannot tell us about MDC-1.

It is the GC's own schedule, not an update to ours: 5,000 activities against
our 2,533, and only 74 activity ids in common -- almost all of them
milestones, site utilities and structure, the contract-level items both sides
carry. Below that level the two schedules describe the same building in
different words. In the generator rooms there is not one activity name in
common: ours reads Install High Steel, OH Branch Raceway, Hang SCR, Equipment
Layout/Isolator Install; theirs reads Cable Tray Install, Cable Pulls & Terms,
Damper Intake, Damper Controls Terms.

So it cannot be applied as a status update by matching ids or names. What it
can do is two things, and this builds both.

1. THE SHARED IDS. 74 activities exist on both sides with the same id. Their
   dates can be compared directly, and the milestones among them are the ones
   that matter most -- those are the contract dates.

2. THE OCTOBER PROGRESS ON OUR SCOPE. The GC booked 166 actual dates after
   09/30, and 42 of them are JER electrical work: LBB installs, generator
   isolators, gen-to-LBB cable and tray, overhead electrical and fire alarm
   rough-in, branch conduit, fan wall pulls. That is real progress on our
   scope that our file does not carry. Each is matched to the likeliest
   activity in our schedule by room plus the kind of work, and scored, so the
   mapping can be confirmed rather than assumed. Nothing is applied here.
"""
import collections
import datetime as dt
import json
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
E = {a["id"]: a for a in json.load(open(f"{SCR}/work/mdc1_exs.json"))}
p = load_xml(f"{SCR}/MDC1_logic_repaired_v14.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
ours = {str(a.activity_id).strip(): a for a in p.activities}
CUT = dt.date(2026, 9, 30)


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def room(s):
    s = str(s).upper().replace(".", " ")
    m = re.search(r"\b(ER|ERR|MV|MVR|HV|HVR|GEN|GL|GAL|DH|DAT|ELEC|ELC)\s*R?(\d{3})\b", s)
    return f"{m.group(1)}{m.group(2)}" if m else ""


# the vocabulary that decides whether two differently-worded activities are
# the same kind of work
KIND = [
    ("LBB", r"\bLBB\b"),
    ("isolator", r"isolator"),
    ("cable tray", r"cable\s*/?\s*tray|ladder tray"),
    ("pull/term", r"\bpull|\bterm|cable pulls"),
    ("oh electrical rough-in", r"rough.?in electrical|oh rough.?in electrical|"
                               r"overhead rough in electrical|branch raceway"),
    ("fire alarm", r"fire alarm|\bFA\b"),
    ("lighting", r"light"),
    ("conduit", r"conduit"),
    ("skid", r"skid"),
    ("start-up", r"start.?up|burn.?in|energi"),
    ("SCR", r"\bSCR\b|dosing"),
]


def kinds(n):
    n = str(n)
    return {k for k, rx in KIND if re.search(rx, n, re.I)}


ELEC = re.compile(
    r"conduit|raceway|cable tray|\btray\b|wire|pull|terminat|\bterm\b|"
    r"switchgear|panel|lighting|\blight\b|device|feeder|busway|ground|"
    r"fire alarm|\bFA\b|UPS|transformer|\bXFMR\b|\bMSB\b|\bRMU\b|\bGIS\b|"
    r"\bLBB\b|\bSCR\b|skid|energi|start.?up|isolator|electrical|branch power|"
    r"\bEDP\b|\bPDP\b", re.I)

byroom = collections.defaultdict(list)
for a in p.activities:
    r = room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))
    if r:
        byroom[r].append(a)

recent = [a for a in E.values()
          if (a["finished"] and D(a["finish"]) and D(a["finish"]) > CUT)
          or (a["started"] and D(a["start"]) and D(a["start"]) > CUT)]
jer = [a for a in recent if ELEC.search(a["name"])]
print(f"GC rows with an actual after 09/30: {len(recent)}   "
      f"JER electrical among them: {len(jer)}")

rows = []
for g in sorted(jer, key=lambda x: str(x["finish"])):
    r = room(f"{g['id']} {g['name']}")
    gk = kinds(g["name"])
    best, score = None, 0
    for a in byroom.get(r, []):
        ak = kinds(a.name)
        s = len(gk & ak) * 2
        if s and a.status != "Completed":
            s += 1
        if s > score:
            best, score = a, s
    conf = ("good" if score >= 3 else "weak" if score else "none")
    rows.append((g["id"], g["name"], r, D(g["start"]), D(g["finish"]),
                 "finished" if g["finished"] else "started",
                 str(best.activity_id) if best is not None else "",
                 str(best.name) if best is not None else "",
                 best.status if best is not None else "", conf,
                 ", ".join(sorted(gk)) or ""))
c = collections.Counter(r[9] for r in rows)
print(f"mapping confidence: " + "  ".join(f"{k} {v}" for k, v in c.most_common()))

shared = []
for i in sorted(set(E) & set(ours)):
    g, o = E[i], ours[i]
    gs, gf = D(g["start"]), D(g["finish"])
    os_, of = D(o.planned_start), D(o.planned_finish)
    agree = ""
    if g["finished"] and o.status == "Completed":
        agree = "both complete"
    elif g["finished"] and o.status != "Completed":
        agree = "GC COMPLETE, OURS NOT"
    elif not g["started"] and o.status == "Completed":
        agree = "OURS COMPLETE, GC NOT STARTED"
    # only meaningful where the work has not started on our side. On a
    # completed activity our planned start is whatever the last F9 left,
    # and the GC shows the actual, so the difference is an artifact -- it
    # made Mobilize look 352 days apart when both sides call it complete.
    d = (os_ - gs).days if (os_ and gs and o.status == "Not Started"
                            and not g["started"]) else None
    shared.append((i, str(o.name), o.status,
                   "finished" if g["finished"] else
                   "started" if g["started"] else "not started",
                   os_, gs, of, gf, d, agree))
print(f"shared ids: {len(shared)}   disagreements: "
      f"{sum(1 for r in shared if r[9].isupper() or 'OURS' in r[9] or 'GC' in r[9])}")

HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
wb = Workbook()
ws = wb.create_sheet("GC Oct progress - our scope")
C1 = ("GC activity ID", "GC activity", "Room", "GC start", "GC finish",
      "GC status", "Our activity ID (best guess)", "Our activity",
      "Our status", "Match confidence", "Work type", "Correct? (Y/N)",
      "Right activity ID if not", "Notes")
ws.append(C1)
for i, cc in enumerate(ws[1], 1):
    cc.font = HEAD
    cc.fill = REDH if C1[i - 1] in ("Correct? (Y/N)", "Right activity ID if not",
                                    "Notes") else FILL
    cc.alignment = Alignment(wrap_text=True, vertical="top")
for r in rows:
    ws.append(r + ("", "", ""))
    for j in (12, 13, 14):
        ws.cell(row=ws.max_row, column=j).fill = REDC
for i, w in enumerate((26, 46, 9, 12, 12, 11, 26, 40, 13, 13, 24, 12, 24, 30), 1):
    ws.column_dimensions[get_column_letter(i)].width = w
ws.freeze_panes = "C2"

ws = wb.create_sheet("Shared IDs - dates")
C2 = ("Activity ID", "Activity", "Our status", "GC status", "Our start",
      "GC start", "Our finish", "GC finish",
      "Our start minus GC (days, not-started only)", "Flag")
ws.append(C2)
for cc in ws[1]:
    cc.font, cc.fill = HEAD, FILL
    cc.alignment = Alignment(wrap_text=True, vertical="top")
for r in shared:
    ws.append(r)
for i, w in enumerate((24, 46, 13, 13, 12, 12, 12, 12, 14, 30), 1):
    ws.column_dimensions[get_column_letter(i)].width = w
ws.freeze_panes = "C2"
for sheet in wb.worksheets:
    for rr in sheet.iter_rows(min_row=2):
        for cc in rr:
            if isinstance(cc.value, dt.date):
                cc.number_format = "mm/dd/yyyy"
del wb["Sheet"]
out = f"{SCR}/MDC1_vs_GC_ExhibitS.xlsx"
wb.save(out)
print(f"\nwrote {out}")
print(f"\n--- the 42, with the guess ---")
for r in rows[:24]:
    print(f"   {r[0]:<24}{r[1][:40]:<42}{str(r[4])}  {r[9]:<6}-> "
          f"{r[6]:<22}{r[7][:30]}")
