"""MDC-3 catch-up and two-week lookahead, rebuilt from v22.

Same three sheets as the 10/28 round, with the completed work gone -- the
field update closed 53 of the original 123, so the catch-up list is now what
is genuinely still open.

Dates are NOT progressed. Everything here is the file as it stands at the
10/24 data date: no forward pass, no F9, no re-forecast. So an activity shows
the date it currently carries, which for a late one is still its original
planned start.

The Complete? / Date finished / Notes columns are left blank again so the
sheet goes straight back out to the field and comes back as the next update.
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

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")

p = load_xml(f"{SCR}/MDC3_logic_repaired_v22.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
dd = dt.date.fromisoformat(str(p.data_date)[:10])
HORIZON = dd + dt.timedelta(days=14)


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)

WBO = re.compile(r"\*+\s*WBO|by others", re.I)
UG = re.compile(r"underground|in-slab|in slab|stub ?up|\bHES\b", re.I)


def scope(a):
    pth = path(a.wbs_uid).replace("MDC-3 (Exhibit S) / ", "")
    nm, aid = str(a.name), str(a.activity_id or "")
    if "Labour Record" in pth:
        return "Labour record"
    if WBO.search(nm):
        return "Work by others"
    if ".ST.PC." in aid:
        return "Precast"
    if ".ST.SHF." in aid:
        return "Shallow foundations"
    if ".STR.DP." in aid:
        return "Deep foundations"
    # underground gets its own label wherever it sits -- it was hidden inside
    # the slab-pour label last round, which understated it badly
    if ".STR.UDG." in aid or (UG.search(nm) and ".ST.L" in aid):
        m = re.match(r"MDC3\.ST\.(L\d)\.", aid)
        return f"Underground MEP - {m.group(1).replace('L', 'Level ')}" if m \
            else "Underground MEP - building"
    m = re.match(r"MDC3\.ST\.(L\d)\.", aid)
    if m:
        return f"Slab pours - {m.group(1).replace('L', 'Level ')}"
    if ".EXT.SUT." in aid or ".UTLI." in aid or "Site Utilities" in pth:
        return "Site utilities"
    if ".ROOF." in aid or re.search(r"Roof", pth):
        return "Roof"
    if re.search(r"/ ((?:ER|MV|HV|HVR) R?\d{3})$", path(a.wbs_uid)):
        g = re.search(r"/ ((?:ER|MV|HV|HVR) R?\d{3})$", path(a.wbs_uid))
        return f"{g.group(1)[:2]} rooms"
    if "LLE" in pth or ".SK.AS." in aid or ".XFMR." in aid:
        return "Long lead / skids"
    if "Data Hall" in pth or "Gallery" in pth:
        return "Data halls & galleries"
    if "Generator" in pth:
        return "Generator rooms"
    if "Admin" in pth:
        return "Admin space"
    if "Milestone" in pth:
        return "Milestones"
    return pth.split(" / ")[0] if pth else "Other"


def open_preds(u):
    return [byu[r.predecessor_uid] for r in pred[u]
            if r.predecessor_uid in byu
            and byu[r.predecessor_uid].status != "Completed"]


def heads_of(u, seen=None):
    seen = seen or set()
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


catch, look, prog = [], [], []
for a in p.activities:
    if a.status == "Completed":
        continue
    s = D(a.planned_start)
    if not s:
        continue
    ps = [byu[r.predecessor_uid] for r in pred[a.uid] if r.predecessor_uid in byu]
    waiting = [x for x in ps if x.status != "Completed"]
    blk = ", ".join(f"{x.activity_id} {x.name}" for x in waiting) or "-"
    hd = sorted(byu[h].activity_id for h in heads_of(a.uid)) if waiting else []
    row = (scope(a), a.activity_id, str(a.name), s, D(a.planned_finish),
           (a.planned_duration or 0) / 8, len(ps), len(waiting), blk,
           ", ".join(hd) or "start here", "", "", "",
           path(a.wbs_uid).replace("MDC-3 (Exhibit S) / ", ""))
    if a.status == "In Progress":
        f = D(a.planned_finish)
        if f and f <= HORIZON:
            prog.append(row)
        continue
    if s < dd:
        catch.append(row)
    elif s <= HORIZON:
        look.append(row)

for lst in (catch, look, prog):
    lst.sort(key=lambda r: (r[0], r[3], r[1]))
heads = [r for r in catch + look if not r[7]]
heads.sort(key=lambda r: (r[3], r[0], r[1]))
releases = collections.Counter()
for r in catch + look:
    for h in r[9].split(", "):
        releases[h] += 1

print(f"MDC-3 v22   data date {dd:%m/%d/%Y}   lookahead to {HORIZON:%m/%d/%Y}")
print(f"catch-up {len(catch)}   lookahead {len(look)}   "
      f"in progress due in window {len(prog)}   start-here {len(heads)}\n")
for lst, title in ((catch, "CATCH-UP"), (look, "TWO-WEEK LOOKAHEAD")):
    print("=" * 76)
    print(f"{title}  ({len(lst)})")
    print("=" * 76)
    for sc, rows in itertools.groupby(lst, key=lambda r: r[0]):
        rows = list(rows)
        print(f"\n  {sc}  ({len(rows)})")
        for _, aid, nm, s, f, d, np_, nw, blk, hd, _c, _dt, _n, pth in rows[:9]:
            late = (dd - s).days
            print(f"     {aid:<24}{nm[:38]:<40}{s:%m/%d}{d:>4.0f}d"
                  f"{(str(late) + 'd late') if late > 0 else '':>10}  "
                  f"{'behind ' + blk.split()[0] if nw else 'START HERE'}")
        if len(rows) > 9:
            print(f"     ... and {len(rows) - 9} more")
    print()
print("=" * 76)
print(f"START HERE  ({len(heads)})")
print("=" * 76)
for sc, aid, nm, s, f, d, np_, nw, blk, hd, _c, _dt, _n, pth in heads:
    late = (dd - s).days
    print(f"  {sc:<28}{aid:<24}{nm[:34]:<36}{s:%m/%d}"
          f"{(str(late) + 'd late') if late > 0 else 'ahead':>10}"
          f"   releases {releases.get(aid, 0):>3}")

# ---- workbook ---------------------------------------------------------
HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
ASK = PatternFill("solid", fgColor="FFF2CC")
SUB = Font(bold=True)
SUBF = PatternFill("solid", fgColor="DDEBF7")
# the three amber columns are for the field to fill. Actual start AND actual
# finish, rather than one date -- last round the single column had to be read
# as a finish and the start worked back from the duration, which is a guess
# wherever an activity ran longer or shorter than planned.
COLS = ("Scope", "Activity ID", "Activity", "Planned start", "Planned finish",
        "Days", "Days late", "Predecessors", "Still open", "Blocked by",
        "Status this head", "Complete?", "Actual start", "Actual finish",
        "Update - Notes", "Releases", "WBS")
WID = (26, 24, 44, 12, 12, 6, 9, 12, 10, 50, 22, 11, 13, 13, 34, 9, 50)
ENTRY = ("Complete?", "Actual start", "Actual finish", "Update - Notes")
wb = Workbook()
for lst, title in ((catch, "Catch-up"), (look, "2-week lookahead"),
                   (prog, "In progress - due soon"), (heads, "Start here")):
    ws = wb.create_sheet(title)
    ws.append(COLS)
    for i, c in enumerate(ws[1], 1):
        c.font, c.fill = HEAD, FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if COLS[i - 1] in ENTRY:
            c.fill = PatternFill("solid", fgColor="BF8F00")
    ecols = [COLS.index(k) + 1 for k in ENTRY]
    last = None
    for sc, aid, nm, s, f, d, np_, nw, blk, hd, c1, c2, c3, pth in lst:
        if sc != last:
            ws.append((f"-- {sc} --",))
            for c in ws[ws.max_row]:
                c.font, c.fill = SUB, SUBF
            last = sc
        ws.append((sc, aid, nm, s, f, d, max(0, (dd - s).days), np_, nw, blk,
                   hd, c1, c2, "", c3,
                   releases.get(aid, 0) if not nw else None, pth))
        for j in ecols:
            ws.cell(row=ws.max_row, column=j).fill = ASK
    for i, wd in enumerate(WID, 1):
        ws.column_dimensions[get_column_letter(i)].width = wd
    for r in ws.iter_rows(min_row=2, min_col=4, max_col=5):
        for c in r:
            c.number_format = "mm/dd/yyyy"
    for j in (COLS.index("Actual start") + 1, COLS.index("Actual finish") + 1):
        for r in ws.iter_rows(min_row=2, min_col=j, max_col=j):
            for c in r:
                c.number_format = "mm/dd/yyyy"
    ws.freeze_panes = "D2"
del wb["Sheet"]
out = f"{SCR}/MDC3_Catchup_and_Lookahead_v22.xlsx"
wb.save(out)
print(f"\nwrote {out}")
