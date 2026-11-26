"""MDC-1 v35: anchor the dangling starts, using Holder's own room sequence.

103 not-started activities have no predecessor at all, so a schedule run drags
each to the data date and the room's work appears to begin today regardless of
what has to happen first. 61 of them sit in Phase 3's electrical rooms alone.

Rather than invent an order, each one is anchored to whatever the 11/03
Exhibit S has finishing immediately before it in the same room. Holder's rooms
are fully sequenced; ours are the same rooms with the same stages under
different wording, so his order is the one to copy. Stage names are matched
across the two files through the wording differences already established --
his OH Rough-In Electrical/ FA is our Overhead Rough In/ FA, his Cable Tray
Install our OH Ladder Tray Install.

A tie is written only where our candidate predecessor really precedes it in
Holder's room, is not already downstream of the activity, and is not the
activity itself. Anything that cannot be matched is left alone and reported.

The three valve room chains are also anchored: Level 1 and Level 2 behind
their own FA backbone raceway, because the panel lands on that backbone, and
the penthouse behind the end of the Level 2 chain, since it is the same crew
moving up.
"""
import collections, copy, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v34.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v35.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")

# each level's valve rooms run off the backbone they land on. The penthouse
# chain runs alongside Level 2 rather than behind it: nothing about a
# penthouse valve room waits on the eight rooms below it, and chaining them
# drove the whole programme 28 days right for no physical reason.
VALVE_ANCHOR = {"101": "MDC1.FA.1040",       # FA Backbone Raceway - Level 1
                "201": "MDC1.FA.1100",       # FA Backbone Raceway - Level 2
                "301": "MDC1.FA.1100"}
STOP = {"the", "of", "to", "and", "a", "in", "s"}
SYN = {"oh": "overhead", "roughin": "rough", "roughins": "rough", "ins": "in",
       "penatration": "penetration", "crahs": "crah", "cracs": "crac",
       "ladder": "cable", "trim": "trimout", "out": "trimout"}


def d10(v):
    return str(v)[:10] if v else ""


def norm(t):
    s = re.sub(r'\([^)]*\)', ' ', str(t))
    s = re.sub(r'\*+\s*WBO', ' ', s, flags=re.I)
    s = re.sub(r'\s*-\s*(ER|ERR|MV|HV|HVR|DH|GL|Gen)\s*\d{2,3}\b.*$', ' ', s, flags=re.I)
    s = re.sub(r'\b(east|west|north|south)\b', ' ', s, flags=re.I)
    s = re.sub(r'\b\d+\b', ' ', s)
    s = s.lower().replace("/", " ").replace("-", " ")
    s = re.sub(r"[^a-z\s']", " ", s)
    out = []
    for t2 in s.split():
        t2 = SYN.get(t2.strip("'"), t2.strip("'"))
        if t2 and t2 not in STOP:
            out.append(t2)
    return frozenset(out)


def roomof(t):
    m = re.search(r'\b(ERR|ER|MV|HVR|HV|DH|GL|GAL|GEN|Gen)[\s.]?0?(\d{2,3})\b', str(t))
    return f"{m.group(1).upper().replace('GAL','GL')} {m.group(2)}" if m else None


def main():
    # ---- Holder's room sequences ----------------------------------------
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Schedule"]
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    H = collections.defaultdict(list)
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I["Activity Name"]] or "")
        rm = roomof(f"{r[0]} {nm}")
        if not rm:
            continue
        H[rm].append({"key": norm(nm), "name": nm,
                      "start": d10(r[I["Start"]]), "finish": d10(r[I["Finish"]])})
    for rm in H:
        H[rm].sort(key=lambda x: (x["finish"] or "9999", x["start"] or "9999"))
    print(f"Exhibit S sequences for {len(H)} rooms")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0 = len(p.relations)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v34 in: {len(p.activities)} activities, {r0} relations, finish {f0}")

    W = {n.uid: n for n in p.wbs_nodes}
    byid = {str(a.activity_id): a for a in p.activities}

    def roomfolder(u):
        cur = u
        while cur in W:
            n = str(W[cur].name)
            m = re.match(r'^(WBO\s+)?(ER|ERR|MV|HV|HVR|DH|GL|GAL)\s*(\d{3})$', n)
            if m:
                return f"{m.group(2).upper().replace('GAL','GL')} {m.group(3)}"
            cur = W[cur].parent_uid
        return None

    ourroom = {}
    for a in p.activities:
        rm = roomof(f"{a.activity_id} {a.name}") or roomfolder(a.wbs_uid)
        if rm:
            ourroom[a.uid] = rm
    byroom = collections.defaultdict(list)
    for a in p.activities:
        if a.uid in ourroom:
            byroom[ourroom[a.uid]].append(a)

    # descendants, so we never tie something behind its own successor
    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)

    def downstream(u):
        seen, st = set(), [u]
        while st:
            v = st.pop()
            for n2 in adj.get(v, ()):
                if n2 not in seen:
                    seen.add(n2); st.append(n2)
        return seen

    protoR = copy.deepcopy(p.relations[0])
    nextR = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1

    def tie(pre, suc, lag=0):
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid; r.successor_uid = suc.uid
        r.type = "Finish to Start"; r.lag = lag * 8.0
        p.relations.append(r)
        adj[pre.uid].append(suc.uid)
        return r

    have_pred = {r.successor_uid for r in p.relations}
    loose = [a for a in p.activities
             if a.uid not in have_pred and not a.actual_start]
    print(f"not-started activities with no predecessor: {len(loose)}")

    anchored, unmatched = 0, []
    bystage = collections.Counter()
    for a in sorted(loose, key=lambda a: str(a.activity_id)):
        rm = ourroom.get(a.uid)
        if not rm or rm not in H:
            unmatched.append((a, "room not in Exhibit S"))
            continue
        k = norm(a.name)
        seq = H[rm]
        # where does this stage sit in Holder's room?
        pos = None
        for i, h in enumerate(seq):
            if h["key"] == k or (k and h["key"] and
                                 (k <= h["key"] or h["key"] <= k)):
                pos = i; break
        if pos is None:
            unmatched.append((a, "stage not in Holder's room"))
            continue
        down = downstream(a.uid)
        # the nearest earlier stage of Holder's that we also carry
        cand = None
        for h in reversed(seq[:pos]):
            for b in byroom[rm]:
                if b.uid == a.uid or b.uid in down:
                    continue
                bk = norm(b.name)
                if bk == h["key"] or (bk and h["key"] and
                                      (bk <= h["key"] or h["key"] <= bk)):
                    cand = b; break
            if cand:
                break
        if cand is None:
            unmatched.append((a, "no earlier stage we carry"))
            continue
        tie(cand, a)
        anchored += 1
        bystage[re.sub(r'\s*\([^)]*\)$', '', str(a.name))[:40]] += 1
    print(f"   anchored {anchored}, left alone {len(unmatched)}")
    for k, v in bystage.most_common(10):
        print(f"      {v:>4}  {k}")
    why = collections.Counter(w for _, w in unmatched)
    for k, v in why.most_common():
        print(f"      {v:>4}  left: {k}")

    # ---- valve room chains ----------------------------------------------
    nv = 0
    for lvl, aid in VALVE_ANCHOR.items():
        first = byid.get(f"MDC1.FA.VR{lvl}.1000")
        anc = byid.get(aid)
        if first is not None and anc is not None and first.uid not in have_pred:
            tie(anc, first)
            nv += 1
            print(f"   Valve Rm {lvl} chain behind {anc.name}")


    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    t2 = {r.successor_uid for r in p.relations}
    still = sum(1 for a in p.activities if a.uid not in t2 and not a.actual_start)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv35 out: {len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   dangling starts {len(loose)} -> {still}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, x, y in sorted(moved)[:12]:
        print(f"      {k:<24}{x} -> {y}")
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
    q = load_xml(SRC)
    assert len(p.activities) == len(q.activities), "activity count changed"
    hp = sum(float(a.planned_labor_units or 0) for a in p.activities)
    hq = sum(float(a.planned_labor_units or 0) for a in q.activities)
    assert abs(hp - hq) < 1, "hours changed"
    print("   checks: activity count and hours unchanged, only ties added")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
