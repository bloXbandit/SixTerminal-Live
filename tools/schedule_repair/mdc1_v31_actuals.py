"""MDC-1 v31: reopen generator room pulls and terminations that the file
claims complete and the 11/03 Exhibit S does not.

Our file marks every pull and termination complete in ten generator rooms.
Holder's 11/03 Exhibit S has "Cable Pulls & Terms. (Gen NNN)" not started in
those same rooms, forecast November 2026 to February 2027, and JER's own
comment against Gen 316 and Gen 317 reads "JER cannot complete due to LBB
breaker testing as of 11.10.2026". The schedule therefore claims complete the
work JER formally told Holder it cannot complete.

The actual dates also do not read as a progress record. In Gen 319 seven
activities in a strict chain -- wall penetration layout, set LBB, three pulls,
final connections, terminate -- all carry actual finish 2026-10-09. Across the
file a third of all actual finishes fall on a date shared by ten or more other
activities.

Every pull, termination and final connection in a generator room whose Cable
Pulls & Terms. is not complete in Exhibit S is returned to Not Started: actual
start and finish cleared, remaining duration restored, percent complete zeroed.
Nothing else is touched -- no logic, no durations, no hours, and no activity in
a room Holder shows as complete.
"""
import collections, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v30.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v31.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")

# the pull / termination family inside a generator room
PT = re.compile(r'^(Pull LBB|Terminate Generator|Final Connections LBB|'
                r'Final Tray Connections)', re.I)


def d10(v):
    return str(v)[:10] if v else ""


def holder_pulls():
    """Per generator room: is Cable Pulls & Terms. complete in Exhibit S?"""
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Schedule"]
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I["Activity Name"]] or "")
        m = re.search(r'Cable Pulls & Terms\..*\(Gen (\d{3})\)', nm)
        if m:
            out[m.group(1)] = {
                "done": str(r[I["Finish Status"]] or "") == "Actual",
                "finish": d10(r[I["Finish"]]),
                "note": str(r[I["NOTES"]] or "").strip()}
    return out


def main():
    H = holder_pulls()
    print(f"Exhibit S carries Cable Pulls & Terms. for {len(H)} generator rooms, "
          f"{sum(1 for v in H.values() if v['done'])} complete")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    done0 = sum(1 for a in p.activities if a.actual_finish)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v30 in: finish {f0}, {done0} activities complete")

    byroom = collections.defaultdict(list)
    for a in p.activities:
        m = re.search(r'\(Gen (\d{3})\)', str(a.name))
        if m and PT.search(str(a.name)):
            byroom[m.group(1)].append(a)

    reopened, rooms = [], []
    for rm, acts in sorted(byroom.items()):
        h = H.get(rm)
        if not h or h["done"]:
            continue                      # Holder says complete, leave it
        hit = [a for a in acts if a.actual_finish or a.actual_start]
        if not hit:
            continue
        rooms.append((rm, len(hit), len(acts), h["finish"], h["note"]))
        for a in hit:
            reopened.append((str(a.activity_id), str(a.name),
                             d10(a.actual_finish), h["finish"]))
            a.actual_start = None
            a.actual_finish = None
            a.status = "Not Started"
            a.percent_complete = 0.0
            a.actual_duration = 0.0
            a.remaining_duration = a.planned_duration
            a.actual_labor_units = 0.0
            a.remaining_labor_units = a.planned_labor_units

    print(f"\nrooms reopened: {len(rooms)}")
    print(f"   {'room':<7}{'reopened':>9}{'Holder forecast':>18}   note")
    for rm, n, tot, hf, note in rooms:
        print(f"   {rm:<7}{n:>4}/{tot:<4}{hf:>18}   {note[:46]}")
    print(f"\n{len(reopened)} activities returned to Not Started")

    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    done1 = sum(1 for a in p.activities if a.actual_finish)
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)]
             if k in mil0 and mil0[k] != d10(a.early_finish)]
    print(f"\nv31 out: complete {done0} -> {done1} ({done1-done0:+d})")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}")
    print(f"   milestones moved: {len(moved)}")
    for k, a, b in sorted(moved, key=lambda x: x[0])[:14]:
        print(f"      {k:<24}{a} -> {b}")
    q = load_xml(SRC)
    assert len(p.activities) == len(q.activities)
    assert len(p.relations) == len(q.relations)
    hp = sum(float(a.planned_labor_units or 0) for a in p.activities)
    hq = sum(float(a.planned_labor_units or 0) for a in q.activities)
    assert abs(hp - hq) < 1, "planned hours changed"
    print("   checks: activity count, relationships and planned hours unchanged")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
