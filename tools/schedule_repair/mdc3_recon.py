"""MDC-3 v26 reconciliation workbook: what changed, what to confirm, what is left.

Four sheets:
  Actuals adopted      the 26 activities statused from Holder's export
  Confirm with Holder  the 5 where our field update and their export disagree
  Added                the three missing rooms and the tag gate
  Granularity gap      the one open decision, with the numbers behind it
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
H = json.load(open(f"{SCR}/work/holder_mdc3.json"))
hact = {a["id"]: a for a in H["acts"]}
V = json.load(open(f"{SCR}/work/mdc3_v26.json"))
p = load_xml(f"{SCR}/MDC3_logic_repaired_v26.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
dd = dt.date.fromisoformat(str(p.data_date)[:10])
prv = collections.defaultdict(set)
for r in p.relations:
    prv[r.successor_uid].add(r.predecessor_uid)


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
ASK = PatternFill("solid", fgColor="FFF2CC")
SUB = Font(bold=True)
SUBF = PatternFill("solid", fgColor="DDEBF7")
wb = Workbook()


def sheet(title, cols, wid, rows, entry=()):
    ws = wb.create_sheet(title)
    ws.append(cols)
    for i, c in enumerate(ws[1], 1):
        c.font, c.fill = HEAD, FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if cols[i - 1] in entry:
            c.fill = PatternFill("solid", fgColor="BF8F00")
    ecol = [cols.index(k) + 1 for k in entry]
    for r in rows:
        if len(r) == 1:
            ws.append(r)
            for c in ws[ws.max_row]:
                c.font, c.fill = SUB, SUBF
            continue
        ws.append(r)
        for j in ecol:
            ws.cell(row=ws.max_row, column=j).fill = ASK
    for i, w in enumerate(wid, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for rr in ws.iter_rows(min_row=2):
        for c in rr:
            if isinstance(c.value, dt.date):
                c.number_format = "mm/dd/yyyy"
    ws.freeze_panes = "C2"
    return ws


# ---- 1. actuals adopted ------------------------------------------------
rows = []
for i, was, now, s, f in V["adopted"]:
    a = ours.get(i)
    rows.append((i, str(a.name) if a is not None else "", path(a.wbs_uid)
                 if a is not None else "", was, now, D(s), D(f),
                 (dd - D(f)).days if D(f) and now == "Completed" else None,
                 "Holder reports this finished; our file had not been statused"))
rows.sort(key=lambda r: (str(r[6]), r[0]))
sheet("Actuals adopted",
      ("Activity ID", "Activity", "WBS", "We had", "Now", "Actual start",
       "Actual finish", "Days before data date", "Why"),
      (24, 46, 44, 13, 13, 13, 13, 10, 54), rows)

# ---- 2. disagreements --------------------------------------------------
rows = []
for i, nm, hs in V["conflict"]:
    a = ours.get(i)
    rows.append((i, nm, path(a.wbs_uid) if a is not None else "",
                 "Completed (ours, from JER field update)",
                 f"In Progress, started {hs} (Holder)",
                 D(a.actual_finish) if a is not None else None, "", ""))
sheet("Confirm with Holder",
      ("Activity ID", "Activity", "WBS", "Our status", "Holder's status",
       "Our actual finish", "Confirmed? (Y/N)", "Notes"),
      (24, 48, 44, 38, 34, 15, 15, 40), rows,
      entry=("Confirmed? (Y/N)", "Notes"))

# ---- 3. what was added -------------------------------------------------
rows = [("-- three rooms that were missing entirely --",)]
for label, n, nw, nj in V["rooms"]:
    rows.append((label, "room", n, nj, nw,
                 f"imported on Holder's own activity ids; {nj} JER scope, "
                 f"{nw} marked **WBO"))
rows.append(("-- equipment tag gate --",))
rows.append(("Equipment Tag Verification (PH1)", "tag gate", V["tags"],
             V["tag_equipment"], 0,
             f"4 stages x {V['tag_equipment']} equipment items: Red (JER), "
             f"Pink (Holder), Orange (Cloud HQ), Yellow (ready to energize). "
             f"Nothing energizes until all four clear."))
rows.append(("-- Holder's logic --",))
tot = sum(V["rel_added"].values())
rows.append(("Relationships from Holder", "logic", tot, "", "",
             f"{V['rel_added'].get('Finish to Start', 0)} FS, "
             f"{V['rel_added'].get('Start to Start', 0)} SS, "
             f"{V['rel_added'].get('Finish to Finish', 0)} FF. "
             f"{V['rel_cand']} candidates; the rest were already implied by "
             f"logic we carry ({V['rel_cand'] - tot - V['skip_cycle']}) or "
             f"would have made a loop ({V['skip_cycle']})."))
sheet("Added",
      ("What", "Kind", "Activities", "JER scope / items", "**WBO", "Notes"),
      (34, 12, 12, 18, 10, 92), rows)

# ---- 4. the granularity gap -------------------------------------------
def norm(n):
    n = re.sub(r"\*+\s*WBO", "", str(n))
    n = re.sub(r"\s*-\s*Data Center\s*[A-Z]\s*$", "", n)
    n = re.sub(r"\s*\(PH\d\)\s*", "", n)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", n)).strip().lower()


def room(s):
    m = re.search(r"\b(ER|ERR|MV|MVR|HV|HVR|GL|GAL|ELEC|ELC|GEN|DH|ADM|POE|STO"
                  r"|VAL)\s*R?(\d{2,3})\b", s.upper())
    return f"{m.group(1)}{m.group(2)}" if m else ""


mine = collections.defaultdict(set)
for a in p.activities:
    mine[room(f"{a.activity_id} {a.name}") or room(path(a.wbs_uid))].add(norm(a.name))
allm = set()
for v in mine.values():
    allm |= v
ELEC = re.compile(
    r"\bPull\b|\bTerm\b|Tapbox|conduit|raceway|cable tray|wire|feeder|\bATS\b|"
    r"\bMSB\b|\bEDP\b|\bESB\b|\bRMU\b|\bGIS\b|\bMSG\b|busway|ground|whip|"
    r"switchgear|\bgear\b|panel|lighting|device|electrical|\bFA\b|skid|\bUPS\b|"
    r"transformer|\bXFMR\b|generator|energi|start.?up|load bank|\bSCR\b|isolator",
    re.I)
agg = collections.defaultdict(lambda: [0, 0, 0])
ex = collections.defaultdict(collections.Counter)
for a in H["acts"]:
    if re.match(r"(Red|Pink|Orange|Yellow) Tag", a["name"]):
        continue
    k = ".".join(a["id"].split(".")[:3])
    r = room(f"{a['id']} {a['name']}")
    nm = norm(a["name"])
    agg[k][0] += 1
    if nm in mine.get(r, set()) or (not r and nm in allm):
        agg[k][1] += 1
    else:
        if ELEC.search(a["name"]):
            agg[k][2] += 1
        g = re.sub(r"[A-Z]{2,6}-[A-Z0-9\-.]{2,}", "<EQ>", a["name"])
        g = re.sub(r"\(.*?\)", "", g)
        ex[k][re.sub(r"\s+", " ", re.sub(r"\b\d{1,3}\b", "N", g)).strip()[:52]] += 1
rows = []
for k, v in sorted(agg.items(), key=lambda x: -(x[1][0] - x[1][1])):
    miss = v[0] - v[1]
    if miss < 15:
        continue
    rows.append((k, v[0], v[1], miss, v[2],
                 "; ".join(f"{c}x {n}" for n, c in ex[k].most_common(3))))
sheet("Granularity gap",
      ("Holder branch", "Holder activities", "We match by name", "Missing",
       "of those, JER electrical", "What the missing ones are"),
      (24, 15, 15, 10, 16, 86), rows)

del wb["Sheet"]
out = f"{SCR}/MDC3_v26_Reconciliation.xlsx"
wb.save(out)
print(f"wrote {out}")
print(f"  actuals adopted {len(V['adopted'])}   disagreements {len(V['conflict'])}")
print(f"  granularity rows {len(rows)}   total missing "
      f"{sum(r[3] for r in rows)}   JER electrical {sum(r[4] for r in rows)}")
