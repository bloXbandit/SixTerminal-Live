"""MDC-3 catch-up and two-week lookahead, built on v29 (now retimed to Holder).

Window runs from the schedule's own data date, 10/24/2026, to 11/07/2026.

JER scope only: anything carrying the **WBO marker is left out, as are the
Pink, Orange and Yellow stages of the equipment tag gate, which are Holder's
and Cloud HQ's sign-offs. The Red Tag stays -- that one is JER's.

Dates are NOT progressed. Every row shows the date the file currently
carries. What has changed since the last round is that 1,508 activities were
retimed to Holder's 11/04 dates, which moved most work about six weeks later,
so far less of the job now reads as late. That is a real change in the
picture, not a filtering trick.

The four amber columns of the last round are red here and left empty for the
foreman: Complete?, Actual start, Actual finish, Update - Notes. Two date
columns rather than the single one that came back last time, because that one
had to be read as a finish with the start worked back from the duration,
which is a guess wherever an activity ran long or short.

Also fixes one activity: MDC3.ST.ELEV.1060 was statused In Progress from
Holder's export with an actual start of 10/28/2026 while its planned start
still read 2027-02-24, so it claimed to have begun four months before it was
due to. Its planned dates now follow the actual.
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

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC3_logic_repaired_v29.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
dd = dt.date.fromisoformat(str(p.data_date)[:10])
HORIZON = dd + dt.timedelta(days=14)


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


# --- the one date inconsistency, fixed before anything is measured --------
fixed = []
for a in p.activities:
    if a.status != "In Progress":
        continue
    asd, psd = D(a.actual_start), D(a.planned_start)
    if asd and psd and psd > asd:
        a.planned_start = asd.isoformat()
        pf = D(a.planned_finish)
        if pf and pf < asd:
            a.planned_finish = asd.isoformat()
        old = a.udfs.get("COMMENTS", "")
        note = (f"Planned start pulled back to the actual start {asd}; it "
                f"read {psd}, after the work had already begun.")
        # cap the COMBINED value, not just the fragment: P6 rejects the whole
        # file over 255 and the last round slipped back to 256 this way
        a.udfs["COMMENTS"] = (((old + "  ") if old else "") + note)[:248]
        fixed.append((str(a.activity_id), str(a.name)[:40], psd, asd))
print(f"date inconsistencies fixed: {len(fixed)}")
for i, n, psd, asd in fixed:
    print(f"   {i:<24}{n:<42}planned {psd} -> {asd}")

WBO = re.compile(r"\*+\s*WBO|by others", re.I)
NOTJER = re.compile(r"^(Pink|Orange|Yellow) Tag", re.I)


def jer(a):
    n = str(a.name)
    return not WBO.search(n) and not NOTJER.match(n)


UG = re.compile(r"underground|in-slab|in slab|stub ?up|\bHES\b", re.I)


def scope(a):
    pth = path(a.wbs_uid).replace("MDC-3 (Exhibit S) / ", "")
    nm, aid = str(a.name), str(a.activity_id or "")
    if "Labour Record" in pth:
        return "Labour record"
    if re.match(r"^Red Tag", nm):
        return "Equipment tag - JER sign-off"
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
        return f"Underground MEP - {m.group(1).replace('L', 'Level ')}" if m \
            else "Underground MEP - building"
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


pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)


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
    # an activity that has started is late against its FINISH, not its start.
    # Measuring from the start made long-lead deliveries that are due inside
    # the window read as 257 days late because they were ordered in February.
    if a.status == "In Progress":
        late = max(0, (dd - f).days) if f else 0
    else:
        late = max(0, (dd - s).days) if s else 0
    return (scope(a), str(a.activity_id), str(a.name), s, f,
            round((a.planned_duration or 0) / 8), late, len(ps), len(waiting),
            blk, ", ".join(hd) or "start here", why,
            path(a.wbs_uid).replace("MDC-3 (Exhibit S) / ", ""), a.uid)


catch, look = [], []
for a in p.activities:
    if not jer(a) or a.status == "Completed":
        continue
    s, f = D(a.planned_start), D(a.planned_finish)
    if a.status == "Not Started":
        if s and s < dd:
            catch.append(row(a, "start date passed, not started"))
        elif s and s <= HORIZON:
            look.append(row(a, "starts in the window"))
    elif a.status == "In Progress":
        if f and f < dd:
            catch.append(row(a, "running, finish date passed"))
        elif f and f <= HORIZON:
            look.append(row(a, "running, due in the window"))
for lst in (catch, look):
    lst.sort(key=lambda r: (r[0], r[3] or dt.date(2030, 1, 1), r[1]))
heads = [r for r in catch + look if not r[8]]
heads.sort(key=lambda r: (r[3] or dt.date(2030, 1, 1), r[0], r[1]))
releases = collections.Counter()
for r in catch + look:
    for h in r[10].split(", "):
        releases[h] += 1

print(f"\nMDC-3 v29  data date {dd:%m/%d/%Y}  lookahead to {HORIZON:%m/%d/%Y}"
      f"  (JER scope only)")
print(f"catch-up {len(catch)}   lookahead {len(look)}   start-here {len(heads)}")
for lst, title in ((catch, "CATCH-UP"), (look, "TWO-WEEK LOOKAHEAD")):
    print("\n" + "=" * 76)
    print(f"{title}  ({len(lst)})")
    print("=" * 76)
    for sc, rows in itertools.groupby(lst, key=lambda r: r[0]):
        rows = list(rows)
        print(f"\n  {sc}  ({len(rows)})")
        for r in rows[:8]:
            print(f"     {r[1]:<24}{r[2][:36]:<38}{r[3]}  {r[5]:>3}d"
                  f"{(str(r[6]) + 'd late') if r[6] else '':>9}  "
                  f"{'behind ' + r[9].split()[0] if r[8] else 'START HERE'}")
        if len(rows) > 8:
            print(f"     ... and {len(rows) - 8} more")

# ---- workbook --------------------------------------------------------
COLS = ("Scope", "Activity ID", "Activity", "Planned start", "Planned finish",
        "Days", "Days late", "Predecessors", "Still open", "Blocked by",
        "Status this head", "Why it is here", "Complete?", "Actual start",
        "Actual finish", "Releases", "WBS", "Update - Notes")
WID = (28, 24, 46, 13, 13, 6, 9, 12, 10, 50, 22, 28, 11, 13, 13, 9, 50, 36)
ENTRY = ("Complete?", "Actual start", "Actual finish", "Update - Notes")
HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
REDH = PatternFill("solid", fgColor="C00000")
REDC = PatternFill("solid", fgColor="FFC7CE")
SUB = Font(bold=True)
SUBF = PatternFill("solid", fgColor="DDEBF7")
wb = Workbook()
for lst, title in ((catch, "Catch-up"), (look, "2-week lookahead"),
                   (heads, "Start here")):
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
del wb["Sheet"]
out = f"{SCR}/MDC3_Catchup_and_Lookahead_v30.xlsx"
wb.save(out)
print(f"\nwrote {out}")
xml = f"{SCR}/MDC3_logic_repaired_v30.xml"
write_p6_xml(p, xml, include_resources=False, include_udfs=True)
print(f"wrote {xml}")
print(f"\nlongest UDF value: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
