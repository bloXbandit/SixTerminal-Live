"""The resource-assignment worksheet: one row per assignment, ready to paste.

Four tabs.

  1 Assignments     one row per resource line item, sorted by crew count then
                    activity, so each P6 filter group is one contiguous block.
                    Budgeted Units is already divided out -- activity hours
                    over crew -- so nothing is calculated in P6.
  2 By crew group   the 21 filter passes, largest first, as a work list.
  3 Inferred counts the 87 activities that had no count, what was inferred and
                    on what evidence, so any of them can be overridden before
                    the assignments are made.
  4 Payroll records the 43 weekly records holding 259,021 actual hours. They
                    carry no crew count and no duration, and they are 76% of
                    every actual hour in the file, so the usage chart stays
                    flat without them.
"""
import collections
import datetime as dt
import re
import statistics
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


def ne(a):
    v = (a.udfs or {}).get("Number of Electricians")
    try:
        n = int(float(str(v).strip()))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


def fam(n):
    w = norm(n).split()
    return " ".join(w[:2]) if len(w) >= 2 else norm(n)


def dd(a):
    return max((a.planned_duration or 8) / 8, 1)


def h(a):
    return a.planned_labor_units or 0


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


work = [a for a in p.activities
        if not str(a.activity_id).startswith("MDC1.LAB")]
lab = [a for a in p.activities if str(a.activity_id).startswith("MDC1.LAB")]
have = [a for a in work if ne(a) and h(a) > 0]
need = [a for a in work if not ne(a) and h(a) > 0]
rate = statistics.median([h(a) / (ne(a) * dd(a)) for a in have])

byname = collections.defaultdict(list)
byfam = collections.defaultdict(list)
for a in have:
    byname[norm(a.name)].append(a)
    byfam[fam(a.name)].append(a)

inferred = {}
for a in need:
    peers, how = byname.get(norm(a.name)), "exact name match"
    if not peers:
        peers, how = byfam.get(fam(a.name)), "similar work"
    if peers:
        pc = statistics.median([ne(x) for x in peers])
        ph = statistics.median([h(x) for x in peers])
        n = max(1, round(pc * (h(a) / ph))) if ph else int(pc)
        ev = (f"{len(peers)} peer activities, median crew {pc:,.0f} "
              f"on {ph:,.0f} h")
    else:
        n = max(1, round(h(a) / (dd(a) * rate)))
        how = "project trend"
        ev = f"{h(a):,.0f} h over {dd(a):,.0f} d at {rate:,.2f} h/elec/day"
    inferred[a.uid] = (n, how, ev)

rows = []
for a in have + need:
    n = ne(a) or inferred[a.uid][0]
    src = "your UDF" if ne(a) else f"inferred - {inferred[a.uid][1]}"
    per = round(h(a) / n, 2)
    for i in range(1, n + 1):
        rows.append((n, str(a.activity_id), str(a.name), path(a.wbs_uid),
                     a.status, round(dd(a)), round(h(a), 1), i, per, src))
rows.sort(key=lambda r: (r[0], r[1], r[7]))
print(f"assignment rows: {len(rows):,}   "
      f"activities: {len(have) + len(need):,}   "
      f"hours: {sum(h(a) for a in have + need):,.0f}")

HEAD = Font(bold=True, color="FFFFFF", size=11)
FILL = PatternFill("solid", fgColor="1F3864")
GRN = PatternFill("solid", fgColor="C6EFCE")
AMB = PatternFill("solid", fgColor="FFF2CC")
SUB = Font(bold=True, size=11)
thin = Side(style="thin", color="BFBFBF")
BD = Border(left=thin, right=thin, top=thin, bottom=thin)
wb = Workbook()


def head(ws, cols, widths, green=()):
    ws.append(cols)
    for i, c in enumerate(ws[1], 1):
        c.font, c.fill = HEAD, (GRN if cols[i - 1] in green else FILL)
        if cols[i - 1] in green:
            c.font = Font(bold=True, size=11)
        c.alignment = Alignment(wrap_text=True, vertical="center",
                                horizontal="center")
        c.border = BD
    ws.row_dimensions[1].height = 34
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


ws = wb.create_sheet("1 Assignments")
C1 = ("Crew group", "Activity ID", "Activity", "WBS", "Status",
      "Duration (d)", "Activity hours", "Line item #", "BUDGETED UNITS",
      "Crew count from")
head(ws, C1, (11, 24, 46, 46, 13, 11, 13, 11, 15, 26),
     green=("BUDGETED UNITS",))
for r in rows:
    ws.append(r)
    ws.cell(row=ws.max_row, column=9).fill = GRN
ws.freeze_panes = "C2"
ws.auto_filter.ref = f"A1:{get_column_letter(len(C1))}{ws.max_row}"

ws = wb.create_sheet("2 By crew group")
C2 = ("Crew group", "Activities", "Assignments", "Hours",
      "Budgeted Units range", "Order to do them")
head(ws, C2, (11, 12, 13, 13, 24, 16))
g = collections.defaultdict(list)
for r in rows:
    g[r[0]].append(r)
order = sorted(g, key=lambda k: -len(g[k]))
for k in sorted(g):
    v = g[k]
    acts = {x[1] for x in v}
    pers = [x[8] for x in v]
    ws.append((k, len(acts), len(v), round(sum(x[6] for x in v) / k, 1),
               f"{min(pers):,.1f} - {max(pers):,.1f}",
               order.index(k) + 1))
ws.append(("TOTAL", len({r[1] for r in rows}), len(rows),
           round(sum(h(a) for a in have + need), 1), "", ""))
for c in ws[ws.max_row]:
    c.font = SUB

ws = wb.create_sheet("3 Inferred counts")
C3 = ("Activity ID", "Activity", "WBS", "Hours", "Duration (d)",
      "INFERRED CREW", "How", "Evidence", "h/elec/day it implies",
      "Override")
head(ws, C3, (24, 44, 40, 10, 11, 13, 18, 44, 14, 11),
     green=("INFERRED CREW", "Override"))
for a in sorted(need, key=lambda x: str(x.activity_id)):
    n, how, ev = inferred[a.uid]
    ws.append((str(a.activity_id), str(a.name), path(a.wbs_uid),
               round(h(a), 1), round(dd(a)), n, how, ev,
               round(h(a) / (n * dd(a)), 1), ""))
    for j in (6, 10):
        ws.cell(row=ws.max_row, column=j).fill = AMB
ws.freeze_panes = "B2"

ws = wb.create_sheet("4 Payroll records")
C4 = ("Activity ID", "Week ending", "ACTUAL hours", "Suggested line items",
      "Budgeted Units each", "Note")
head(ws, C4, (26, 14, 14, 20, 18, 56))
for a in sorted(lab, key=lambda x: str(D(x.actual_finish) or "")):
    hh = a.actual_labor_units or 0
    crew = max(1, round(hh / 50))
    ws.append((str(a.activity_id), D(a.actual_finish), round(hh, 1), crew,
               round(hh / crew, 2),
               "Headcount at 50 h/week, which is what the onsite record runs"))
    for j in (4, 5):
        ws.cell(row=ws.max_row, column=j).fill = GRN
for rr in ws.iter_rows(min_row=2, min_col=2, max_col=2):
    for c in rr:
        c.number_format = "mm/dd/yyyy"
ws.freeze_panes = "B2"

del wb["Sheet"]
out = f"{SCR}/MDC1_Resource_Assignments.xlsx"
wb.save(out)
print(f"wrote {out}")
print(f"\ntab 1: {len(rows):,} assignment rows")
print(f"tab 2: {len(g)} crew groups")
print(f"tab 3: {len(need)} inferred counts")
print(f"tab 4: {len(lab)} payroll records, "
      f"{sum(a.actual_labor_units or 0 for a in lab):,.0f} actual hours")
print(f"\ngrand total units on tab 1: "
      f"{sum(r[8] for r in rows):,.0f} h  (file budget "
      f"{sum(h(a) for a in work):,.0f} h)")
