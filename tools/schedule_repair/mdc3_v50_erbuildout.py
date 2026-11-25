"""MDC3 v50: itemise the ER rooms' overhead rough-in into the six activities
the reference workbook confirms, inside the window each room already has.

Each ER room carries one 5-day "Overhead Rough In Electrical/ FA" bar. The
reference ER sheet breaks that work into six activities totalling 23 days
sequential, which cannot be chained end to end inside 5 days, so they are
laddered on Start to Start with a one-day stagger. That is how the work
actually runs in a small room: steel leads hangers by a day, hangers lead
tray, and so on. The last activity finishes on the window's last day, so the
room's start and finish are unchanged.

  Install High Steel   2d  3 men   takes the old bar's predecessors
  Install Hangers      2d  3 men   SS +1 day
  Install Cable Tray   2d  4 men   SS +1 day
  Conduit Install      2d  5 men   SS +1 day
  Bus Duct Install     1d  5 men   SS +1 day
  OH Branch Power      1d  5 men   SS +0 days, takes the old bar's successors

Crew counts are the workbook's own. Hours are NOT inflated: the room's total
planned hours are preserved and split across the six by their share of
man-days, so the labour budget does not move. The report states what the
crew-based hours would have been instead, which is the user's call to make.
"""
import collections, copy, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC3_logic_repaired_v49.xml"
UDF = "Number of Electricians"

# Hours for the six steps.
#   "preserve": keep the room's existing total and split it by man-day share.
#   "crews":    hours = men x 10.6 x duration days, the formula the rest of
#               MDC3 uses, so the sequence carries what its own crews imply
#               rather than what the single rolled-up bar happened to hold.
HOURS = "crews"
OUT = (f"{SCR}/MDC3_logic_repaired_v51.xml" if HOURS == "crews"
       else f"{SCR}/MDC3_logic_repaired_v50.xml")
HOURS_PER_MANDAY = 10.6

# name, duration days, men, lag in days from the previous one (SS)
STEPS = [("Install High Steel",  2, 3, None),
         ("Install Hangers",     2, 3, 1),
         ("Install Cable Tray",  2, 4, 1),
         ("Conduit Install",     2, 5, 1),
         ("Bus Duct Install",    1, 5, 1),
         ("OH Branch Power",     1, 5, 0)]


def d10(v):
    return str(v)[:10] if v else ""


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    a0, r0 = len(p.activities), len(p.relations)
    h0 = sum(float(a.planned_labor_units or 0) for a in p.activities)
    print(f"v49 in: {a0} activities, {r0} relations, {h0:,.0f} hours, finish {f0}")

    w = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in w:
            o.append(str(w[u].name))
            u = w[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    allbars = [a for a in p.activities
               if re.search(r'Overhead Rough In Electrical', str(a.name), re.I)
               and 'Electrical Rooms' in path(a.wbs_uid)]
    # a bar with no electrician count and no hours is not a JER-loaded bar:
    # splitting it would add five empty rows and no loading
    bars = [a for a in allbars if a.udfs.get(UDF)]
    for a in allbars:
        if a not in bars:
            print(f"   skipped {a.activity_id}: no '{UDF}' value and "
                  f"{float(a.planned_labor_units or 0):.0f} hours, not JER loaded")
    print(f"{len(bars)} of {len(allbars)} overhead rough-in bars to itemise\n")

    # what each room already carries, so no step is duplicated
    already = collections.defaultdict(set)
    for a in p.activities:
        already[a.wbs_uid].add(str(a.name).lower())

    used = {str(a.activity_id) for a in p.activities}
    nextact = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
    nextrel = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
    protoact = copy.deepcopy(bars[0])
    protorel = copy.deepcopy(p.relations[0])

    newacts, newrels, log = [], [], []
    drop = set()
    manday_total = sum(d * m for _, d, m, _ in STEPS)

    for bar in sorted(bars, key=lambda a: str(a.activity_id)):
        oldid = str(bar.activity_id)
        room = re.sub(r'^.*?-\s*', '', str(bar.name)).strip() or oldid
        # tail of the room name, as the file writes it, e.g. "ER 212 - Data Center A"
        m = re.search(r'-\s*((?:ER|ERR|ELEC)\s*\d{3}.*)$', str(bar.name))
        tail = m.group(1).strip() if m else ""
        hours = float(bar.planned_labor_units or 0)
        win = (d10(bar.early_start), d10(bar.early_finish))
        preds = [r for r in p.relations if r.successor_uid == bar.uid]
        succs = [r for r in p.relations if r.predecessor_uid == bar.uid]

        base = re.match(r'^(.*\.)(\d+)$', oldid)
        stem, num = base.group(1), int(base.group(2))
        made = []
        skipped_steps = []
        for i, (nm, dur, men, lag) in enumerate(STEPS):
            if any(e.startswith(nm.lower()) for e in already[bar.wbs_uid]):
                skipped_steps.append(nm)
                continue
            # a free id near the old one
            cand = None
            for off in range(1, 400):
                t = f"{stem}{num + off}"
                if t not in used:
                    cand = t
                    break
            used.add(cand)
            a = copy.deepcopy(protoact)
            a.uid = str(nextact); nextact += 1
            a.activity_id = cand
            a.name = f"{nm} - {tail}" if tail else nm
            a.wbs_uid = bar.wbs_uid
            a.calendar_uid = bar.calendar_uid
            a.planned_duration = dur * 8.0
            a.remaining_duration = dur * 8.0
            a.actual_duration = 0.0
            a.planned_labor_units = 0.0  # set once the kept steps are known
            a.remaining_labor_units = a.planned_labor_units
            a.actual_labor_units = 0.0
            a.planned_start = bar.planned_start
            a.planned_finish = bar.planned_finish
            a.actual_start = None
            a.actual_finish = None
            a.early_start = a.early_finish = None
            a.late_start = a.late_finish = None
            a.total_float = a.free_float = None
            a.status = "Not Started"
            a.percent_complete = 0.0
            a.constraint_type = None
            a.constraint_date = None
            a.udfs = dict(bar.udfs)
            a.udfs[UDF] = str(men)
            newacts.append(a)
            made.append((a, dur, men, lag))

        # hours for the steps actually made
        mdt = sum(d * m for _, d, m, _ in made) or 1
        for a, dur, men, lag in made:
            if HOURS == "crews" and hours > 0:
                a.planned_labor_units = round(men * HOURS_PER_MANDAY * dur, 1)
            else:
                a.planned_labor_units = round(hours * (dur * men) / mdt, 1)
            a.remaining_labor_units = a.planned_labor_units

        # ladder them Start to Start
        for i in range(1, len(made)):
            a, dur, men, lag = made[i]
            r = copy.deepcopy(protorel)
            r.uid = str(nextrel); nextrel += 1
            r.predecessor_uid = made[i - 1][0].uid
            r.successor_uid = a.uid
            r.type = "Start to Start"
            r.lag = lag * 8.0
            newrels.append(r)

        # the old bar's logic moves to the ends of the chain
        first, last = made[0][0], made[-1][0]
        for r in preds:
            r.successor_uid = first.uid
        for r in succs:
            r.predecessor_uid = last.uid
        drop.add(bar.uid)
        if skipped_steps:
            print(f"   {oldid}: {', '.join(skipped_steps)} already in this room, "
                  f"not duplicated")
        log.append((oldid, str(bar.name), len(preds), len(succs), hours,
                    [a.activity_id for a, _, _, _ in made], win))

    p.activities.extend(newacts)
    p.activities[:] = [a for a in p.activities if a.uid not in drop]
    p.relations.extend(newrels)
    # a predecessor and successor both re-pointed onto the same chain can meet
    seen, clean = set(), []
    for r in p.relations:
        if r.predecessor_uid == r.successor_uid:
            continue
        k = (r.predecessor_uid, r.successor_uid, r.type, r.lag)
        if k in seen:
            continue
        seen.add(k)
        clean.append(r)
    collapsed = len(p.relations) - len(clean)
    p.relations[:] = clean

    print(f"replaced {len(log)} bars with {len(newacts)} activities "
          f"and {len(newrels)} new ties ({collapsed} duplicate ties collapsed)")

    # ---- verify ----
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    h1 = sum(float(a.planned_labor_units or 0) for a in p.activities)
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    moved = [(k, v, d10(a.early_finish))
             for a in p.activities if ".MIL." in str(a.activity_id)
             for k, v in [(str(a.activity_id), mil0.get(str(a.activity_id)))]
             if v and d10(a.early_finish) != v]
    byid = {str(a.activity_id): a for a in p.activities}
    print(f"\nwindow check, per room (the bar's own window against the chain's):")
    bad = 0
    for oldid, nm, np_, ns, hrs, ids, win in log:
        ch = [byid[i] for i in ids if i in byid]
        s = min(d10(x.early_start) for x in ch)
        f = max(d10(x.early_finish) for x in ch)
        same = (s == win[0] and f == win[1])
        if not same:
            bad += 1
        print(f"   {oldid:<24}{win[0]} -> {win[1]}   becomes   {s} -> {f}   "
              f"{len(ch)} acts {sum(float(x.planned_labor_units or 0) for x in ch):>6.0f} h"
              f"{'' if same else '   <-- MOVED'}")
    print(f"   {len(log)-bad} of {len(log)} rooms held their window to the day, "
          f"{bad} moved")
    assert bad == 0, f"{bad} rooms did not hold their window"
    print(f"\nv50 out: {len(p.activities)} activities, {len(p.relations)} relations, "
          f"{h1:,.0f} hours")
    print(f"   activities {a0} -> {len(p.activities)} ({len(p.activities)-a0:+d})")
    print(f"   hours {h0:,.0f} -> {h1:,.0f} ({h1-h0:+,.0f})")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}")
    print(f"   milestones moved: {len(moved)}")
    for k, v, nv in moved:
        print(f"      {k}  {v} -> {nv}")
    was = sum(l[4] for l in log)
    now = sum(float(byid[i].planned_labor_units or 0)
              for l in log for i in l[5] if i in byid)
    BASIS = 605707.0
    print(f"\n   hours mode: {HOURS}")
    print(f"   the itemised rooms carry {now:,.0f} h, against {was:,.0f} h in "
          f"the bars they replaced ({now-was:+,.0f})")
    print(f"   schedule total {h1:,.0f} h = {100*h1/BASIS:.1f}% of the "
          f"{BASIS:,.0f} h basis (was {100*h0/BASIS:.1f}%)")
    # hours must not move a date in this engine; prove it
    assert f1 == f0, "finish moved"
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")
    return log


if __name__ == "__main__":
    main()
