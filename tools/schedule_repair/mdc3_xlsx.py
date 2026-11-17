"""Build the MDC-3 contract-to-contract deliverable workbook."""
import collections
import datetime as dt
import json
import sys

import openpyxl
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
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


ALERTFILL = PatternFill("solid", fgColor="C00000")


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
           "Negative = earlier in the proposed schedule. Positive = later. "
           "Sorted by size of move.",
           [22, 44, 13, 13, 11, 10, 34])
header(ws, 6, ["Activity ID (10/07)", "Milestone", "02/04 Contract",
               "10/07 Proposed", "Move (days)", "Flag", "Note"])


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
    else:
        put(ws, r, 5, None, fill=fill)
    put(ws, r, 6, flag, fill=fill, font=BOLD if flag else BODY, align="center")
    put(ws, r, 7, note, fill=fill, font=SMALL)
    r += 1
put(ws, r + 1, 2, "Average move, Phase 1/2/3 milestones", font=BOLD)
put(ws, r + 1, 5, f"=ROUND(AVERAGE(E7:E{r - 1}),0)", font=BOLD,
    fmt="+0;-0;0", align="center")

# ===================================================== 2. COMPRESSION
ws = wb.create_sheet("Compression")
titleblock(ws, "Compression - windows that shrank between the two contracts",
           "Days are calendar days between the two milestones in each "
           "contract. A shorter window means the same work in less time.",
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
titleblock(ws, "Trade Stacking - how much runs at the same time",
           "Electrical rooms open simultaneously, per the 10/07 proposed "
           "schedule.", [16, 10, 13, 13, 40, 14, 14])
MONB = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def pdb(s):
    s = s.replace(" A", "").strip()
    d, m, y = s.split("-")
    return dt.date(2000 + int(y), MONB[m], int(d))


rooms = [(nm, dur, pdb(s), pdb(f)) for nm, dur, s, f, pg in BANDS
         if nm.startswith(("ER 1", "ER 2", "ERR 1", "ERR 2"))]
header(ws, 6, ["Month", "Rooms open", "", "", "Electrical room", "Start",
               "Finish"])
d = dt.date(2027, 1, 15)
r = 7
while d < dt.date(2028, 1, 15):
    n = sum(1 for _, _, s, f in rooms if s <= d <= f)
    fill = BADFILL if n >= 20 else (WARNFILL if n >= 12 else None)
    put(ws, r, 1, d.strftime("%b-%y"), fill=fill, align="center")
    put(ws, r, 2, n, fill=fill, align="center", font=BOLD)
    put(ws, r, 3, "#" * n, fill=fill, font=Font(name="Consolas", size=10))
    d = (d.replace(day=28) + dt.timedelta(days=5)).replace(day=15)
    r += 1
r2 = 7
for nm, dur, s, f in sorted(rooms, key=lambda x: x[2]):
    put(ws, r2, 5, nm)
    put(ws, r2, 6, s, fmt=DATEFMT, align="center")
    put(ws, r2, 7, f, fmt=DATEFMT, align="center")
    r2 += 1
rr = max(r, r2) + 2
put(ws, rr, 1, "PEAK: all 24 electrical rooms across the three datacenters "
               "are open at once on 15-Apr-27.", font=BOLD)
put(ws, rr + 1, 1, "The three buildings' electrical fit-outs are not "
                   "staggered - they overlap, so the peak cannot be served "
                   "by moving crews between phases.", font=BODY)
rr += 3
put(ws, rr, 1, "COMMISSIONING / CLOSEOUT OVERLAP", font=BOLD)
rr += 1
header(ws, rr, ["Phase", "Days", "Start", "Finish", "", "", ""])
rr += 1
for nm, dur, s, f, pg in BANDS:
    if "Commissioning / Closeout" in nm:
        put(ws, rr, 1, nm.replace("Commissioning / Closeout ", ""),
            align="center")
        put(ws, rr, 2, dur, align="center")
        put(ws, rr, 3, pdb(s), fmt=DATEFMT, align="center")
        put(ws, rr, 4, pdb(f), fmt=DATEFMT, align="center")
        rr += 1
put(ws, rr, 1, "All three phases run together 23-Aug-27 to 20-Dec-27 = "
               "119 days.", font=BOLD)
rr += 2
put(ws, rr, 1, "ACTIVITIES HOLDER HAS ALREADY PUT ON A SIX-DAY WEEK",
    font=BOLD)
rr += 1
header(ws, rr, ["Activity", "Days", "Start", "Finish", "", "", ""])
rr += 1
SIX = ["Long Lead Equipment Delivery", "Roof Mechanical/Electrical "
       "Installation", "Equipment Set", "Admin Space", "Mechanical Platform",
       "Roofing System Installation", "2nd Floor Office Build-Out",
       "1st Floor Admin Build-Out"]
for nm, dur, s, f, pg in BANDS:
    if nm in SIX:
        put(ws, rr, 1, nm, fill=WARNFILL)
        put(ws, rr, 2, dur, align="center", fill=WARNFILL)
        put(ws, rr, 3, pdb(s), fmt=DATEFMT, align="center", fill=WARNFILL)
        put(ws, rr, 4, pdb(f), fmt=DATEFMT, align="center", fill=WARNFILL)
        rr += 1
put(ws, rr, 1, "Each of these has a duration matching a six-day week to "
               "within 0.5% - premium time already built into the proposal, "
               "not priced.", font=SMALL)

# ===================================================== 4. LABOR CURVE
ws = wb.create_sheet("Labor Curve")
titleblock(ws, "Labour Curve - baseline (02/04) vs projected (10/07)",
           "Weekly, week ending Sunday. Hours = crew x 50, matching the JER "
           "labour report.  BASELINE 533,400 h planned at contract.  "
           "PROJECTED 605,707 h per JER as of 08-Nov-26.",
           [13, 13, 14, 13, 14, 13, 15, 15])
alert(ws, 'ALERT - PROJECTION MAY BE SHORT BY 50,604 h.  The 10/07 carries 122,911 h of scope with no counterpart in the 02/04 (galleries + HV/HVR). JER has booked only 72,307 h of growth. Assumed here that the 533,400 baseline did NOT include this scope - CONFIRM WITH FIELD.', span=8)
header(ws, 6, ["Week Ending", "Baseline Labor", "Baseline Hours",
               "Proposed Labor", "Proposed Hours", "Actual Hours",
               "Cum Baseline", "Cum Proposed"])
r = 7
first = r
for w in CURVE["weeks"]:
    wd = dt.date.fromisoformat(w["w"])
    put(ws, r, 1, wd, fmt=DATEFMT, align="center")
    put(ws, r, 2, f"=ROUND(C{r}/50,0)", align="center")
    put(ws, r, 3, round(w["base"], 0), fmt="#,##0", align="right")
    put(ws, r, 4, f"=ROUND(E{r}/50,0)", align="center")
    put(ws, r, 5, round(w["cur"], 0), fmt="#,##0", align="right")
    put(ws, r, 6, round(w["actual"], 0) if w["actual"] else None,
        fmt="#,##0", align="right")
    put(ws, r, 7, f"=SUM($C${first}:C{r})", fmt="#,##0", align="right")
    put(ws, r, 8, f"=SUM($E${first}:E{r})", fmt="#,##0", align="right")
    r += 1
last = r - 1
put(ws, r, 1, "TOTAL", font=BOLD)
put(ws, r, 3, f"=SUM(C{first}:C{last})", font=BOLD, fmt="#,##0",
    align="right")
put(ws, r, 5, f"=SUM(E{first}:E{last})", font=BOLD, fmt="#,##0",
    align="right")
put(ws, r, 6, f"=SUM(F{first}:F{last})", font=BOLD, fmt="#,##0",
    align="right")
put(ws, r + 1, 1, "PEAK", font=BOLD)
put(ws, r + 1, 2, f"=ROUND(MAX(C{first}:C{last})/50,0)", font=BOLD,
    align="center")
put(ws, r + 1, 3, f"=MAX(C{first}:C{last})", font=BOLD, fmt="#,##0",
    align="right")
put(ws, r + 1, 4, f"=ROUND(MAX(E{first}:E{last})/50,0)", font=BOLD,
    align="center")
put(ws, r + 1, 5, f"=MAX(E{first}:E{last})", font=BOLD, fmt="#,##0",
    align="right")

ch = BarChart()
ch.type = "col"
ch.style = 10
ch.grouping = "clustered"
ch.overlap = -10
ch.gapWidth = 12                       # thick bars, P6 usage-profile look
ch.title = "MDC-3 Labour Curve - Baseline (02/04) vs Proposed (10/07)"
ch.y_axis.title = "Hours per week"
ch.x_axis.title = "Week ending"
ch.height = 12
ch.width = 34
ch.roundedCorners = False
ch.y_axis.delete = False
ch.x_axis.delete = False
data = Reference(ws, min_col=3, max_col=3, min_row=6, max_row=last)
data2 = Reference(ws, min_col=5, max_col=5, min_row=6, max_row=last)
cats = Reference(ws, min_col=1, min_row=first, max_row=last)
ch.add_data(data, titles_from_data=True)
ch.add_data(data2, titles_from_data=True)
ch.set_categories(cats)
ch.series[0].graphicalProperties.solidFill = "9DC3E6"
ch.series[0].graphicalProperties.line.noFill = True
ch.series[1].graphicalProperties.solidFill = "C00000"
ch.series[1].graphicalProperties.line.noFill = True
if ch.legend:
    ch.legend.overlay = False
    ch.legend.position = "t"
if ch.title:
    ch.title.overlay = False

ln = LineChart()
cum = Reference(ws, min_col=7, max_col=8, min_row=6, max_row=last)
ln.add_data(cum, titles_from_data=True)
ln.y_axis.axId = 200
ln.y_axis.title = "Cumulative hours"
ln.y_axis.delete = False
ln.series[0].graphicalProperties.line.solidFill = "2E75B6"
ln.series[0].graphicalProperties.line.width = 20000
ln.series[1].graphicalProperties.line.solidFill = "843C0C"
ln.series[1].graphicalProperties.line.width = 20000
for s in ln.series:
    s.smooth = False
ch.y_axis.crosses = "autoZero"
ch += ln
ws.add_chart(ch, "J6")

# ===================================================== 5. CROSSWALK
ws = wb.create_sheet("Scope Crosswalk")
titleblock(ws, "Scope Crosswalk - how hours were placed in each contract",
           "THIS IS THE AUDIT TAB. Every hour in both curves comes from a "
           "row here. Check the mapping before trusting the curves.",
           [28, 12, 9, 13, 13, 38, 13, 13, 14])
alert(ws, 'ALERT - PROJECTION MAY BE SHORT BY 50,604 h.  The 10/07 carries 122,911 h of scope with no counterpart in the 02/04 (galleries + HV/HVR). JER has booked only 72,307 h of growth. Assumed here that the 533,400 baseline did NOT include this scope - CONFIRM WITH FIELD.', span=9)
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
alert(ws, 'ALERT - PROJECTION MAY BE SHORT BY 50,604 h.  The 10/07 carries 122,911 h of scope with no counterpart in the 02/04 (galleries + HV/HVR). JER has booked only 72,307 h of growth. Assumed here that the 533,400 baseline did NOT include this scope - CONFIRM WITH FIELD.', span=4)
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
