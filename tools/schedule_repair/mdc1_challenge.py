"""Which of the 214 backward-forced completions does the evidence argue against?

They were completed because something downstream had started, not because
anyone reported them. That reasoning is sound, but it inherits whatever was
wrong upstream of it -- and 111 of the bookings that triggered it were matched
on kind of work in a room, not on the activity itself.

Five tests, each one a reason to doubt a particular completion:

  A  THE GC SAYS IT IS NOT DONE. Their export carries the same kind of work
     in the same room and has it unfinished at 10/23.
  B  THE FIELD IS STILL ON IT. A daily report from AFTER the date we booked
     it complete names that work in that room.
  C  IT IS A VERIFICATION STAGE. Testing, inspection, start-up, energization,
     commissioning. Nobody infers these from a successor -- they produce a
     document when they happen, and there is no document.
  D  IT IS TOO BIG TO HAVE SLIPPED BY. A long activity completed with nobody
     reporting it, where a week of work might pass unnoticed but a quarter
     should not.
  E  ITS OWN FORECAST PUT IT IN THE FUTURE. Early start after the data date,
     so the schedule did not expect it to have begun at all.
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
R = json.load(open(f"{SCR}/work/mdc1_daily.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v17.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}


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


DD = D(p.data_date)
GCDD = dt.date(2026, 10, 23)


def room(s):
    s = str(s).upper().replace(".", " ")
    m = re.search(r"\b(ER|ERR|MV|HV|GEN|GL|GAL|DH|DAT|ELEC|POE)\s*R?\s?(\d{3})\b", s)
    return f"{m.group(1)}{m.group(2)}" if m else ""


KIND = [("LBB", r"\bLBB\b"), ("isolator", r"isolator"),
        ("tray", r"cable\s*/?\s*tray|ladder tray"),
        ("pull/term", r"\bpull|\bterm|cable pulls"),
        ("oh-elec", r"rough.?in electrical|rough.?in/ ?fa|branch raceway|"
                    r"overhead rough in"),
        ("fire alarm", r"fire alarm|\bFA\b|vesda"), ("lighting", r"\blight"),
        ("conduit", r"conduit|\bEMT\b"), ("skid", r"skid"),
        ("start-up", r"start.?up|burn.?in|energi"),
        ("device", r"device|trim out"), ("hanger", r"hanger|high steel")]


def kinds(n):
    return {k for k, rx in KIND if re.search(rx, str(n), re.I)}


VERIFY = re.compile(r"\binspect|\btest(ing)?\b|\bQA\b|\bQC\b|\bmeg\b|"
                    r"start.?up|commission|energi|punch|\bsign.?off\b|"
                    r"turnover|\bwalk\b", re.I)

# the GC's open work, by room and kind
gcopen = collections.defaultdict(set)
gcdone = collections.defaultdict(set)
for a in E.values():
    r = room(a["id"] + " " + a["name"])
    if not r:
        continue
    (gcdone if a["finished"] else gcopen)[r] |= kinds(a["name"])
# what the field was still working on AFTER a given date
later = []
for r in R:
    d = D(r["date"])
    if not d:
        continue
    txt = f"{r['tasks']} {r['comments']}"
    rooms = {f"{m.group(1).upper()}{m.group(2)}" for m in
             re.finditer(r"\b(ER|ERR|MV|HV|GEN|GL|DH|POE|ELEC)\s*R?\s?(\d{3})\b",
                         txt, re.I)}
    later.append((d, rooms, kinds(txt)))

forced = [a for a in p.activities
          if a.status == "Completed"
          and "by inference" in str((a.udfs or {}).get("COMMENTS", ""))]
print(f"backward-forced completions in the file: {len(forced)}")

doubts = collections.defaultdict(list)
for a in forced:
    rm = room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))
    kk = kinds(a.name)
    af = D(a.actual_finish)
    dur = round((a.planned_duration or 8) / 8)
    why = []
    if rm and kk and (gcopen.get(rm, set()) & kk) and not (gcdone.get(rm, set()) & kk):
        why.append(("A", f"GC has {', '.join(sorted(gcopen[rm] & kk))} in {rm} "
                         f"still open at 10/23"))
    if rm and kk and af:
        on = [d for d, rooms, ks in later
              if d > af and rm in rooms and (ks & kk)]
        if on:
            why.append(("B", f"field still reporting this in {rm} on "
                             f"{max(on)}, after we booked it {af}"))
    if VERIFY.search(str(a.name)):
        why.append(("C", "a verification stage - produces a document, and "
                         "there is none"))
    if dur >= 20:
        why.append(("D", f"{dur} working days long - too big to go unreported"))
    es = D(a.early_start)
    if es and es > DD:
        why.append(("E", f"its own forecast had it starting {es}, after the "
                         f"data date"))
    if why:
        doubts[a.uid] = (a, rm, why)

print(f"of those, flagged by at least one test: {len(doubts)}")
cnt = collections.Counter(t for _, _, ws in doubts.values() for t, _ in ws)
for t in "ABCDE":
    lbl = {"A": "GC says still open", "B": "field still working it",
           "C": "verification stage", "D": "too long to miss",
           "E": "forecast put it in the future"}[t]
    print(f"   {t}  {cnt.get(t, 0):>4}  {lbl}")

strong = {u: v for u, v in doubts.items() if len(v[2]) >= 2}
print(f"\nflagged by two or more tests -- the ones I would put back: {len(strong)}")
for u, (a, rm, ws) in sorted(strong.items(),
                             key=lambda x: -len(x[1][2]))[:20]:
    print(f"\n   {str(a.activity_id):<26}{str(a.name)[:42]:<44}{rm}")
    print(f"      booked complete {D(a.actual_finish)}, "
          f"{round((a.planned_duration or 8) / 8)}d long")
    for t, m in ws:
        print(f"      [{t}] {m}")

HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
AMB = PatternFill("solid", fgColor="FFF2CC")
RED = PatternFill("solid", fgColor="FFC7CE")
wb = Workbook()
ws = wb.create_sheet("Doubtful completions")
C = ("Confidence it is WRONG", "Activity ID", "Activity", "Room",
     "We booked complete", "Duration (d)", "Why it is doubtful", "WBS",
     "Keep complete? (Y/N)", "Notes")
ws.append(C)
for i, c in enumerate(ws[1], 1):
    c.font, c.fill = HEAD, FILL
    c.alignment = Alignment(wrap_text=True, vertical="top")
for u, (a, rm, wss) in sorted(doubts.items(),
                              key=lambda x: (-len(x[1][2]),
                                             str(x[1][0].activity_id))):
    lvl = "HIGH - put it back" if len(wss) >= 2 else "worth a look"
    ws.append((lvl, str(a.activity_id), str(a.name), rm, D(a.actual_finish),
               round((a.planned_duration or 8) / 8),
               " | ".join(f"[{t}] {m}" for t, m in wss), path(a.wbs_uid),
               "", ""))
    f = RED if len(wss) >= 2 else AMB
    for j in (1, 7):
        ws.cell(row=ws.max_row, column=j).fill = f
    for j in (9, 10):
        ws.cell(row=ws.max_row, column=j).fill = RED
for i, w in enumerate((22, 26, 44, 8, 15, 11, 76, 44, 14, 30), 1):
    ws.column_dimensions[get_column_letter(i)].width = w
for rr in ws.iter_rows(min_row=2):
    for c in rr:
        if isinstance(c.value, dt.date):
            c.number_format = "mm/dd/yyyy"
ws.freeze_panes = "C2"
del wb["Sheet"]
out = f"{SCR}/MDC1_Doubtful_Completions.xlsx"
wb.save(out)
print(f"\nwrote {out}")
json.dump([str(byu[u].activity_id) for u in strong],
          open(f"{SCR}/work/mdc1_putback.json", "w"), indent=1)
