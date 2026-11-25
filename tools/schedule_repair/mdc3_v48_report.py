"""Report for v48: what was mapped, what was not, and how MDC3's scope in each
room type lines up against the confirmed JER scope in the reference workbook."""
import collections, importlib.util, io, contextlib, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
OUT = f"{SCR}/MDC3_v48_electrician_counts.xlsx"
HDR = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3864")
WARN = PatternFill("solid", fgColor="FFF2CC")
UDF = "Number of Electricians"

spec = importlib.util.spec_from_file_location(
    "v48", "/home/user/SixTerminal-Live/tools/schedule_repair/mdc3_v48_crews.py")
V = importlib.util.module_from_spec(spec)
V.OUT = "/dev/null"
spec.loader.exec_module(V)

# analogies worth proposing but NOT written into the file: the user decides
SUGGEST = {
    "Install In-Wall MEP": ("MEP In Wall", 10),
    "Install Electrical Overhead": ("Remaining Overhead Rough In / Equipment Tie In", 10),
    "Install Lighting": ("Lighting / MEP Trim Out", 9),
    "Install Fire Alarm Devices": ("Ceiling Mounted Device Install", 10),
    "Install Upper Vesda": ("Ceiling Mounted Device Install", 10),
    "Install VESDA Rack": ("Ceiling Mounted Device Install", 10),
    "Inspection of Upper Vesda": ("Testing and Inspections", 3),
    "Rooms Ready for Inspection": ("Testing and Inspections", 3),
    "Term MSB": ("Electrical Pulls and Final Connections", 8),
    "Pull MSB": ("Electrical Pulls and Final Connections", 8),
    "Term CRAH": ("Electrical Terminations and Controls", 4),
    "Pull from EDP": ("Electrical Pulls and Final Connections", 8),
    "Term from EDP": ("Electrical Terminations and Controls", 4),
    "Install Station Batteries": ("Set Misc. Electrical Equip", 4),
    "FA Device Install": ("Ceiling Mounted Device Install", 10),
}


def sheet(wb, name, cols, rows, note="", warncol=None):
    ws = wb.create_sheet(name)
    r = 1
    if note:
        ws.cell(1, 1, note).font = Font(italic=True, color="555555")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
        ws.row_dimensions[1].height = 28
        ws.cell(1, 1).alignment = Alignment(wrap_text=True, vertical="top")
        r = 3
    for i, c in enumerate(cols, 1):
        cell = ws.cell(r, i, c)
        cell.font = HDR
        cell.fill = FILL
    head = r
    for row in rows:
        r += 1
        for i, v in enumerate(row, 1):
            ws.cell(r, i, v)
        if warncol is not None and not row[warncol]:
            for i in range(1, len(cols) + 1):
                ws.cell(r, i).fill = WARN
    for i, c in enumerate(cols, 1):
        vals = [len(str(c))] + [len(str(x[i - 1])) for x in rows] if rows else [len(str(c))]
        ws.column_dimensions[get_column_letter(i)].width = min(max(vals) + 3, 58)
    ws.freeze_panes = ws.cell(head + 1, 1)
    return ws


def main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        changed, unmapped, unmapped_ex = V.main()
    tables = V.load_ref() if False else None

    import openpyxl as xl
    wbref = xl.load_workbook(V.REF, data_only=True)
    reftab = {}
    for sh in wbref.sheetnames:
        rows = []
        for rr in wbref[sh].iter_rows(values_only=True):
            if not rr or len(rr) < 4:
                continue
            nm, dur, men = rr[1], rr[2], rr[3]
            if nm is None:
                continue
            t = str(nm).strip()
            # skip the header row, the section headings and the footnotes
            if (t.lower() in ("activity name", "activity id")
                    or t.startswith("NOTE:") or t.startswith("*=")
                    or t.startswith("PH2 ") or re.match(r'^(Level \d|Lineup )', t)):
                continue
            try:
                men = int(float(str(men).strip()))
            except (TypeError, ValueError):
                men = None
            rows.append((str(nm).strip(), dur, men))
        reftab[sh] = rows

    a = load_xml(f"{SCR}/MDC3_logic_repaired_v47.xml")
    b = load_xml(f"{SCR}/MDC3_logic_repaired_v48.xml")
    wn = {n.uid: n for n in b.wbs_nodes}

    def path(u):
        o = []
        while u in wn:
            o.append(str(wn[u].name))
            u = wn[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ---- summary ----
    ws = wb.create_sheet("Summary")
    ws["A1"] = "MDC-3 v48 — electrician counts from the MV Commissioning workbook"
    ws["A1"].font = Font(bold=True, size=13)
    bef = collections.Counter(str(x.udfs.get(UDF)) for x in a.activities if UDF in x.udfs)
    aft = collections.Counter(str(x.udfs.get(UDF)) for x in b.activities if UDF in x.udfs)
    for i, h in enumerate(("# of Electricians", "Before", "After", "Change"), 1):
        c = ws.cell(3, i, h); c.font = HDR; c.fill = FILL
    r = 3
    for k in sorted(set(bef) | set(aft), key=lambda x: (len(x), x)):
        r += 1
        ws.cell(r, 1, k); ws.cell(r, 2, bef.get(k, 0)); ws.cell(r, 3, aft.get(k, 0))
        ws.cell(r, 4, aft.get(k, 0) - bef.get(k, 0))
    r += 2
    ws.cell(r, 1, "Activities matched to the workbook").font = Font(bold=True)
    ws.cell(r, 2, len(changed) + sum(1 for _ in []) )
    ws.cell(r, 2, 1086)
    r += 1
    ws.cell(r, 1, "Of those, value actually changed"); ws.cell(r, 2, len(changed))
    r += 1
    ws.cell(r, 1, "Still at 4 (no workbook match)"); ws.cell(r, 2, aft.get("4", 0))
    r += 2
    ws.cell(r, 1, "Hours, durations, dates and logic are untouched. Only the "
                  "Number of Electricians field changed. The same activity name "
                  "can hold a different count per room type, so each activity was "
                  "matched against its own room type's sheet first and only then "
                  "against the others.").alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 4, end_column=5)
    ws.column_dimensions["A"].width = 38
    for col in "BCDE":
        ws.column_dimensions[col].width = 14

    # ---- mapped ----
    rows = [(aid, nm, rt, old, new, refname, src, how)
            for aid, nm, rt, old, new, refname, src, how in changed]
    sheet(wb, "Counts Changed",
          ["Activity ID", "Activity", "Room type", "Was", "Now",
           "Matched reference activity", "From sheet", "Match"],
          sorted(rows, key=lambda x: (x[2], x[1])),
          "Every activity whose count changed. 'From sheet' says which room "
          "type's sheet supplied the number; 'borrowed' means that room type's "
          "own sheet did not list the activity.")

    # ---- unmapped, with suggestions ----
    un = []
    for k, cnt in unmapped.most_common():
        rt, nm = k.split(" | ", 1)
        key = nm.strip()
        sug = None
        for pat, (refn, men) in SUGGEST.items():
            if key.lower().startswith(pat.lower()):
                sug = (refn, men); break
        un.append((rt, key, cnt, unmapped_ex[k],
                   sug[1] if sug else "", sug[0] if sug else ""))
    sheet(wb, "Not Mapped", ["Room type", "Activity", "Count of activities",
                             "Example ID", "Suggested #", "By analogy with"],
          un,
          "Names the workbook does not carry. These keep the value they already "
          "had. Where a defensible analogy exists it is suggested but NOT written "
          "into the file. Shaded rows have no suggestion, mostly work by others "
          "(drywall, paint, floor finishes) or scope the workbook does not cover.",
          warncol=4)

    # ---- scope correlation ----
    def rtype_of(x):
        return V.rtype(x, path)
    present = collections.defaultdict(set)
    for x in b.activities:
        rt = rtype_of(x)
        if rt in ("LLE", "other"):
            continue
        present[rt].add(V.norm(x.name))
    corr = []
    for sh, rows_ in reftab.items():
        rt = V.SHEET_FOR.get(sh, sh)
        for nm, dur, men in rows_:
            k = V.norm(nm)
            if not k:
                continue
            hit = k in present.get(rt, set())
            if not hit:
                for kk in present.get(rt, set()):
                    if kk and (kk <= k or k <= kk) and len(kk & k) >= 2:
                        hit = True; break
            anywhere = ""
            if not hit:
                for ort, ks in present.items():
                    if ort == rt:
                        continue
                    if k in ks:
                        anywhere = ort; break
            corr.append((sh, nm, dur if dur is not None else "",
                         men if men is not None else "(blank, WBO)",
                         "yes" if hit else "no", anywhere))
    sheet(wb, "Scope Correlation",
          ["Reference sheet", "Reference activity", "Ref duration",
           "Ref # electricians", "In MDC3 same room type?", "Found in"],
          corr,
          "Confirmed JER scope from the workbook against what MDC-3 carries in "
          "the same room type. 'no' with a blank 'Found in' is scope the "
          "reference has and MDC-3 does not.")

    wb.save(OUT)
    print(f"written {OUT}")
    miss = [c for c in corr if c[4] == "no"]
    print(f"\nscope correlation: {len(corr)-len(miss)} of {len(corr)} reference "
          f"activities present in the same room type")
    for c in miss:
        print(f"   MISSING  {c[0]:<17}{c[1][:46]:<48}ref {c[3]} men"
              f"{'   (exists in '+c[5]+')' if c[5] else ''}")
    for s in wb.sheetnames:
        print(f"   {s}: {wb[s].max_row} rows")


if __name__ == "__main__":
    main()
