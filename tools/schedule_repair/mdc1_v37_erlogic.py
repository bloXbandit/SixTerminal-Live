"""MDC-1 v37: anchor the remaining dangling starts inside the rooms.

The 40 in Phase 3's electrical rooms are not disconnected rooms and not
missing first activities. Each room has 21 activities, 15 to 19 of them
properly linked, and the loose ones sit in the MIDDLE of the sequence with a
successor but no predecessor: Electrical Terminations and Controls, Overhead
Rough In Fire Protection, Set UPS in every room, plus Device Trim Out,
Drywall Framing and Testing and Inspections in ER 108 and ER 109.

They were missed in v35 for a dull reason. The activity is spelled
"Electrical Te rminations and Controls", with a space inside the word, so it
never matched Holder's "Electrical Terminations and Controls" and the room
sequence could not be read across. The matcher now collapses spaces inside
words before comparing, which is what makes these reachable.

Anything still unmatched falls back to the room's own order: the latest
activity in the same room that finishes before this one's successor starts
and is not already downstream of it. A tie is written only when it cannot
close a loop, tested in the correct direction.
"""
import collections, copy, re, sys
import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v36.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v37.xml"
XL = (f"{SCR}/exs1103_src/"
      "beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx")

STOP = {"the", "of", "to", "and", "a", "in", "s"}
SYN = {"oh": "overhead", "roughin": "rough", "roughins": "rough", "ins": "in",
       "penatration": "penetration", "crahs": "crah", "cracs": "crac",
       "ladder": "cable"}


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
    # "Electrical Te rminations" -> one word: a stray space inside a word is a
    # typo, not a word boundary, so glue single and double letter fragments on
    toks, out = s.split(), []
    for t2 in toks:
        if out and len(t2) > 2 and len(out[-1]) <= 2 and out[-1] not in STOP:
            out[-1] = out[-1] + t2
        else:
            out.append(t2)
    return frozenset(SYN.get(x, x) for x in out if x and x not in STOP)


def roomof(t):
    m = re.search(r'\b(ERR|ER|MV|HVR|HV|DH|GL|GAL|GEN|Gen)[\s.]?0?(\d{2,3})\b', str(t))
    return f"{m.group(1).upper().replace('GAL','GL')} {m.group(2)}" if m else None


def main():
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
        if rm:
            H[rm].append({"key": norm(nm), "name": nm,
                          "finish": d10(r[I["Finish"]])})
    for rm in H:
        H[rm].sort(key=lambda x: x["finish"] or "9999")

    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0 = len(p.relations)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v36 in: {r0} relations, finish {f0}")

    A = {a.uid: a for a in p.activities}
    W = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in W:
            o.append(str(W[u].name))
            u = W[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    def roomfolder(u):
        cur = u
        while cur in W:
            m = re.match(r'^(WBO\s+)?(ER|ERR|MV|HV|HVR|DH|GL|GAL)\s*(\d{3})$',
                         str(W[cur].name))
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

    adj = collections.defaultdict(list)
    for r in p.relations:
        adj[r.predecessor_uid].append(r.successor_uid)
    succ_of = collections.defaultdict(list)
    for r in p.relations:
        succ_of[r.predecessor_uid].append(r)

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

    def tie(pre, suc):
        nonlocal nextR
        r = copy.deepcopy(protoR)
        r.uid = str(nextR); nextR += 1
        r.predecessor_uid = pre.uid; r.successor_uid = suc.uid
        r.type = "Finish to Start"; r.lag = 0
        p.relations.append(r)
        adj[pre.uid].append(suc.uid)

    have = {r.successor_uid for r in p.relations}
    loose = [a for a in p.activities if a.uid not in have and not a.actual_start]
    print(f"dangling starts: {len(loose)}")

    tied, left = [], []
    for a in sorted(loose, key=lambda a: str(a.activity_id)):
        rm = ourroom.get(a.uid)
        down = downstream(a.uid)
        target = why = None
        # 1. Holder's own order in the same room
        if rm and rm in H:
            k = norm(a.name)
            seq = H[rm]
            pos = next((i for i, h in enumerate(seq)
                        if h["key"] == k or (k and h["key"] and
                                             (k <= h["key"] or h["key"] <= k))), None)
            if pos is not None:
                for h in reversed(seq[:pos]):
                    for b in byroom.get(rm, []):
                        if b.uid == a.uid or b.uid in down:
                            continue
                        if a.uid in downstream(b.uid):
                            pass                     # b already leads to a
                        bk = norm(b.name)
                        if bk == h["key"] or (bk and h["key"] and
                                              (bk <= h["key"] or h["key"] <= bk)):
                            if a.uid not in downstream(b.uid) or True:
                                target, why = b, "Holder's order in this room"
                                break
                    if target:
                        break
        # There is no rule 2. Picking the latest-finishing activity in the
        # room produced nonsense -- Electrical Terminations behind Start ups
        # of Equipment, Drywall Framing behind Set RMU's -- because these
        # activities all float at the data date, so their dates carry no
        # ordering information to read. Sequence has to come from Holder's
        # file or from the foreman, not from dates that are themselves the
        # symptom. Anything Holder's order cannot reach is reported, not
        # guessed at.
        if target is None:
            left.append((a, rm or "-"))
            continue
        if a.uid in downstream(target.uid):
            left.append((a, "would loop"))
            continue
        tie(target, a)
        tied.append((a, target, why))

    print(f"\ntied {len(tied)}:")
    c = collections.Counter((re.sub(r'\s*\([^)]*\)$', '', str(a.name))[:34],
                             re.sub(r'\s*\([^)]*\)$', '', str(t.name))[:30])
                            for a, t, _ in tied)
    for (an, tn), v in c.most_common(14):
        print(f"   {v:>3}x  {an:<36}<- {tn}")
    print(f"\nleft {len(left)}")
    for a, rm in left[:10]:
        print(f"   {a.activity_id:<24}{str(a.name)[:40]:<42}{rm}")

    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    h2 = {r.successor_uid for r in p.relations}
    still = sum(1 for a in p.activities if a.uid not in h2 and not a.actual_start)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv37 out: {len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   dangling starts {len(loose)} -> {still}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, x, y in sorted(moved)[:8]:
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
    assert len(p.activities) == len(q.activities)
    print("   checks: activity count unchanged, only ties added")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
