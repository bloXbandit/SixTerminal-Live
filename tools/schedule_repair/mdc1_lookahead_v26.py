"""MDC-1 catch-up and two-week lookahead, rebuilt on v23.

The sheet that went out was built on v18. Five versions have changed what it
would say: v20 cleared 374 completed activities that were still carrying
remaining hours and put the real October labour in, v21 loaded 16 finished
underground activities, v22 merged four duplicated rooms and took 31
completions out with the surplus copies, and v23 added six rooms with
Holder's own actual dates on them and closed 21 predecessors out behind
them. So the overdue-and-ready list is not the same list.

Dates are the EARLY dates, as before -- P6 freezes the planned dates and does
not move them on a schedule run, so reading those made an earlier version of
this sheet 7 rows when the answer was 95.

Column J is now a Yes/No dropdown from row 3 down, so the foreman picks
rather than types. The three date and note columns beside it stay free text.
"""
import collections
import datetime as dt
import itertools
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
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
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


DD = D(p.data_date)
HZ = DD + dt.timedelta(days=14)
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)


def ready(a):
    return not [1 for q in pred[a.uid]
                if q in byu and byu[q].status != "Completed"]


def where(a):
    s = f"{a.activity_id} {a.name} {path(a.wbs_uid)}"
    m = re.search(r"\b((?:ERR|HVR|ER|MV|HV|GEN|GL|GAL|DH|ELEC|POE)"
                  r"[\s\.\-_]*R?[\s\.\-_]*\d{3})\b", s, re.I)
    if m:
        return re.sub(r"[\s\.\-_]+", " ", m.group(1)).upper()
    return path(a.wbs_uid).split(" / ")[-1][:28]


def scope(a):
    pth = path(a.wbs_uid)
    aid = str(a.activity_id or "")
    if "Milestones" in pth or aid.startswith("MDC1.MIL"):
        return "Milestones"
    for key, lab in (("Generator", "Generator rooms"),
                     ("Galler", "Galleries"),
                     ("Electrical Room", "Electrical rooms"),
                     ("Data Hall", "Data halls"), ("CUP", "CUP"),
                     ("Admin", "Admin space"), ("Roof", "Roof"),
                     ("Chiller", "Chiller lineups"),
                     ("Lineup", "Chiller lineups"),
                     ("MV Room", "MV rooms"), ("HV Room", "HV rooms"),
                     ("Procurement", "Procurement"),
                     ("Underground", "Underground"), ("Site", "Site"),
                     ("Structure", "Structure")):
        if key.lower() in pth.lower():
            return lab
    parts = pth.split(" / ")
    return parts[1] if len(parts) > 1 else (pth or "Other")


def releases(a):
    return len([v for v in succ[a.uid] if v in byu
                and byu[v].status == "Not Started"])


catch, running, look = [], [], []
for a in p.activities:
    if WBO.search(str(a.name)) or a.status == "Completed":
        continue
    if str(a.activity_id).startswith("MDC1.LAB"):
        continue
    es, ef = D(a.early_start), D(a.early_finish)
    if a.status == "In Progress":
        if ef and ef <= HZ:
            running.append(a)
        continue
    if es and es < DD and ready(a):
        catch.append(a)
    elif es and DD <= es <= HZ:
        look.append(a)

print(f"MDC-1 v26   data date {DD:%m/%d/%Y}   lookahead to {HZ:%m/%d/%Y}")
print(f"catch-up (overdue AND ready)   {len(catch):>5}   (v18 sheet had 87)")
print(f"running now, due in the window {len(running):>5}   (had 50)")
print(f"starting in the next two weeks {len(look):>5}   (had 93)")
blocked = sum(1 for a in p.activities
              if a.status == "Not Started" and not WBO.search(str(a.name))
              and D(a.early_start) and D(a.early_start) < DD and not ready(a))
print(f"\nleft out: {blocked} overdue activities still waiting on something")
new = [a for a in catch + running + look
       if re.search(r"^MDC1\.PH\d\.(ERR20[12]|HVR101|GAL(101|102|202))\.",
                    str(a.activity_id))]
print(f"of the rows, {len(new)} are in the six rooms added in v23")
for lst, t in ((catch, "CATCH-UP"), (running, "RUNNING NOW"),
               (look, "NEXT TWO WEEKS")):
    print(f"\n{t}  ({len(lst)})")
    for sc, rows in itertools.groupby(
            sorted(lst, key=scope), key=scope):
        rows = list(rows)
        print(f"   {sc:<22}{len(rows)}")

COLS = ("Scope", "Activity ID", "Activity", "Where", "Should start",
        "Should finish", "Days", "Days late", "Frees up",
        "Started / Done?", "Actual start", "Actual finish", "Notes")
WID = (20, 24, 48, 11, 13, 13, 6, 9, 9, 17, 14, 14, 34)
ENTRY = ("Started / Done?", "Actual start", "Actual finish", "Notes")
HEAD = Font(bold=True, color="FFFFFF", size=11)
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
SUB = Font(bold=True, size=11)
SUBF = PatternFill("solid", fgColor="DDEBF7")
thin = Side(style="thin", color="BFBFBF")
BD = Border(left=thin, right=thin, top=thin, bottom=thin)
JCOL = COLS.index("Started / Done?") + 1          # column J
assert get_column_letter(JCOL) == "J", get_column_letter(JCOL)

catch.sort(key=lambda a: (scope(a), D(a.early_start) or DD,
                          str(a.activity_id)))
running.sort(key=lambda a: (scope(a), D(a.early_finish) or DD))
look.sort(key=lambda a: (D(a.early_start) or DD, scope(a)))


def row(a, late):
    es, ef = D(a.early_start), D(a.early_finish)
    return (scope(a), str(a.activity_id), str(a.name), where(a), es, ef,
            round((a.planned_duration or 0) / 8),
            max(0, (DD - es).days) if (late and es) else None,
            releases(a))


wb = Workbook()
for lst, title, late in ((catch, "1 Catch-up", True),
                         (running, "2 Running now", False),
                         (look, "3 Next two weeks", False)):
    ws = wb.create_sheet(title)
    ws.append(COLS)
    for i, c in enumerate(ws[1], 1):
        c.font = HEAD
        c.fill = REDH if COLS[i - 1] in ENTRY else FILL
        c.alignment = Alignment(wrap_text=True, vertical="center",
                                horizontal="center")
        c.border = BD
    ws.row_dimensions[1].height = 34
    ecols = [COLS.index(k) + 1 for k in ENTRY]
    last = None
    for a in lst:
        sc = scope(a)
        if sc != last:
            ws.append((f"{sc}",))
            for c in ws[ws.max_row]:
                c.font, c.fill = SUB, SUBF
            last = sc
        ws.append(row(a, late) + ("", "", "", ""))
        for c in ws[ws.max_row]:
            c.border = BD
        for j in ecols:
            ws.cell(row=ws.max_row, column=j).fill = REDC
    for i, w in enumerate(WID, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for j in (5, 6, COLS.index("Actual start") + 1,
              COLS.index("Actual finish") + 1):
        for rr in ws.iter_rows(min_row=2, min_col=j, max_col=j):
            for c in rr:
                c.number_format = "mm/dd/yyyy"
    # Yes/No dropdown in column J, row 3 down. allow_blank so an untouched
    # row is not flagged, and showErrorMessage so a typo cannot get in.
    end = max(ws.max_row, 3)
    dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True,
                        showDropDown=False, showErrorMessage=True,
                        errorTitle="Pick Yes or No",
                        error="Choose Yes or No from the list.",
                        promptTitle="Started or done?",
                        prompt="Yes if this activity has started or finished, "
                               "No if it has not.")
    ws.add_data_validation(dv)
    dv.add(f"J3:J{end}")
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{ws.max_row}"
    print(f"\n{title}: rows 2..{ws.max_row}, dropdown on J3:J{end}")
del wb["Sheet"]
out = f"{SCR}/MDC1_Catchup_and_Lookahead_v26.xlsx"
wb.save(out)
print(f"\nwrote {out}")
print(f"rows: catch-up {len(catch)}, running {len(running)}, "
      f"lookahead {len(look)}  (total {len(catch) + len(running) + len(look)})")
