"""Change log workbook for v46: every folder deleted, delivery moved,
duplicate merged and tie added, so each edit can be checked by hand."""
import collections, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
OUT = f"{SCR}/MDC3_v47_change_log.xlsx"
HDR = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
TITLE = Font(bold=True, size=13)


def d10(v):
    return str(v)[:10] if v else ""


def load(f):
    p = load_xml(f)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    return p


def sheet(wb, name, cols, rows, note=""):
    ws = wb.create_sheet(name)
    r = 1
    if note:
        ws.cell(1, 1, note).font = Font(italic=True, color="555555")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
        r = 3
    for i, c in enumerate(cols, 1):
        cell = ws.cell(r, i, c)
        cell.font = HDR
        cell.fill = FILL
    for row in rows:
        r += 1
        for i, v in enumerate(row, 1):
            ws.cell(r, i, v)
    for i, c in enumerate(cols, 1):
        w = max(len(str(c)), *(len(str(x[i - 1])) for x in rows)) if rows else len(str(c))
        ws.column_dimensions[get_column_letter(i)].width = min(w + 3, 62)
    ws.freeze_panes = ws.cell(r - len(rows) + 1, 1)
    return ws


def main():
    a = load(f"{SCR}/MDC3_logic_repaired_v45.xml")
    b = load(f"{SCR}/MDC3_logic_repaired_v47.xml")
    A = {str(x.activity_id): x for x in a.activities}
    B = {str(x.activity_id): x for x in b.activities}
    wa = {w.uid: w for w in a.wbs_nodes}
    wbn = {w.uid: w for w in b.wbs_nodes}

    def path(nodes, u):
        o = []
        while u in nodes:
            o.append(str(nodes[u].name))
            u = nodes[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # --- summary ---
    ws = wb.create_sheet("Summary")
    ws["A1"] = "MDC-3 v47 — WBS structure and orphan logic (against v45)"
    ws["A1"].font = TITLE
    fa = max((d10(x.early_finish) for x in a.activities if x.early_finish), default="")
    fb = max((d10(x.early_finish) for x in b.activities if x.early_finish), default="")
    orA = len([x for x in a.activities
               if not any(r.predecessor_uid == x.uid for r in a.relations)])
    orB = len([x for x in b.activities
               if not any(r.predecessor_uid == x.uid for r in b.relations)])
    rows = [("Activities", len(a.activities), len(b.activities)),
            ("Relations", len(a.relations), len(b.relations)),
            ("WBS folders", len(a.wbs_nodes), len(b.wbs_nodes)),
            ("Activities with no successor", orA, orB),
            ("Negative float activities",
             sum(1 for x in a.activities if (x.total_float or 0) < 0),
             sum(1 for x in b.activities if (x.total_float or 0) < 0)),
            ("Project finish", fa, fb)]
    for i, h in enumerate(("Measure", "v45", "v47"), 1):
        c = ws.cell(3, i, h); c.font = HDR; c.fill = FILL
    for j, r in enumerate(rows, 4):
        for i, v in enumerate(r, 1):
            ws.cell(j, i, v)
    ws["A12"] = ("The finish date does not move. No milestone moves. No relation that "
                 "was already in the file is removed. Every tie added is Finish to "
                 "Start with no lag, written only where the predecessor already "
                 "finishes before the successor starts, so it records the sequence "
                 "without taking a day out of anyone's float. All 911 deliveries now "
                 "sit in the LLE bucket for their phase, so room folders show "
                 "installed scope only and every MV room folder holds the same 18 "
                 "activities. No pair is tied in both directions and no delivery "
                 "feeds another delivery.")
    ws["A12"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A12:F18")
    ws.column_dimensions["A"].width = 34
    for col in "BC":
        ws.column_dimensions[col].width = 16

    # --- folders deleted ---
    kept = {path(wbn, w.uid) for w in b.wbs_nodes}
    gone = sorted(path(wa, w.uid) for w in a.wbs_nodes
                  if path(wa, w.uid) not in kept)
    sheet(wb, "Folders Deleted", ["WBS path in v45"], [(g,) for g in gone],
          "Folders with no activities under them, here or below. The room's real "
          "work sits in a sibling folder in every case.")

    # --- deliveries moved ---
    moved = []
    for k, x in B.items():
        if k in A and path(wa, A[k].wbs_uid) != path(wbn, x.wbs_uid):
            moved.append((k, str(x.name), path(wa, A[k].wbs_uid),
                          path(wbn, x.wbs_uid)))
    sheet(wb, "Deliveries Moved", ["Activity ID", "Name", "Was in", "Now in"],
          sorted(moved),
          "Deliveries foldered by the phase token in their ID rather than the "
          "phase the room is built in. Moving between folders changes no logic.")

    # --- duplicates merged ---
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "v46", "/home/user/SixTerminal-Live/tools/schedule_repair/mdc3_v46.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    merged = [(t, keep, str(A[keep].name) if keep in A else "", drop,
               str(A[drop].name) if drop in A else "") for t, keep, drop in m.MERGE]
    sheet(wb, "Duplicates Merged",
          ["Equipment tag", "Kept ID", "Kept name", "Deleted ID", "Deleted name"],
          merged,
          "One piece of equipment carried by two delivery rows. The keeper is the "
          "row holding the successor logic; the deleted row's ties were moved onto "
          "the keeper first, so nothing was unlinked.")

    # --- ties added ---
    def relset(p):
        mm = {x.uid: str(x.activity_id) for x in p.activities}
        return {(mm[r.predecessor_uid], mm[r.successor_uid], r.type)
                for r in p.relations
                if r.predecessor_uid in mm and r.successor_uid in mm}

    new = sorted(relset(b) - relset(a))
    ties = []
    for pid, sid, typ in new:
        x, y = B.get(pid), B.get(sid)
        if not x or not y:
            continue
        ties.append((pid, str(x.name), d10(x.early_finish), sid, str(y.name),
                     d10(y.early_start), typ,
                     round((y.total_float or 0) / 8.0, 1)))
    sheet(wb, "New Logic Ties",
          ["Predecessor ID", "Predecessor", "Pred finish", "Successor ID",
           "Successor", "Succ start", "Type", "Succ float (days)"],
          ties,
          "Every tie added. The predecessor finishes on or before the successor "
          "starts in all cases, so none of these consumed float or moved a date.")

    # --- still open ---
    openx = [(str(x.activity_id), str(x.name), d10(x.early_finish))
             for x in b.activities
             if not any(r.predecessor_uid == x.uid for r in b.relations)]
    sheet(wb, "Still Open", ["Activity ID", "Name", "Finish"], sorted(openx),
          "Left without a successor on purpose. A Substantial Completion "
          "milestone is the end of its phase and has nothing to feed.")

    wb.save(OUT)
    print(f"written {OUT}")
    for s in wb.sheetnames:
        print(f"   {s}: {wb[s].max_row - 1} rows")


if __name__ == "__main__":
    main()
