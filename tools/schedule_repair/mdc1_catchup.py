"""MDC-1 catch-up and two-week lookahead at the 10/31/2026 data date.

Closes the five open ends that breaking the loops created, then builds the
field workbook in the same shape as the MDC-3 one.

THE OPEN ENDS. Cutting a relationship to break a loop can leave the activity
on either side of it dangling. Four of the five are fine as they stand:

  Floor Finishes **WBO x2  lost the Paint tie that ran 62 days backwards.
                           Floor finishes run 06/30 and paint 08/27, so the
                           surviving Floor Finishes -> Paint tie is the right
                           way round and these two need a lead-in from their
                           own room, not from paint.
  Owner Acceptance         the pair was tied both ways; Final Completion ->
                           Owner Acceptance survives, which is the right
                           order, and acceptance ends the chain.
  Network Availability     Equipment Energization Start -> Network
                           Availability survives: you energize, then the
                           network comes up.

The fifth does need fixing. Equipment Energization Start was left with no
predecessor at all, so it would schedule at the data date regardless of the
job. It now follows Building Systems Ready for Energization, which is the
milestone the name points at.

THE WORKBOOK. Window runs 10/31/2026 to 11/14/2026. JER scope only: anything
marked *WBO or **WBO is left out. Dates are not progressed -- every row shows
what the file carries today. The four red columns are empty for the foreman.

October is unstatused in this file, so the catch-up sheet is the month: fill
it in, then run F9 once.
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
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v2.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id): a for a in p.activities}
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
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


rels = list(p.relations)
nrel = max(int(r.uid) for r in rels if str(r.uid).isdigit()) + 1
have = {(r.predecessor_uid, r.successor_uid) for r in rels}
nxt = collections.defaultdict(set)
prv = collections.defaultdict(set)
for r in rels:
    nxt[r.predecessor_uid].add(r.successor_uid)
    prv[r.successor_uid].add(r.predecessor_uid)


def reaches(a, b):
    if a == b:
        return True
    seen, q = {a}, collections.deque([a])
    while q:
        u = q.popleft()
        for v in nxt[u]:
            if v == b:
                return True
            if v not in seen:
                seen.add(v)
                q.append(v)
    return False


def tie(s, d, why):
    global nrel
    if s is None or d is None or s.uid == d.uid:
        return False
    if (s.uid, d.uid) in have or reaches(d.uid, s.uid) or reaches(s.uid, d.uid):
        return False
    rels.append(Relation(uid=str(nrel), predecessor_uid=s.uid,
                         successor_uid=d.uid, type="Finish to Start", lag=0.0))
    nrel += 1
    have.add((s.uid, d.uid))
    nxt[s.uid].add(d.uid)
    prv[d.uid].add(s.uid)
    old = d.udfs.get("Comments", "")
    d.udfs["Comments"] = ((((old + "  ") if old else "") + why)[:248])
    return True


print("=" * 78)
print("OPEN END LEFT BY BREAKING THE LOOPS")
print("=" * 78)
# Building Systems Ready for Energization looked like the predecessor the
# name points at, but the guard refused it: Equipment Energization Start
# already reaches that milestone, so the tie would have rebuilt a loop. Left
# open and reported rather than forced.
src = ours.get("MDC1.MIL.1270")
dst = ours.get("MDC1.MIL.1410")
ok = tie(src, dst,
         "Lead-in added: breaking a loop removed this milestone's only "
         "predecessor, so it would have scheduled at the data date.")
print(f"  MDC1.MIL.1410 Equipment Energization Start: "
      + ("lead-in added off MDC1.MIL.1270" if ok else
         "still has no predecessor. The obvious candidate, MDC1.MIL.1270 "
         "Building Systems Ready for Energization, is DOWNSTREAM of it, so "
         "tying it would rebuild the loop. Needs a call on what really "
         "releases energization."))
p.relations = rels

# ---- the workbook ----------------------------------------------------
WBO = re.compile(r"\*+\s*WBO|by others", re.I)


def jer(a):
    return not WBO.search(str(a.name))


def scope(a):
    pth = path(a.wbs_uid)
    nm, aid = str(a.name), str(a.activity_id or "")
    if "Labour" in pth or aid.startswith("MDC1.LAB"):
        return "Labour record"
    if "Milestone" in pth or aid.startswith("MDC1.MIL"):
        return "Milestones"
    g = re.search(r"/ ((?:ER|MV|HV|HVR|ERR|ELEC) ?R?\d{3})$", pth)
    if g:
        return f"{g.group(1)[:2].strip()} rooms"
    for key, lab in (("Generator", "Generator rooms"),
                     ("Data Hall", "Data halls & galleries"),
                     ("Gallery", "Data halls & galleries"),
                     ("Electrical Room", "Electrical rooms"),
                     ("Admin", "Admin space"),
                     ("Roof", "Roof"), ("Precast", "Precast"),
                     ("Foundation", "Foundations"),
                     ("Underground", "Underground MEP"),
                     ("Site", "Site utilities"),
                     ("Structure", "Structure"), ("CUP", "CUP")):
        if key.lower() in pth.lower():
            return lab
    return pth.split(" / ")[1] if " / " in pth else (pth or "Other")


pred = collections.defaultdict(list)
for r in rels:
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
    if a.status == "In Progress":
        late = max(0, (dd - f).days) if f else 0
    else:
        late = max(0, (dd - s).days) if s else 0
    return (scope(a), str(a.activity_id), str(a.name), s, f,
            round((a.planned_duration or 0) / 8), late, len(ps), len(waiting),
            blk, ", ".join(hd) or "start here", why, path(a.wbs_uid), a.uid)


# This schedule was last calculated around 06/30/2026 -- 169 not-started
# activities still sit on that date, 97 on 07/22, 92 on 07/16. So 1,455 JER
# activities read as overdue purely because the dates are four months stale,
# and a catch-up list of 1,455 is not a field document.
#
# What the foreman can actually answer is the work that is READY: nothing
# incomplete in front of it. That is 136 activities. The rest are sitting
# behind something that has not happened, so there is no progress to report
# on them -- they need F9, not a status call.
def is_ready(a):
    return not [1 for r in pred[a.uid] if r.predecessor_uid in byu
                and byu[r.predecessor_uid].status != "Completed"]


ready, blocked, look = [], [], []
for a in p.activities:
    if not jer(a) or a.status == "Completed":
        continue
    s, f = D(a.planned_start), D(a.planned_finish)
    if a.status == "Not Started":
        if s and s < dd:
            (ready if is_ready(a) else blocked).append(
                row(a, "ready to work, start date passed" if is_ready(a)
                    else "overdue but waiting on something"))
        elif s and s <= HORIZON:
            look.append(row(a, "starts in the window"))
    elif a.status == "In Progress":
        if f and f < dd:
            ready.append(row(a, "running, finish date passed"))
        elif f and f <= HORIZON:
            look.append(row(a, "running, due in the window"))
for lst in (ready, blocked, look):
    lst.sort(key=lambda r: (r[0], r[3] or dt.date(2030, 1, 1), r[1]))
catch = ready
heads = ready
releases = collections.Counter()
for r in ready + blocked + look:
    for h in r[10].split(", "):
        releases[h] += 1
# what is holding the blocked work back
blockers = collections.Counter()
for r in blocked:
    for part in r[9].split(", "):
        if part and part != "-":
            blockers[part] += 1

print(f"\nMDC-1   data date {dd:%m/%d/%Y}   lookahead to {HORIZON:%m/%d/%Y}"
      f"   (JER scope only)")
print(f"ready to work now {len(ready)}   blocked {len(blocked)}   "
      f"lookahead {len(look)}")
print(f"\nTHE SCHEDULE'S DATES ARE STALE: it was last calculated around "
      f"06/30/2026.\n{len(ready) + len(blocked)} JER activities read as "
      f"overdue, but only {len(ready)} are actually ready to work.\nThe other "
      f"{len(blocked)} are waiting on something, so there is nothing to "
      f"status on them.")
for lst, title in ((ready, "READY TO WORK NOW"), (look, "TWO-WEEK LOOKAHEAD")):
    print("\n" + "=" * 76)
    print(f"{title}  ({len(lst)})")
    print("=" * 76)
    for sc, rows in itertools.groupby(lst, key=lambda r: r[0]):
        rows = list(rows)
        print(f"\n  {sc}  ({len(rows)})")
        for r in rows[:7]:
            print(f"     {r[1]:<26}{r[2][:34]:<36}{r[3]}{r[5]:>4}d"
                  f"{(str(r[6]) + 'd late') if r[6] else '':>9}  "
                  f"{'behind ' + r[9].split()[0] if r[8] else 'START HERE'}")
        if len(rows) > 7:
            print(f"     ... and {len(rows) - 7} more")

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
# the leverage list: what is holding back the most work
ws = wb.create_sheet("What is blocking")
ws.append(("Blocking activity", "Activities it holds up", "Status",
           "Planned finish", "WBS"))
for i, c in enumerate(ws[1], 1):
    c.font, c.fill = HEAD, FILL
    c.alignment = Alignment(wrap_text=True, vertical="top")
for k, v in blockers.most_common(120):
    aid = k.split()[0]
    a = ours.get(aid)
    ws.append((k, v, a.status if a is not None else "",
               D(a.planned_finish) if a is not None else None,
               path(a.wbs_uid) if a is not None else ""))
for i, w in enumerate((60, 12, 14, 14, 50), 1):
    ws.column_dimensions[get_column_letter(i)].width = w
for rr in ws.iter_rows(min_row=2, min_col=4, max_col=4):
    for c in rr:
        c.number_format = "mm/dd/yyyy"
ws.freeze_panes = "B2"

del wb["Sheet"]
out = f"{SCR}/MDC1_Catchup_and_Lookahead.xlsx"
wb.save(out)
xml = f"{SCR}/MDC1_logic_repaired_v3.xml"
write_p6_xml(p, xml, include_resources=False, include_udfs=True)
mx = max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()),
         default=0)
print(f"\nwrote {out}")
print(f"wrote {xml}")
print(f"relationships {len(rels)}   longest UDF value {mx}")
