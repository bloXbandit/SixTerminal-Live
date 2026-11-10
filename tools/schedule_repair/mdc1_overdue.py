"""The 1,189 not-started activities whose early start has already passed:
which of them did we miss, and which are just stale arithmetic?

Four tests, strongest first. An activity only needs to fail the strong ones to
fall through to the weak.

  1. SOMETHING DOWNSTREAM HAS STARTED. If any successor of this activity is
     complete or in progress, then this one happened, whatever the file says.
     That is not inference from dates, it is the logic the user built. These
     are missed status, full stop.

  2. THE GC REPORTS IT DONE. Their Exhibit S marks the same work finished,
     matched on room plus kind of work.

  3. THE FIELD REPORTS IT. A JER daily report names that work in that room
     inside the window.

  4. NOTHING, BUT IT COULD HAVE RUN. Every predecessor is complete, so the
     work was available. No evidence either way: a question for the foreman.

Everything else is BLOCKED -- it still has an incomplete predecessor, so it
cannot have happened, and its early date is simply older than the data date.
Those need no statusing at all; F9 moves them.
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
p = load_xml(f"{SCR}/MDC1_logic_repaired_v16.xml")
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
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)


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


gcdone = collections.defaultdict(set)
for a in E.values():
    if a["finished"]:
        r = room(a["id"] + " " + a["name"])
        if r:
            gcdone[r] |= kinds(a["name"])
fieldwork = collections.defaultdict(set)
for r in R:
    d = D(r["date"])
    if not d or d > DD:
        continue
    txt = f"{r['tasks']} {r['comments']}"
    for m in re.finditer(r"\b(ER|ERR|MV|HV|GEN|GL|DH|POE|ELEC)\s*R?\s?(\d{3})\b",
                         txt, re.I):
        fieldwork[f"{m.group(1).upper()}{m.group(2)}"] |= kinds(txt)

overdue = [a for a in p.activities
           if a.status == "Not Started" and not WBO.search(str(a.name))
           and D(a.early_start) and D(a.early_start) < DD]
print(f"not-started JER activities with an early start before {DD}: {len(overdue)}")

buckets = collections.defaultdict(list)
for a in overdue:
    rm = room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))
    kk = kinds(a.name)
    ds = [byu[v] for v in succ[a.uid] if v in byu]
    moved = [x for x in ds if x.status in ("Completed", "In Progress")]
    blocked = [byu[q] for q in pred[a.uid]
               if q in byu and byu[q].status != "Completed"]
    if moved:
        buckets["1. successor already started - this MUST have happened"].append(
            (a, rm, f"{moved[0].activity_id} {str(moved[0].name)[:30]} "
                    f"({moved[0].status})"))
    elif rm and kk and (gcdone.get(rm, set()) & kk):
        buckets["2. the GC reports this work done in this room"].append(
            (a, rm, "GC: " + ", ".join(sorted(gcdone[rm] & kk))))
    elif rm and kk and (fieldwork.get(rm, set()) & kk):
        buckets["3. a JER daily report names this work in this room"].append(
            (a, rm, "field: " + ", ".join(sorted(fieldwork[rm] & kk))))
    elif not blocked:
        buckets["4. nothing in front of it - could have run, no evidence"].append(
            (a, rm, "ready since " + str(D(a.early_start))))
    else:
        buckets["5. still blocked - cannot have happened, date is just old"].append(
            (a, rm, f"waiting on {blocked[0].activity_id} "
                    f"{str(blocked[0].name)[:28]}"))

print()
for k in sorted(buckets):
    v = buckets[k]
    print(f"{len(v):>6}   {k}")
missed = sum(len(buckets[k]) for k in buckets if k[0] in "123")
print(f"\n{'-' * 70}")
print(f"{missed:>6}   LIKELY MISSED STATUS (tests 1-3)")
print(f"{len(buckets['4. nothing in front of it - could have run, no evidence']):>6}"
      f"   ASK THE FOREMAN")
print(f"{len(buckets['5. still blocked - cannot have happened, date is just old']):>6}"
      f"   JUST STALE DATES - F9 moves them, no statusing needed")

for k in sorted(buckets):
    v = buckets[k]
    if not v or k.startswith("5"):
        continue
    print(f"\n=== {k} ({len(v)}) ===")
    for a, rm, why in sorted(v, key=lambda x: str(D(x[0].early_start)))[:14]:
        print(f"   {str(a.activity_id):<26}{str(a.name)[:36]:<38}"
              f"{str(D(a.early_start))}  {rm:<8}{why[:52]}")
    if len(v) > 14:
        print(f"   ... and {len(v) - 14} more")

HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
wb = Workbook()
C = ("Verdict", "Activity ID", "Activity", "Room", "Early start",
     "Early finish", "Days past", "Evidence", "WBS",
     "Done? (Y/N)", "Actual start", "Actual finish", "Notes")
for key, title in (("1", "1 - must have happened"), ("2", "2 - GC says done"),
                   ("3", "3 - field reports it"), ("4", "4 - ask the foreman"),
                   ("5", "5 - just stale dates")):
    rows = [b for k, b in buckets.items() if k.startswith(key)]
    rows = rows[0] if rows else []
    ws = wb.create_sheet(title)
    ws.append(C)
    for i, c in enumerate(ws[1], 1):
        c.font = HEAD
        c.fill = REDH if C[i - 1] in ("Done? (Y/N)", "Actual start",
                                      "Actual finish", "Notes") else FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for a, rm, why in sorted(rows, key=lambda x: str(D(x[0].early_start))):
        es, ef = D(a.early_start), D(a.early_finish)
        ws.append((title, str(a.activity_id), str(a.name), rm, es, ef,
                   (DD - es).days if es else None, why, path(a.wbs_uid),
                   "", "", "", ""))
        for j in (10, 11, 12, 13):
            ws.cell(row=ws.max_row, column=j).fill = REDC
    for i, w in enumerate((22, 26, 46, 9, 12, 12, 10, 52, 46, 11, 13, 13, 30), 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for rr in ws.iter_rows(min_row=2):
        for c in rr:
            if isinstance(c.value, dt.date):
                c.number_format = "mm/dd/yyyy"
    ws.freeze_panes = "C2"
del wb["Sheet"]
out = f"{SCR}/MDC1_Overdue_Triage.xlsx"
wb.save(out)
print(f"\nwrote {out}")
