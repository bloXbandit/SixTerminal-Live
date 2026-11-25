"""Report for v49: every count, with where it came from and the basis."""
import collections, importlib.util, io, contextlib, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
OUT = f"{SCR}/MDC3_v49_electrician_counts.xlsx"
HDR = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
JUDGE = PatternFill("solid", fgColor="FFF2CC")
UDF = "Number of Electricians"

spec = importlib.util.spec_from_file_location(
    "v49", "/home/user/SixTerminal-Live/tools/schedule_repair/mdc3_v49_crews.py")
V = importlib.util.module_from_spec(spec)
V.OUT = "/dev/null"
spec.loader.exec_module(V)


def sheet(wb, name, cols, rows, note="", judgecol=None):
    ws = wb.create_sheet(name)
    r = 1
    if note:
        c = ws.cell(1, 1, note)
        c.font = Font(italic=True, color="555555")
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
        ws.row_dimensions[1].height = 30
        r = 3
    for i, cn in enumerate(cols, 1):
        cell = ws.cell(r, i, cn)
        cell.font = HDR
        cell.fill = FILL
    head = r
    for row in rows:
        r += 1
        for i, v in enumerate(row, 1):
            ws.cell(r, i, v)
        if judgecol is not None and str(row[judgecol]) == "rule":
            for i in range(1, len(cols) + 1):
                ws.cell(r, i).fill = JUDGE
    for i, cn in enumerate(cols, 1):
        vals = [len(str(cn))] + ([len(str(x[i - 1])) for x in rows] or [0])
        ws.column_dimensions[get_column_letter(i)].width = min(max(vals) + 3, 60)
    ws.freeze_panes = ws.cell(head + 1, 1)
    return ws


def main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        decided, leftover, leftover_ex = V.main()
    a = load_xml(f"{SCR}/MDC3_logic_repaired_v47.xml")
    b = load_xml(f"{SCR}/MDC3_logic_repaired_v49.xml")
    bef = collections.Counter(str(x.udfs.get(UDF)) for x in a.activities if UDF in x.udfs)
    aft = collections.Counter(str(x.udfs.get(UDF)) for x in b.activities if UDF in x.udfs)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Summary")
    ws["A1"] = "MDC-3 v49 — electrician counts, every activity set deliberately"
    ws["A1"].font = Font(bold=True, size=13)
    for i, h in enumerate(("# of Electricians", "Before", "After", "Change"), 1):
        c = ws.cell(3, i, h); c.font = HDR; c.fill = FILL
    r = 3
    for k in sorted(set(bef) | set(aft), key=lambda x: (len(x), x)):
        r += 1
        ws.cell(r, 1, int(k) if k.isdigit() else k)
        ws.cell(r, 2, bef.get(k, 0)); ws.cell(r, 3, aft.get(k, 0))
        ws.cell(r, 4, aft.get(k, 0) - bef.get(k, 0))
    src = collections.Counter(d[5] for d in decided)
    r += 2
    ws.cell(r, 1, "Where each count came from").font = Font(bold=True)
    for k, v in src.most_common():
        r += 1
        ws.cell(r, 1, k); ws.cell(r, 2, v)
    r += 2
    ws.cell(r, 1, "Hours, durations, dates and logic are unchanged and asserted so. "
                  "Only the Number of Electricians field moved. Rows shaded in the "
                  "Counts tab are the ones set by reasoning rather than by a direct "
                  "workbook match, so those are the ones to check. A count of 0 means "
                  "another trade carries that activity and JER has no electricians on "
                  "it, which is why the flat 4 had to go."
                  ).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 5, end_column=6)
    ws.column_dimensions["A"].width = 40
    for col in "BCDEF":
        ws.column_dimensions[col].width = 14

    sheet(wb, "Counts",
          ["Activity ID", "Activity", "Room type", "Was", "Now", "Source", "Basis"],
          sorted(decided, key=lambda x: (x[2], x[1], x[0])),
          "Every activity whose count changed. Source 'workbook' means a direct "
          "match in the reference; 'alias' means the same work under another "
          "name; 'rule' means reasoned from the scope, and those rows are shaded.",
          judgecol=5)

    # the rule book itself, so the reasoning is reviewable in one place
    rules = [(pat, sc or "any", val, why) for pat, sc, val, why in V.RULES]
    sheet(wb, "Rule Book",
          ["Matches activity names containing", "Room scale", "# Electricians", "Basis"],
          rules,
          "The reasoning applied where the workbook had no equivalent activity. "
          "Room scale 'big' is a data hall, gallery or the CUP; 'small' is an ER, "
          "MV, HV or generator room. The workbook puts 9 to 15 men on large-area "
          "overhead and 2 to 5 on the same work in a small room.")

    alias = [(k, sh, refname) for k, (sh, refname) in V.ALIAS.items()]
    sheet(wb, "Aliases", ["MDC3 activity starts with", "Reference sheet",
                          "Treated as reference activity"], alias,
          "MDC-3 names that are the reference activity under different wording. "
          "These take the reference's own count.")

    # by room type, what the crew curve now looks like
    wn = {n.uid: n for n in b.wbs_nodes}

    def path(u):
        o = []
        while u in wn:
            o.append(str(wn[u].name))
            u = wn[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    byrt = collections.defaultdict(collections.Counter)
    for x in b.activities:
        if UDF not in x.udfs:
            continue
        rt = V.rtype(x, path)
        if rt in ("LLE", "other"):
            continue
        byrt[rt][str(x.udfs[UDF])] += 1
    allv = sorted({v for c in byrt.values() for v in c},
                  key=lambda x: (len(x), x))
    rows = []
    for rt in sorted(byrt):
        rows.append(tuple([rt] + [byrt[rt].get(v, 0) for v in allv]))
    sheet(wb, "By Room Type", ["Room type"] + [f"{v} men" for v in allv], rows,
          "How the counts distribute per room type now.")

    wb.save(OUT)
    print(f"written {OUT}")
    for s in wb.sheetnames:
        print(f"   {s}: {wb[s].max_row} rows")
    print(f"\nundecided: {sum(leftover.values())}")


if __name__ == "__main__":
    main()
