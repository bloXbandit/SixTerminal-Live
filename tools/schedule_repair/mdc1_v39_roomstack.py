"""MDC-1 v39: give the loose rooms an entry point, from Holder's room stack.

Two things settled first, by looking rather than deciding:

  Holder runs the same overhead order we do. OH Rough-In Fire Protection,
  then Mechanical, then Electrical/FA, then Drywall Framing, in every room.
  So nothing is reversed: our order was already his.

  What hands a room over is the crew finishing the room before it. Sorting
  Holder's OH Rough-In Fire Protection by start date lays out one continuous
  walk, ERR 202 then ER 208, 209, 210, then the HV and MV rooms, then ER 211
  to 214, then the Phase 3 group ERR 201 and 101, ER 107 down to ER 101 with
  the MV rooms interleaved, then ER 207 back down to ER 201.

Each room whose fire protection has no predecessor is therefore tied behind
the fire protection of the room before it in that walk. The sequence is read
off Holder's dates, not invented, and where he shows two rooms starting the
same day they stay parallel rather than being forced into a line.

ER 108, 109 and 110 are not in Exhibit S at all. They are chained after the
last Phase 3 room in the walk, in room order, and flagged as such.
"""
import collections, copy, datetime as dt, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v38.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v39.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")

FP = re.compile(r'^(OH|Overhead) Rough.?In Fire Protection', re.I)
EXTRA = ["ER 108", "ER 109", "ER 110"]      # absent from Exhibit S


def d10(v):
    return str(v)[:10] if v else ""


def holder_walk():
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Schedule"]
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I["Activity Name"]] or "").strip()
        m = re.match(r'OH Rough-In Fire Protection \((ER|ERR|MV|HV|HVR)\s*(\d{3})\)', nm)
        if m:
            out.append((f"{m.group(1)} {m.group(2)}", d10(r[I["Start"]])))
    out.sort(key=lambda x: x[1] or "9999")
    return out


def main():
    walk = holder_walk()
    print(f"Holder's fire protection walk: {len(walk)} rooms")
    print("   " + " -> ".join(f"{rm}" for rm, _ in walk[:10]) + " -> ...")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0 = len(p.relations)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v38 in: {r0} relations, finish {f0}")

    W = {n.uid: n for n in p.wbs_nodes}

    def roomof(u):
        cur = u
        while cur in W:
            m = re.match(r'^(WBO\s+)?(ER|ERR|MV|HV|HVR)\s*(\d{3})$', str(W[cur].name))
            if m:
                return f"{m.group(2)} {m.group(3)}"
            cur = W[cur].parent_uid
        return None

    # our fire protection activity per room
    ourfp = {}
    for a in p.activities:
        if FP.match(str(a.name)):
            rm = roomof(a.wbs_uid)
            if rm and rm not in ourfp:
                ourfp[rm] = a
    print(f"we carry a fire protection rough-in in {len(ourfp)} rooms")

    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)

    def reaches(src, dst):
        seen, st = set(), [src]
        while st:
            v = st.pop()
            if v == dst:
                return True
            for n2 in adj.get(v, ()):
                if n2 not in seen:
                    seen.add(n2); st.append(n2)
        return False

    protoR = copy.deepcopy(p.relations[0])
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1

    def workdays(a1, b1):
        """working days between two ISO dates, Holder's own stagger"""
        try:
            d1, d2 = dt.date.fromisoformat(a1), dt.date.fromisoformat(b1)
        except Exception:
            return 1
        if d2 < d1:
            return 0
        n, cur = 0, d1
        while cur < d2:
            cur += dt.timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        return n

    def tie(pre, suc, lag):
        """Start to Start with Holder's gap. His crew walk OVERLAPS: the rooms
        start a day or two apart while each takes five days, so a Finish to
        Start chain turns a one-day stagger into a five-day serial line and
        pushed this file 100 days the first time it was run."""
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid; r.successor_uid = suc.uid
        r.type = "Start to Start"; r.lag = lag * 8.0
        p.relations.append(r)
        adj[pre.uid].append(suc.uid)

    have = {r.successor_uid for r in p.relations}
    # the walk as an ordered list of rooms we actually carry, plus the extras
    order = [rm for rm, _ in walk if rm in ourfp]
    seen = set()
    order = [x for x in order if not (x in seen or seen.add(x))]
    tail = [x for x in EXTRA if x in ourfp]
    order_all = order + tail

    print(f"\nwalk restricted to rooms we carry: {len(order)}"
          f" (+{len(tail)} not in Exhibit S, chained on the end)")

    tied, left = [], []
    for i, rm in enumerate(order_all):
        a = ourfp[rm]
        if a.uid in have or a.actual_start:
            continue                      # already driven, or already underway
        # the nearest earlier room in the walk whose FP we carry
        target = None
        for prev in reversed(order_all[:i]):
            q = ourfp.get(prev)
            if q is None or q.uid == a.uid:
                continue
            if reaches(a.uid, q.uid):     # would close a loop
                continue
            target = (prev, q)
            break
        if target is None:
            left.append((rm, "no earlier room in the walk"))
            continue
        prev, q = target
        hs_map = {r2: s2 for r2, s2 in walk}
        lag = workdays(hs_map.get(prev, ""), hs_map.get(rm, "")) \
            if prev in hs_map and rm in hs_map else 1
        tie(q, a, lag)
        tied.append((rm, prev, rm in EXTRA, lag))

    print(f"\ntied {len(tied)} rooms into the walk:")
    for rm, prev, extra, lag in tied:
        note = "   (not in Exhibit S, chained by room order)" if extra else ""
        print(f"   {rm:<9}<- {prev:<9}SS +{lag}d{note}")
    if left:
        print(f"\nleft {len(left)}:")
        for rm, why in left:
            print(f"   {rm:<9}{why}")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    h2 = {r.successor_uid for r in p.relations}
    still = sum(1 for a in p.activities if a.uid not in h2 and not a.actual_start)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv39 out: {len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   dangling starts -> {still}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, x, y in sorted(moved)[:10]:
        print(f"      {k:<24}{x} -> {y}")
    # does our walk now agree with Holder's ordering?
    print("\n   room fire-protection start, ours against Holder's order:")
    hs = {rm: s for rm, s in walk}
    bad = 0
    prev_ours = None
    for rm in order_all:
        a = ourfp[rm]
        o = d10(a.actual_start or a.early_start)
        flag = ""
        if prev_ours and o < prev_ours:
            flag = "   <-- out of walk order"; bad += 1
        print(f"      {rm:<9}Holder {hs.get(rm,'n/a'):<12}ours {o}{flag}")
        prev_ours = o
    print(f"   {bad} rooms out of order against the walk")
    colour = {}
    def ok(s0):
        st = [(s0, iter(adj[s0]))]; colour[s0] = 1
        while st:
            v, it = st[-1]
            n2 = next(it, None)
            if n2 is None:
                colour[v] = 2; st.pop()
            elif colour.get(n2, 0) == 1:
                return False
            elif colour.get(n2, 0) == 0:
                colour[n2] = 1; st.append((n2, iter(adj[n2])))
        return True
    acyc = all(ok(a.uid) for a in p.activities if colour.get(a.uid, 0) == 0)
    print(f"   cycles: {'none' if acyc else 'PRESENT'}")
    assert acyc
    q2 = load_xml(SRC)
    assert len(p.activities) == len(q2.activities)
    print("   checks: activity count unchanged, only ties added")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
