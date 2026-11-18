"""Build the MDC-3 contract-to-contract deliverable workbook."""
import collections
import datetime as dt
import json
import sys

import openpyxl
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.utils import get_column_letter

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
sys.path.insert(0, f"{SCR}/work")
import contextlib                                            # noqa: E402
import io                                                    # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    from mdc3_p1_1007 import P1, pd as pdate                 # noqa: E402
from mdc3_1007_bands import BANDS                            # noqa: E402

OUT = f"{SCR}/MDC3_Contract_Comparison_DRAFT.xlsx"
CURVE = json.load(open(f"{SCR}/work/mdc3_curve_data.json"))
C0204 = {r["id"]: r for r in
         json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))}

F = "Arial"
H1 = Font(name=F, size=14, bold=True)
H2 = Font(name=F, size=10, bold=True, color="FFFFFF")
BOLD = Font(name=F, size=10, bold=True)
BODY = Font(name=F, size=10)
SMALL = Font(name=F, size=9, italic=True, color="595959")
HDRFILL = PatternFill("solid", fgColor="1F3864")
BANDFILL = PatternFill("solid", fgColor="D9E2F3")
WARNFILL = PatternFill("solid", fgColor="FCE4D6")
BADFILL = PatternFill("solid", fgColor="F8CBAD")
NEWFILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DATEFMT = "dd-mmm-yy"

wb = openpyxl.Workbook()
wb.remove(wb.active)


def titleblock(ws, title, sub, widths):
    ws["A1"] = "CloudHQ MDC3 - JER Schedule Change Analysis"
    ws["A1"].font = H1
    ws["A2"] = title
    ws["A2"].font = Font(name=F, size=11, bold=True)
    ws["A3"] = sub
    ws["A3"].font = SMALL
    ws["A4"] = ("DRAFT for field review - 02/04/2026 Current Contract vs "
                "10/07/2026 Proposed Exhibit S")
    ws["A4"].font = Font(name=F, size=9, bold=True, color="C00000")
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A7"
    # Landscape, fit to one page wide. Without it a 34cm chart is sliced
    # across three sheets of paper and the tabs print unusable.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:6"


ALERTFILL = PatternFill("solid", fgColor="C00000")
ALERT_TEXT = 'ALERT - PROJECTION MAY BE SHORT BY 50,604 h.  The 10/07 carries 122,911 h of scope with no counterpart in the 02/04 (galleries + HV/HVR). JER has booked only 72,307 h of growth. Assumed here that the 533,400 baseline did NOT include this scope - CONFIRM WITH FIELD.'


def alert(ws, text, span=8):
    """A banner on row 5, between the title block and the data.

    The 50,604 h gap is the kind of finding that gets lost in a note at the
    bottom of an audit tab. Working on the instruction that the 533,400 did
    NOT include the galleries, it says JER's own projection may be short --
    which is against JER's interest to leave quiet, so it goes where nobody
    can scroll past it.
    """
    ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=span)
    c = ws.cell(row=5, column=1, value=text)
    c.font = Font(name=F, size=10, bold=True, color="FFFFFF")
    c.fill = ALERTFILL
    c.alignment = Alignment(horizontal="left", vertical="center",
                            wrap_text=True)
    ws.row_dimensions[5].height = 30


def header(ws, row, cells):
    for i, t in enumerate(cells, start=1):
        c = ws.cell(row=row, column=i, value=t)
        c.font = H2
        c.fill = HDRFILL
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
        c.border = BOX
    ws.row_dimensions[row].height = 30


def put(ws, r, c, v, font=BODY, fmt=None, fill=None, align=None):
    cell = ws.cell(row=r, column=c, value=v)
    cell.font = font
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    if align:
        cell.alignment = Alignment(horizontal=align)
    return cell


# ===================================================== 1. MILESTONES
ws = wb.create_sheet("Milestones")
titleblock(ws, "Milestone Comparison - every milestone, both contracts",
           "Every milestone printed on page 1 of the 10/07 Exhibit S (62 "
           "rows), matched to the 02/04 contract by activity ID, falling "
           "back to milestone NAME where Holder renumbered it. Dates are as "
           "printed in each contract. Move = 10/07 date minus 02/04 date.",
           [22, 42, 13, 13, 11, 18, 10, 32])
header(ws, 6, ["Activity ID (10/07)", "Milestone", "02/04 Contract",
               "10/07 Proposed", "Move (days)", "Direction", "Flag",
               "Note"])


def norm(n):
    return " ".join(str(n).split()).lower().replace(" - ", " ").replace("-", " ")


BY = {}
for r in C0204.values():
    BY.setdefault(norm(r["name"]), r)

rows = []
for aid, nm, s, f in P1:
    nd, nflag = pdate(f or s)
    c = C0204.get(aid) or BY.get(norm(nm))
    if c is None or nd is None:
        rows.append((aid, nm, None, nd, None, "NEW", "no 02/04 counterpart"))
        continue
    od = (dt.date.fromisoformat(c["finish"]) if c["finish"]
          else dt.date.fromisoformat(c["start"]) if c["start"] else None)
    if od is None:
        continue
    note = ""
    if c["id"] != aid:
        note = f"renumbered from {c['id']}"
    # The 02/04 data date is 05-Dec-25. A milestone sitting on top of it
    # was never scheduled in that contract -- "Permanent Power Available"
    # reads 08-Dec-25, which turns into a +595 day move and lands at the
    # top of the list. That is an artefact of an unscheduled milestone, not
    # a real slip, and leaving it unflagged invites the whole tab to be
    # waved off. Flagged, and sorted below the genuine moves.
    placeholder = abs((od - dt.date(2025, 12, 5)).days) <= 7
    if placeholder:
        note = ("02/04 date sits on that contract's data date - unscheduled "
                "placeholder, not a true comparison")
    rows.append((aid, nm, od, nd, (nd - od).days,
                 "CONTRACT DATE" if "*" in nflag else
                 ("CHECK" if placeholder else ""), note))

rows.sort(key=lambda x: (x[5] == "CHECK",
                         -(x[4] if x[4] is not None else -9999)))
r = 7
for aid, nm, od, nd, dlt, flag, note in rows:
    fill = BADFILL if (dlt or 0) > 100 else (
        WARNFILL if (dlt or 0) > 60 else (NEWFILL if flag == "NEW" else None))
    put(ws, r, 1, aid, fill=fill)
    put(ws, r, 2, nm, fill=fill)
    put(ws, r, 3, od, fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 4, nd, fmt=DATEFMT, fill=fill, align="center")
    if dlt is not None:
        put(ws, r, 5, dlt, fmt="+0;-0;0", fill=fill, align="center",
            font=BOLD if abs(dlt) > 100 else BODY)
        # "+218" alone makes a reader stop and work out which way that is.
        # Spell it: a positive move is LATER, further right on the bar chart.
        dirn = ("LATER  (shift right)" if dlt > 0 else
                "EARLIER  (shift left)" if dlt < 0 else "no change")
        put(ws, r, 6, dirn, fill=fill, align="center",
            font=BOLD if abs(dlt) > 100 else BODY)
    else:
        put(ws, r, 5, None, fill=fill)
        put(ws, r, 6, "not in 02/04", fill=fill, align="center", font=SMALL)
    put(ws, r, 7, flag, fill=fill, font=BOLD if flag else BODY, align="center")
    put(ws, r, 8, note, fill=fill, font=SMALL)
    r += 1
put(ws, r + 1, 2, "Average move, all matched milestones", font=BOLD)
put(ws, r + 1, 5, f"=ROUND(AVERAGE(E7:E{r - 1}),0)", font=BOLD,
    fmt="+0;-0;0", align="center")
put(ws, r + 1, 6, "LATER  (shift right)", font=BOLD, align="center")
put(ws, r + 3, 1, "POSITIVE = the milestone moved LATER (right on the bar "
                  "chart).  NEGATIVE = it moved EARLIER (left).", font=BOLD)
put(ws, r + 4, 1, "Rows flagged CHECK sit on the 02/04 contract's own data "
                  "date (05-Dec-25) - unscheduled placeholders, not a real "
                  "comparison. They are sorted to the bottom.", font=SMALL)

# ===================================================== 2. COMPRESSION
ws = wb.create_sheet("Compression")
titleblock(ws, "Compression - windows that shrank between the two contracts",
           "Each row is the gap between two named milestones, measured "
           "inside each contract separately. Both ends matched by NAME, so "
           "Holder's renumbering cannot skew it. Days are calendar days. "
           "NEGATIVE change = the window is SHORTER in the 10/07 = "
           "compression.",
           [58, 13, 13, 13, 13, 12, 12, 30])
header(ws, 6, ["Span", "02/04 start", "02/04 finish", "10/07 start",
               "10/07 finish", "02/04 days", "10/07 days", "Change"])

NEW = {nm: pdate(f or s)[0] for aid, nm, s, f in P1}


def old(nm):
    rr = BY.get(norm(nm))
    if not rr:
        return None
    return (dt.date.fromisoformat(rr["finish"]) if rr["finish"]
            else dt.date.fromisoformat(rr["start"]) if rr["start"] else None)


SPANS = [
    ("Finish Precast -> Set First Piece of Major Equipment",
     "Finish Precast", "Set First Piece of Major Equipment"),
    ("100% CDs/IFCs Received -> Set First Piece of Major Equipment",
     "100% CDs/IFCs Received", "Set First Piece of Major Equipment"),
    ("Level 3 Commissioning PH3 (start to finish)",
     "Level 3 Commissioning Start (PH3)",
     "Level 3 Commissioning Finish (PH3)"),
    ("Level 3 Commissioning PH2 (start to finish)",
     "Level 3 Commissioning Start (PH2)",
     "Level 3 Commissioning Finish (PH2)"),
    ("Level 3 Commissioning PH1 (start to finish)",
     "Level 3 Commissioning Start (PH1)",
     "Level 3 Commissioning Finish (PH1)"),
    ("Level 3 Cx Start -> Substantial Completion (PH3)",
     "Level 3 Commissioning Start (PH3)", "Substantial Completion (PH3)"),
    ("Building Systems Ready -> Substantial Completion (PH1)",
     "Building Systems Ready for Energization",
     "Substantial Completion (PH1)"),
    ("Level 3 Cx Start -> Substantial Completion (PH2)",
     "Level 3 Commissioning Start (PH2)", "Substantial Completion (PH2)"),
    ("Substantial Completion PH1 -> PH3 (turnover stagger)",
     "Substantial Completion (PH1)", "Substantial Completion (PH3)"),
    ("Level 4 Commissioning PH3 (start to finish)",
     "Level 4 Commissioning Start (PH3)",
     "Level 4 Commissioning Finish (PH3)"),
    ("Start Precast -> Finish Precast",
     "Start Precast", "Finish Precast"),
    ("Set First Major Equipment -> Equipment Energization Start",
     "Set First Piece of Major Equipment", "Equipment Energization Start"),
]
r = 7
for lab, a, b in SPANS:
    oa, ob, na, nb = old(a), old(b), NEW.get(a), NEW.get(b)
    if not all((oa, ob, na, nb)):
        continue
    o, n = (ob - oa).days, (nb - na).days
    fill = BADFILL if n - o < -60 else (WARNFILL if n - o < 0 else None)
    put(ws, r, 1, lab, fill=fill)
    for i, d in enumerate([oa, ob, na, nb], start=2):
        put(ws, r, i, d, fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 6, o, fill=fill, align="center")
    put(ws, r, 7, n, fill=fill, align="center")
    put(ws, r, 8, f"=G{r}-F{r}", fmt="+0;-0;0", fill=fill, align="center",
        font=BOLD if n - o < 0 else BODY)
    r += 1
put(ws, r + 1, 1, "Negative = the window is shorter in the proposed "
                  "schedule. Both ends matched by milestone name, so "
                  "Holder's renumbering cannot skew it.", font=SMALL)

# ===================================================== 3. TRADE STACKING
ws = wb.create_sheet("Trade Stacking")
titleblock(ws, "Trade Stacking - how much work runs at the same time",
           "Read top to bottom. Three questions, answered in order: how "
           "many electrical rooms are open each month, when each room runs, "
           "and what else overlaps.",
           [30, 14, 14, 14, 11, 44])
MONB = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def pdb(s):
    s = s.replace(" A", "").strip()
    d, m, y = s.split("-")
    return dt.date(2000 + int(y), MONB[m], int(d))


def section(ws, row, title, explain):
    """A banner and one plain line saying what the reader is looking at.

    The first version of this tab put three unrelated tables side by side
    with blank spacer columns. Everything here is now one column set,
    stacked, each block introduced.
    """
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    c = ws.cell(row=row, column=1, value=title)
    c.font = Font(name=F, size=11, bold=True, color="FFFFFF")
    c.fill = HDRFILL
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 22
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1,
                   end_column=6)
    c = ws.cell(row=row + 1, column=1, value=explain)
    c.font = SMALL
    c.alignment = Alignment(horizontal="left", vertical="center")
    return row + 2


DC = {}
for n in range(208, 215):
    DC[f"ER {n}"] = "A  (Phase 1)"
for n in range(201, 208):
    DC[f"ER {n}"] = "B  (Phase 2)"
for n in range(101, 108):
    DC[f"ER {n}"] = "C  (Phase 3)"
DC["ERR 202"], DC["ERR 201"], DC["ERR 101"] = ("A  (Phase 1)", "B  (Phase 2)",
                                               "C  (Phase 3)")
rooms = [(nm, dur, pdb(s), pdb(f)) for nm, dur, s, f, pg in BANDS
         if nm.startswith(("ER 1", "ER 2", "ERR 1", "ERR 2"))]

r = 6
ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
c = ws.cell(row=r, column=1,
            value="HEADLINE:  on 15-Apr-2027 all 24 electrical rooms across "
                  "the three datacenters are open at the same time.")
c.font = Font(name=F, size=12, bold=True, color="FFFFFF")
c.fill = ALERTFILL
c.alignment = Alignment(horizontal="left", vertical="center")
ws.row_dimensions[r].height = 24

r = section(ws, r + 2, "1.  HOW MANY ELECTRICAL ROOMS ARE OPEN, MONTH BY "
            "MONTH", "Counted on the 15th of each month from the 10/07 "
            "start and finish dates. 24 rooms in total.")
header(ws, r, ["Month", "Rooms open", "Out of", "% of all rooms", "",
               "What this means"])
r += 1
chart_first = r
prev = 0
for i in range(13):
    d = dt.date(2027, 1, 15)
    d = (d.replace(day=1) + dt.timedelta(days=32 * i)).replace(day=15)
    if d > dt.date(2028, 1, 15):
        break
    n = sum(1 for _, _, s, f in rooms if s <= d <= f)
    fill = BADFILL if n >= 20 else (WARNFILL if n >= 12 else None)
    note = ("PEAK - every room in all three buildings" if n == 24 else
            "none open" if n == 0 else
            "heavy overlap" if n >= 18 else
            "ramping up" if n > prev else
            "coming down" if n < prev else "holding")
    prev = n
    put(ws, r, 1, d.strftime("%B %Y"), fill=fill)
    put(ws, r, 2, n, fill=fill, align="center", font=BOLD)
    put(ws, r, 3, 24, fill=fill, align="center")
    put(ws, r, 4, f"=B{r}/C{r}", fmt="0%", fill=fill, align="center")
    put(ws, r, 5, None, fill=fill)
    put(ws, r, 6, note, fill=fill, font=SMALL)
    r += 1
chart_last = r - 1

rc = BarChart()
rc.type = "col"
rc.gapWidth = 0
rc.title = "Electrical rooms open at the same time"
rc.y_axis.title = "Rooms open"
rc.x_axis.title = "Month"
rc.height, rc.width = 8, 18
rc.roundedCorners = False
rc.y_axis.delete = False
rc.x_axis.delete = False
rc.add_data(Reference(ws, min_col=2, max_col=2, min_row=chart_first - 1,
                      max_row=chart_last), titles_from_data=True)
rc.set_categories(Reference(ws, min_col=1, min_row=chart_first,
                            max_row=chart_last))
rc.series[0].graphicalProperties.solidFill = "C00000"
rc.series[0].graphicalProperties.line.noFill = True
if rc.legend:
    rc.legend.overlay = False
    rc.legend.position = "t"
if rc.title:
    rc.title.overlay = False
ws.add_chart(rc, "H8")

r = section(ws, r + 1, "2.  WHEN EACH ELECTRICAL ROOM RUNS",
            "Sorted by start date. Datacenter A is Phase 1, B is Phase 2, "
            "C is Phase 3. Note the three buildings interleave rather than "
            "follow one another.")
header(ws, r, ["Electrical room", "Datacenter", "Start", "Finish",
               "Duration (days)", "Rooms already open on its start date"])
r += 1
for nm, dur, s, f in sorted(rooms, key=lambda x: x[2]):
    ov = sum(1 for n2, d2, s2, f2 in rooms
             if n2 != nm and s2 <= s <= f2)
    fill = BADFILL if ov >= 18 else (WARNFILL if ov >= 12 else None)
    put(ws, r, 1, nm, fill=fill)
    put(ws, r, 2, DC.get(nm, "?"), fill=fill, align="center")
    put(ws, r, 3, s, fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 4, f, fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 5, dur, fill=fill, align="center")
    put(ws, r, 6, ov, fill=fill, align="center",
        font=BOLD if ov >= 18 else BODY)
    r += 1

r = section(ws, r + 1, "3.  COMMISSIONING OVERLAP",
            "The three phases were meant to hand over one after another. "
            "In the 10/07 they run together.")
header(ws, r, ["Phase", "Days", "Start", "Finish", "", "Note"])
r += 1
for nm, dur, s, f, pg in BANDS:
    if "Commissioning / Closeout" in nm:
        put(ws, r, 1, "Phase " + nm.split("(PH")[1].rstrip(")"))
        put(ws, r, 2, dur, align="center")
        put(ws, r, 3, pdb(s), fmt=DATEFMT, align="center")
        put(ws, r, 4, pdb(f), fmt=DATEFMT, align="center")
        put(ws, r, 5, None)
        put(ws, r, 6, "", font=SMALL)
        r += 1
ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
c = ws.cell(row=r, column=1,
            value="All three phases are live together from 23-Aug-27 to "
                  "20-Dec-27 = 119 days.")
c.font = BOLD
c.fill = WARNFILL
r += 2

r = section(ws, r, "4.  WORK HOLDER HAS ALREADY PUT ON A SIX-DAY WEEK",
            "Each of these has a duration that only works on a six-day "
            "calendar - matched to within 0.5%. Premium time is already "
            "assumed in the proposed schedule, and is not priced.")
header(ws, r, ["Activity", "Days", "Start", "Finish", "", "Note"])
r += 1
SIX = ["Long Lead Equipment Delivery",
       "Roof Mechanical/Electrical Installation", "Equipment Set",
       "Admin Space", "Mechanical Platform", "Roofing System Installation",
       "2nd Floor Office Build-Out", "1st Floor Admin Build-Out"]
for nm, dur, s, f, pg in BANDS:
    if nm in SIX:
        put(ws, r, 1, nm, fill=WARNFILL)
        put(ws, r, 2, dur, align="center", fill=WARNFILL)
        put(ws, r, 3, pdb(s), fmt=DATEFMT, align="center", fill=WARNFILL)
        put(ws, r, 4, pdb(f), fmt=DATEFMT, align="center", fill=WARNFILL)
        put(ws, r, 5, None, fill=WARNFILL)
        put(ws, r, 6, "6-day week assumed", fill=WARNFILL, font=SMALL)
        r += 1


# ===================================================== 4. LABOR CURVE
ws = wb.create_sheet("Labor Curve")
titleblock(ws, "Labour Curve - baseline (02/04) vs projected (10/07)",
           "One row per week, week ending SUNDAY (matching the JER labour "
           "report). Crew = hours / 50. Columns C,E,G are the three P6 "
           "series: BL Project Labor, Actual Labor, Remaining Labor. "
           "Actual runs to the 25-Sep-26 data date; Remaining is everything "
           "after it. C sums to 533,400 (baseline at contract). E+G sums to "
           "605,707 (JER projection at 08-Nov-26).",
           [13, 12, 13, 12, 13, 12, 14, 13, 14, 14])
alert(ws, ALERT_TEXT, span=10)
header(ws, 6, ["Week Ending", "BL Crew", "BL Project Labor (h)",
               "Actual Crew", "Actual Labor (h)", "Remaining Crew",
               "Remaining Labor (h)", "Projected Total (h)",
               "Cum Baseline", "Cum Projected"])
r = 7
first = r
DD = dt.date.fromisoformat(CURVE["data_date"])
for w in CURVE["weeks"]:
    wd = dt.date.fromisoformat(w["w"])
    act = round(w["actual"], 0)
    rem = round(w["cur"] - w["actual"], 0)
    put(ws, r, 1, wd, fmt=DATEFMT, align="center")
    put(ws, r, 2, f"=ROUND(C{r}/50,0)", align="center")
    put(ws, r, 3, round(w["base"], 0), fmt="#,##0", align="right")
    put(ws, r, 4, f"=ROUND(E{r}/50,0)", align="center")
    put(ws, r, 5, act, fmt="#,##0", align="right")
    put(ws, r, 6, f"=ROUND(G{r}/50,0)", align="center")
    put(ws, r, 7, rem, fmt="#,##0", align="right")
    put(ws, r, 8, f"=E{r}+G{r}", fmt="#,##0", align="right")
    put(ws, r, 9, f"=SUM($C${first}:C{r})", fmt="#,##0", align="right")
    put(ws, r, 10, f"=SUM($H${first}:H{r})", fmt="#,##0", align="right")
    r += 1
last = r - 1
put(ws, r, 1, "TOTAL", font=BOLD)
for col in (3, 5, 7, 8):
    put(ws, r, col, f"=SUM({get_column_letter(col)}{first}:"
                    f"{get_column_letter(col)}{last})",
        font=BOLD, fmt="#,##0", align="right")
put(ws, r + 1, 1, "PEAK", font=BOLD)
put(ws, r + 1, 2, f"=ROUND(MAX(C{first}:C{last})/50,0)", font=BOLD,
    align="center")
put(ws, r + 1, 3, f"=MAX(C{first}:C{last})", font=BOLD, fmt="#,##0",
    align="right")
put(ws, r + 1, 6, f"=ROUND(MAX(H{first}:H{last})/50,0)", font=BOLD,
    align="center")
put(ws, r + 1, 8, f"=MAX(H{first}:H{last})", font=BOLD, fmt="#,##0",
    align="right")

# P6 draws its histogram with the bars touching -- no gap between periods
# and none between the series inside a period. gapWidth 0 with overlap 0
# is that look; the default 150/-10 is what makes an Excel chart read as
# an Excel chart rather than a usage profile.
P6_RED, P6_BLUE, P6_GREEN = "FF0000", "0000FF", "00B050"


def p6chart(title, cols, colours, anchor, stacked=False):
    ch = BarChart()
    ch.type = "col"
    ch.grouping = "stacked" if stacked else "clustered"
    ch.overlap = 100 if stacked else 0
    ch.gapWidth = 0
    ch.title = title
    ch.y_axis.title = "Units (hours per week)"
    ch.x_axis.title = "Week ending"
    ch.height, ch.width = 10.5, 34
    ch.roundedCorners = False
    ch.y_axis.delete = False
    ch.x_axis.delete = False
    ch.x_axis.tickLblSkip = 13
    ch.x_axis.tickMarkSkip = 13
    for col, colour in zip(cols, colours):
        ref = Reference(ws, min_col=col, max_col=col, min_row=6,
                        max_row=last)
        ch.add_data(ref, titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    for s, colour in zip(ch.series, colours):
        s.graphicalProperties.solidFill = colour
        s.graphicalProperties.line.noFill = True
    if ch.legend:
        ch.legend.overlay = False
        ch.legend.position = "t"
    if ch.title:
        ch.title.overlay = False
    ws.add_chart(ch, anchor)
    return ch


p6chart("1. BASELINE - 02/04 Contract (BL Project Labor, 533,400 h)",
        [3], [P6_RED], "L6")
p6chart("2. PROJECTED - 10/07 Proposed (Actual to 25-Sep-26, then "
        "Remaining; 605,707 h)",
        [5, 7], [P6_BLUE, P6_GREEN], "L28", stacked=True)
ch = p6chart("3. COMBINED - Baseline vs Projected (P6 usage profile)",
             [3, 5, 7], [P6_RED, P6_BLUE, P6_GREEN], "L50")

# S-curve on the combined chart only, so charts 1 and 2 stay clean
ln = LineChart()
cum = Reference(ws, min_col=9, max_col=10, min_row=6, max_row=last)
ln.add_data(cum, titles_from_data=True)
ln.y_axis.axId = 200
ln.y_axis.title = "Cumulative hours (S-curve)"
ln.y_axis.delete = False
# Without this the secondary scale is drawn on the LEFT, on top of the
# primary one, and both sets of numbers become unreadable.
ln.y_axis.crosses = "max"
ln.series[0].graphicalProperties.line.solidFill = "A50021"
ln.series[0].graphicalProperties.line.width = 22000
ln.series[1].graphicalProperties.line.solidFill = "1F4E79"
ln.series[1].graphicalProperties.line.width = 22000
for s in ln.series:
    s.smooth = False
ch.y_axis.crosses = "autoZero"
ch += ln


# ===================================================== 5. CROSSWALK
ws = wb.create_sheet("Scope Crosswalk")
titleblock(ws, "Scope Crosswalk - how hours were placed in each contract",
           "THIS IS THE AUDIT TAB. Every hour in both curves comes from a "
           "row here. Hours are our P6 v32 rescaled to the basis; the 02/04 "
           "columns are that contract's own date range for the bucket; the "
           "10/07 columns are the matching WBS band. Check the mapping "
           "before trusting the curves.",
           [28, 12, 9, 13, 13, 38, 13, 13, 14])
alert(ws, ALERT_TEXT, span=9)
header(ws, 6, ["Scope bucket", "Hours", "Acts", "02/04 start",
               "02/04 finish", "10/07 band it maps to", "10/07 start",
               "10/07 finish", "In 02/04?"])
r = 7
for x in sorted(CURVE["rows"], key=lambda z: -z["hours"]):
    fill = NEWFILL if x["new_scope"] else None
    put(ws, r, 1, x["bucket"], fill=fill)
    put(ws, r, 2, round(x["hours"], 0), fmt="#,##0", fill=fill, align="right")
    put(ws, r, 3, x["acts"], fill=fill, align="center")
    for i, k in enumerate(["base_start", "base_finish"], start=4):
        put(ws, r, i, dt.date.fromisoformat(x[k]) if x[k] else None,
            fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 6, x["prop_band"], fill=fill)
    for i, k in enumerate(["prop_start", "prop_finish"], start=7):
        put(ws, r, i, dt.date.fromisoformat(x[k]) if x[k] else None,
            fmt=DATEFMT, fill=fill, align="center")
    put(ws, r, 9, "NEW SCOPE" if x["new_scope"] else "yes", fill=fill,
        font=BOLD if x["new_scope"] else BODY, align="center")
    r += 1
put(ws, r, 1, "TOTAL", font=BOLD)
put(ws, r, 2, f"=SUM(B7:B{r - 1})", font=BOLD, fmt="#,##0", align="right")
r += 2
put(ws, r, 1, "Shaded rows have NO counterpart in the 02/04 contract - "
              "searched by activity ID and by name across all 1,957 "
              "activities. Carried in the projected curve, excluded from "
              "the baseline.", font=SMALL)

# Where the hours come from. The 02/04 Exhibit S has no labour in it at
# all -- its columns are Activity ID, Activity Name, At Completion
# Duration, Start, Finish, and a search of all 27 pages for units / labor /
# hours / budgeted / resource / cost returns nothing. It supplies DATES.
# Saying so on the face of the audit tab, because "where is the 533,400 in
# the contract" is the first question anyone will ask.
r += 2
put(ws, r, 1, "WHERE THE NUMBERS COME FROM", font=BOLD)
r += 1
for line, fnt in [
    ("The 02/04 Exhibit S contains NO hours. Its columns are Activity ID, "
     "Activity Name, At Completion Duration, Start, Finish. All 27 pages "
     "were searched for units / labor / hours / budgeted / resource / cost "
     "- no such column exists.", BODY),
    ("So the 02/04 supplies DATES ONLY. Every hour in this workbook is "
     "JER's.", BOLD),
    ("", BODY),
    ("BASELINE  533,400 h - JER planned at contract, spread across the "
     "02/04 dates.", BODY),
    ("PROJECTED 605,707 h - JER projection as of 08-Nov-26, spread across "
     "the 10/07 dates, with actual payroll to 20-Sep-26.", BODY),
    ("GROWTH     72,307 h  (+13.6%) booked by JER between the two.", BOLD),
    ("", BODY),
    ("Scope found in the 10/07 with no 02/04 counterpart: 122,911 h "
     "(galleries + HV/HVR).", BODY),
    ("That is 50,604 h MORE than the growth booked.", BOLD),
    ("WORKING ASSUMPTION: the 533,400 baseline did NOT include this scope. "
     "On that basis the 605,707 projection is short by 50,604 h. Confirm "
     "with the field before the final issue.", BOLD),
    ("", BODY),
    ("Within a bucket, hours keep their relative position from our P6 and "
     "are mapped onto the target window, so the internal ramp survives. "
     "Both curves are built at the same level of detail on purpose - a "
     "finer baseline would flatten the proposal and understate the "
     "acceleration.", SMALL)]:
    c = put(ws, r, 1, line, font=fnt)
    c.border = Border()
    r += 1

# ===================================================== 6. FIELD QUESTIONS
ws = wb.create_sheet("Field Questions")
titleblock(ws, "Open questions - answer these before the final issue",
           "Each one moves a number in this workbook. Nothing here is "
           "rhetorical.", [6, 62, 16, 46])
alert(ws, ALERT_TEXT, span=4)
header(ws, 6, ["#", "Question", "Hours at stake", "Why it matters"])
Q = [
    ("Does the 605,707 projection fully absorb the galleries and HV rooms?",
     "50,604",
     "We can identify 122,911 h in the 10/07 with no 02/04 counterpart, but "
     "only 72,307 h of growth has been booked. Either some of that scope "
     "was already inside the 533,400, or the projection is short by about "
     "50,604 h. This is the biggest open number in the workbook."),
    ("Are the galleries JER scope?",
     "111,675",
     "Assumed YES in this draft. No counterpart anywhere in the 02/04. If "
     "yes, this is new work and prices separately from acceleration."),
    ("Why does our P6 carry paint, drywall and fan walls with hours on "
     "them?", "111,008",
     "384 activities named for other trades carry hours. Either JER's total "
     "was spread across all trades, or these are placeholders."),
    ("Do we trust the per-activity loading, or only the 533,400 total?",
     "whole curve shape",
     "As loaded it predicts 20 weeks of payroll within 19%. Filtering to "
     "electrical under-predicts by 58%. Used as loaded here."),
    ("Are HV / HVR rooms JER scope and genuinely new?", "11,237",
     "Zero presence in the 02/04 and 83% electrically named. Cleanest new "
     "work candidate in the file."),
    ("Is the roof MEP scope in the 02/04 or not?", "27,887",
     "The 02/04 has 7 coarse roof activities incl Mechanical Piping. We "
     "carry 97. Treated as ORIGINAL scope in this draft."),
    ("Is the fire alarm breakdown where Holder accounts for gallery work?",
     "affects Q1",
     "If so, the gallery gap may be an itemisation difference rather than "
     "new scope, and Q1 reverses."),
    ("Does 'Data Hall 201 (Expanded)' mean added scope?", "unknown",
     "Holder's own band name. No 02/04 counterpart for the word Expanded. "
     "Worth asking directly."),
    ("Can we get the XER for MDC3-ExhibitS-20260925?", "n/a",
     "The 10/07 PDF has no text layer. Without logic we can quantify the "
     "compression but not name which ties cause it."),
]
r = 7
for i, (q, h, w) in enumerate(Q, start=1):
    put(ws, r, 1, i, align="center", font=BOLD)
    c = put(ws, r, 2, q, font=BOLD)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    put(ws, r, 3, h, align="center")
    c = put(ws, r, 4, w)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r].height = 46
    r += 1

wb.save(OUT)
print(f"wrote {OUT}")
for s in wb.sheetnames:
    print(f"   tab: {s}")
