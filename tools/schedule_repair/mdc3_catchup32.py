"""MDC-3 catch-up and lookahead, in the format MDC-1 ended up with.

The sheet that went out had eighteen columns, including Blocked by, Still
open, Status this head and Why it is here. That is a planner's view, and the
point of this one is that a foreman fills it in without being walked through
it. Thirteen columns, four of them his, red and empty, with column J a Yes/No
dropdown from row 3 down so he picks instead of typing.

TWO THINGS ABOUT THE WINDOW. Dates are the EARLY dates, as on MDC-1: P6
freezes the planned dates and does not move them on a schedule run, so the
early dates are what the Start and Finish columns actually show.

And the window is not fourteen days from the data date. This file is statused
to 10/24 and that window closed on 11/07, which would hand the field a
lookahead entirely in the past -- seventeen activities, all of them already
overdue by the time anyone read it. It runs from the data date to 11/26
instead, two weeks out from today, which is forty-four. The catch-up is still
measured against the 10/24 data date, because that is what has to be true
before the schedule is run.
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
TODAY = dt.date(2026, 11, 12)
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
NOTJER = re.compile(r"^(Red Tag|Holder |GC )", re.I)
UG = re.compile(r"underground|in-slab|in slab|stub ?up|\bHES\b", re.I)


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
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


DD = D(p.data_date)
HZ = TODAY + dt.timedelta(days=14)
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)


def jer(a):
    n = str(a.name)
    return not WBO.search(n) and not NOTJER.match(n)


def ready(a):
    return not [1 for q in pred[a.uid]
                if q in byu and byu[q].status != "Completed"]


def releases(a):
    return len([v for v in succ[a.uid] if v in byu
                and byu[v].status == "Not Started"])


def scope(a):
    pth = path(a.wbs_uid)
    nm, aid = str(a.name), str(a.activity_id or "")
    if "Labour Record" in pth:
        return "Labour record"
    if ".ST.PC." in aid:
        return "Precast"
    if ".ST.SHF." in aid:
        return "Shallow foundations"
    if ".STR.DP." in aid:
        return "Deep foundations"
    if ".ST.ELEV." in aid:
        return "Elevators"
    if ".STR.UDG." in aid or (UG.search(nm) and ".ST.L" in aid):
        m = re.match(r"MDC3\.ST\.(L\d)\.", aid)
        return (f"Underground MEP - {m.group(1).replace('L', 'Level ')}"
                if m else "Underground MEP - building")
    m = re.match(r"MDC3\.ST\.(L\d)\.", aid)
    if m:
        return f"Slab pours - {m.group(1).replace('L', 'Level ')}"
    if ".EXT.SUT." in aid or ".UTLI." in aid or "Site Utilities" in pth:
        return "Site utilities"
    if "LLE" in pth or ".SK.AS." in aid or ".XFMR." in aid:
        return "Long lead equipment"
    if ".ROOF." in aid or re.search(r"Roof", pth):
        return "Roof"
    g = re.search(r"/ ((?:ER|MV|HV|HVR|ERR|ELEC) ?R?\d{3})$", path(a.wbs_uid))
    if g:
        return f"{g.group(1)[:2].strip()} rooms"
    if "Data Hall" in pth or "Gallery" in pth:
        return "Data halls & galleries"
    if "Generator" in pth:
        return "Generator rooms"
    if "Admin" in pth:
        return "Admin space"
    if "Milestone" in pth:
        return "Milestones"
    return pth.split(" / ")[0] if pth else "Other"


def where(a):
    s = f"{a.activity_id} {a.name} {path(a.wbs_uid)}"
    m = re.search(r"\b((?:ERR|HVR|ER|MV|HV|GEN|GL|GAL|DH|ELEC|POE)"
                  r"[\s\.\-_]*R?[\s\.\-_]*\d{3})\b", s, re.I)
    if m:
        return re.sub(r"[\s\.\-_]+", " ", m.group(1)).upper()
    m = re.search(r"Segment ([A-Z])", s)
    if m:
        return f"Segment {m.group(1)}"
    return path(a.wbs_uid).split(" / ")[-1][:28]


catch, running, look = [], [], []
for a in p.activities:
    if not jer(a) or a.status == "Completed":
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

print(f"MDC-3 v32   data date {DD:%m/%d/%Y}   lookahead to {HZ:%m/%d/%Y}"
      f"   (two weeks from today {TODAY:%m/%d})")
print(f"catch-up (overdue AND ready)   {len(catch):>5}")
print(f"running now, due in the window {len(running):>5}")
print(f"starting in the window         {len(look):>5}")
blocked = sum(1 for a in p.activities
              if a.status == "Not Started" and jer(a)
              and D(a.early_start) and D(a.early_start) < DD and not ready(a))
print(f"\nleft out: {blocked} overdue activities still waiting on something")
for lst, t in ((catch, "CATCH-UP"), (running, "RUNNING NOW"),
               (look, "IN THE WINDOW")):
    print(f"\n{t}  ({len(lst)})")
    for sc, rows in itertools.groupby(sorted(lst, key=scope), key=scope):
        print(f"   {sc:<30}{len(list(rows))}")

COLS = ("Scope", "Activity ID", "Activity", "Where", "Should start",
        "Should finish", "Days", "Days late", "Frees up",
        "Started / Done?", "Actual start", "Actual finish", "Notes")
WID = (24, 26, 48, 13, 13, 13, 6, 9, 9, 17, 14, 14, 34)
ENTRY = ("Started / Done?", "Actual start", "Actual finish", "Notes")
HEAD = Font(bold=True, color="FFFFFF", size=11)
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
SUB = Font(bold=True, size=11)
SUBF = PatternFill("solid", fgColor="DDEBF7")
thin = Side(style="thin", color="BFBFBF")
BD = Border(left=thin, right=thin, top=thin, bottom=thin)
JCOL = COLS.index("Started / Done?") + 1
assert get_column_letter(JCOL) == "J"

catch.sort(key=lambda a: (scope(a), D(a.early_start) or DD,
                          str(a.activity_id)))
running.sort(key=lambda a: (scope(a), D(a.early_finish) or DD))
look.sort(key=lambda a: (D(a.early_start) or DD, scope(a)))


def row(a, late):
    es, ef = D(a.early_start), D(a.early_finish)
    ref = ef if a.status == "In Progress" else es
    return (scope(a), str(a.activity_id), str(a.name), where(a), es, ef,
            round((a.planned_duration or 0) / 8),
            max(0, (DD - ref).days) if (late and ref) else None,
            releases(a))


wb = Workbook()
for lst, title, late in ((catch, "1 Catch-up", True),
                         (running, "2 Running now", True),
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
    print(f"\n{title}: rows 2..{ws.max_row}, dropdown J3:J{end}")
del wb["Sheet"]
out = f"{SCR}/MDC3_Catchup_and_Lookahead_v32.xlsx"
wb.save(out)
print(f"\nwrote {out}")
print(f"rows: catch-up {len(catch)}, running {len(running)}, "
      f"window {len(look)}  (total {len(catch) + len(running) + len(look)})")
