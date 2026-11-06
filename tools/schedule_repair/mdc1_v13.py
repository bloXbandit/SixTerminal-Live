"""MDC-1 v13: data date to 10/31/2026, plus the catch-up and lookahead workbook.

Built on MDC1_logic_repaired_v12.xml -- the file we repaired earlier, which is
already acyclic (3,775 relationships, no circular logic, no dangling ties), so
nothing needs cutting here.

THE DATA DATE moves 09/30/2026 -> 10/31/2026, as asked, not to 11/04. The file
carries no actual dates after 09/30, so October is unstatused: moving the date
does not invent progress, it exposes the month that needs a status call.

WHAT THE CATCH-UP ACTUALLY IS. The schedule was last calculated around
06/30/2026 -- 169 not-started activities still sit on that exact date, 97 on
07/22, 92 on 07/16. So 1,451 JER activities read as overdue mostly because the
dates are four months stale, and a catch-up list that long is not a field
document.

What the foreman can answer is the work that is READY: nothing incomplete in
front of it. That is 137 activities, plus the 25 in-progress ones already past
their planned finish. The other 1,314 are waiting on something that has not
happened, so there is no progress to report on them -- they need F9, not a
status call. The sheets are split that way, and a fourth lists what is holding
the blocked work back, worst first.

JER scope only: anything marked *WBO or **WBO is left out. Dates are not
progressed. The four red columns are empty for the foreman.
"""
import collections
import datetime as dt
import itertools
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SRC = sys.argv[1]
SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
NEW_DD = dt.date(2026, 10, 31)
p = load_xml(SRC)
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
OLD_DD = dt.date.fromisoformat(str(p.data_date)[:10])


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


print("=" * 78)
print("DATA DATE")
print("=" * 78)
asd = [D(a.actual_start) for a in p.activities if D(a.actual_start)]
af = [D(a.actual_finish) for a in p.activities if D(a.actual_finish)]
print(f"moving {OLD_DD} -> {NEW_DD}")
print(f"latest actual start {max(asd)}   latest actual finish {max(af)}")
print(f"actuals dated after the new data date: "
      f"starts {sum(1 for x in asd if x > NEW_DD)}  "
      f"finishes {sum(1 for x in af if x > NEW_DD)}  (nothing to hold back)")
p.data_date = NEW_DD.isoformat()
dd = NEW_DD
HORIZON = dd + dt.timedelta(days=14)

WBO = re.compile(r"\*+\s*WBO|by others", re.I)


def jer(a):
    return not WBO.search(str(a.name))


def scope(a):
    pth = path(a.wbs_uid)
    aid = str(a.activity_id or "")
    if "Labour Record" in pth or aid.startswith("MDC1.LAB"):
        return "Labour record"
    if "Milestones" in pth or aid.startswith("MDC1.MIL"):
        return "Milestones"
    g = re.search(r"/ ((?:ER|MV|HV|HVR|ERR|ELEC) ?R?\d{3})$", pth)
    if g:
        return f"{g.group(1)[:2].strip()} rooms"
    g = re.search(r"/ (Lineup \d+)$", pth)
    if g:
        return "Chiller lineups"
    for key, lab in (("Generator", "Generator rooms"),
                     ("Data Hall", "Data halls & galleries"),
                     ("Gallery", "Data halls & galleries"),
                     ("Electrical Room", "Electrical rooms"),
                     ("Admin", "Admin space"), ("CUP", "CUP"),
                     ("Roof", "Roof"), ("Precast", "Precast"),
                     ("Foundation", "Foundations"),
                     ("Underground", "Underground MEP"),
                     ("Site", "Site utilities"),
                     ("Procurement", "Procurement"),
                     ("Structure", "Structure")):
        if key.lower() in pth.lower():
            return lab
    parts = pth.split(" / ")
    return parts[1] if len(parts) > 1 else (pth or "Other")


pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)


def is_ready(a):
    return not [1 for r in pred[a.uid] if r.predecessor_uid in byu
                and byu[r.predecessor_uid].status != "Completed"]


def open_preds(u):
    return [byu[r.predecessor_uid] for r in pred[u]
            if r.predecessor_uid in byu
            and byu[r.predecessor_uid].status != "Completed"]


def heads_of(u, seen=None):
    seen = seen if seen is not None else set()
    if u in seen:
        return set()
    seen.add(u)
    ps = open_preds(u)
    if not ps:
        return {u}
    out = set()
    for q in ps:
        out |= heads_of(q.uid, seen)
    return out


def row(a, why):
    s, f = D(a.planned_start), D(a.planned_finish)
    ps = [byu[r.predecessor_uid] for r in pred[a.uid] if r.predecessor_uid in byu]
    waiting = [x for x in ps if x.status != "Completed"]
    blk = ", ".join(f"{x.activity_id} {x.name}" for x in waiting) or "-"
    hd = sorted(byu[h].activity_id for h in heads_of(a.uid)) if waiting else []
    if a.status == "In Progress":
        late = max(0, (dd - f).days) if f else 0
    else:
        late = max(0, (dd - s).days) if s else 0
    return (scope(a), str(a.activity_id), str(a.name), s, f,
            round((a.planned_duration or 0) / 8), late, len(ps), len(waiting),
            blk, ", ".join(hd) or "start here", why, path(a.wbs_uid), a.uid)


ready, blocked, look = [], [], []
blockers = collections.Counter()
for a in p.activities:
    if not jer(a) or a.status == "Completed":
        continue
    s, f = D(a.planned_start), D(a.planned_finish)
    if a.status == "Not Started":
        if s and s < dd:
            if is_ready(a):
                ready.append(row(a, "ready to work, start date passed"))
            else:
                blocked.append(row(a, "overdue but waiting on something"))
                # count blockers off the activity objects. Re-parsing the
                # comma-joined "Blocked by" text split names like
                # "Set Equipment (Chillers, Pumps and Heat Exchanger)" in two
                # and invented a blocker holding 19 activities.
                for q in open_preds(a.uid):
                    blockers[str(q.activity_id)] += 1
        elif s and s <= HORIZON:
            look.append(row(a, "starts in the window"))
    elif a.status == "In Progress":
        if f and f < dd:
            ready.append(row(a, "running, finish date passed"))
        elif f and f <= HORIZON:
            look.append(row(a, "running, due in the window"))
for lst in (ready, blocked, look):
    lst.sort(key=lambda r: (r[0], r[3] or dt.date(2030, 1, 1), r[1]))
releases = collections.Counter()
for r in ready + blocked + look:
    for h in r[10].split(", "):
        releases[h] += 1


print("\n" + "=" * 78)
print(f"MDC-1   data date {dd:%m/%d/%Y}   lookahead to {HORIZON:%m/%d/%Y}"
      f"   (JER scope only)")
print("=" * 78)
print(f"ready to work now {len(ready)}   blocked {len(blocked)}   "
      f"lookahead {len(look)}")
print(f"\nTHE DATES ARE STALE: last calculated around 06/30/2026, so "
      f"{len(ready) + len(blocked)} JER activities read as overdue.")
print(f"Only {len(ready)} are actually ready to work. The other {len(blocked)} "
      f"are waiting on something,\nso there is nothing to status on them "
      f"until the schedule is recalculated.")
for lst, title in ((ready, "READY TO WORK NOW"), (look, "TWO-WEEK LOOKAHEAD")):
    print("\n" + "=" * 76)
    print(f"{title}  ({len(lst)})")
    print("=" * 76)
    for sc, rows in itertools.groupby(lst, key=lambda r: r[0]):
        rows = list(rows)
        print(f"\n  {sc}  ({len(rows)})")
        for r in rows[:7]:
            print(f"     {r[1]:<26}{r[2][:34]:<36}{r[3]}{r[5]:>4}d"
                  f"{(str(r[6]) + 'd late') if r[6] else '':>9}")
        if len(rows) > 7:
            print(f"     ... and {len(rows) - 7} more")
print("\n" + "=" * 76)
print(f"WHAT IS HOLDING THE BLOCKED WORK BACK  (top 12 of {len(blockers)})")
print("=" * 76)
_by = {str(x.activity_id): x for x in p.activities}
for k, v in blockers.most_common(12):
    a = _by.get(k)
    print(f"   holds {v:>4}   {k:<26}{str(a.name)[:38] if a is not None else '':<40}"
          f"{a.status if a is not None else '':<12}"
          f"{D(a.planned_finish) if a is not None else ''}")

# ---- workbook --------------------------------------------------------
COLS = ("Scope", "Activity ID", "Activity", "Planned start", "Planned finish",
        "Days", "Days late", "Predecessors", "Still open", "Blocked by",
        "Status this head", "Why it is here", "Complete?", "Actual start",
        "Actual finish", "Releases", "WBS", "Update - Notes")
WID = (26, 26, 46, 13, 13, 6, 9, 12, 10, 50, 24, 28, 11, 13, 13, 9, 50, 36)
ENTRY = ("Complete?", "Actual start", "Actual finish", "Update - Notes")
HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
SUB = Font(bold=True)
SUBF = PatternFill("solid", fgColor="DDEBF7")
wb = Workbook()
for lst, title in ((ready, "Catch-up - ready now"),
                   (look, "2-week lookahead"),
                   (blocked, "Catch-up - blocked")):
    ws = wb.create_sheet(title)
    ws.append(COLS)
    for i, c in enumerate(ws[1], 1):
        c.font = HEAD
        c.fill = REDH if COLS[i - 1] in ENTRY else FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ecols = [COLS.index(k) + 1 for k in ENTRY]
    last = None
    for sc, aid, nm, s, f, d, late, np_, nw, blk, hd, why, pth, _u in lst:
        if sc != last:
            ws.append((f"-- {sc} --",))
            for c in ws[ws.max_row]:
                c.font, c.fill = SUB, SUBF
            last = sc
        ws.append((sc, aid, nm, s, f, d, late, np_, nw, blk, hd, why,
                   "", "", "", releases.get(aid, 0) if not nw else None,
                   pth, ""))
        for j in ecols:
            ws.cell(row=ws.max_row, column=j).fill = REDC
    for i, w in enumerate(WID, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for j in (4, 5, COLS.index("Actual start") + 1,
              COLS.index("Actual finish") + 1):
        for rr in ws.iter_rows(min_row=2, min_col=j, max_col=j):
            for c in rr:
                c.number_format = "mm/dd/yyyy"
    ws.freeze_panes = "D2"
ws = wb.create_sheet("What is blocking")
ws.append(("Blocking activity", "Activities it holds up", "Status",
           "Planned start", "Planned finish", "WBS"))
for c in ws[1]:
    c.font, c.fill = HEAD, FILL
    c.alignment = Alignment(wrap_text=True, vertical="top")
ours = {str(a.activity_id): a for a in p.activities}
for k, v in blockers.most_common(150):
    a = ours.get(k)
    ws.append((f"{k} {a.name}" if a is not None else k, v,
               a.status if a is not None else "",
               D(a.planned_start) if a is not None else None,
               D(a.planned_finish) if a is not None else None,
               path(a.wbs_uid) if a is not None else ""))
for i, w in enumerate((62, 12, 13, 13, 13, 50), 1):
    ws.column_dimensions[get_column_letter(i)].width = w
for rr in ws.iter_rows(min_row=2, min_col=4, max_col=5):
    for c in rr:
        c.number_format = "mm/dd/yyyy"
ws.freeze_panes = "B2"
del wb["Sheet"]
out = f"{SCR}/MDC1_Catchup_and_Lookahead_v13.xlsx"
wb.save(out)
xml = f"{SCR}/MDC1_logic_repaired_v13.xml"
write_p6_xml(p, xml, include_resources=False, include_udfs=True)
mx = max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()),
         default=0)
print(f"\nwrote {out}")
print(f"wrote {xml}")
print(f"activities {len(p.activities)}  relationships {len(p.relations)}  "
      f"longest UDF {mx}")
