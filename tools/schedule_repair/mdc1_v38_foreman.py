"""MDC-1 v38: anchor the loose room activities on the foreman's sequence.

The predecessors are his, not derived:

  Overhead Rough In Fire Protection   <- OH Branch Power
  Electrical Terminations & Controls  <- Set CRAH's AND Electrical Pulls
  Set UPS                             <- Set Electrical Skids
  Drywall Framing                     <- Overhead Rough In Electrical/ FA
  Device Trim Out                     <- Floor Finishes (same room)
  Testing and Inspections             <- Electrical Terminations & Controls

One substitution, flagged rather than hidden: only ER 210 carries an OH
Branch Power activity. The Phase 3 electrical rooms and the MV rooms run the
leaner 21-activity set and have none, so there the fire protection rough-in
is hung off Overhead Rough In Electrical/ FA, which is the overhead
electrical work in those rooms. Every such substitution is listed.

Names are matched exactly, not by prefix: "Overhead Rough In " alone matches
Fire Protection, Mechanical and Electrical alike, and an earlier check in
this session reported a false match because of it. The loop screen asks
whether the activity is reachable FROM the proposed predecessor, which is the
direction that actually catches a cycle.
"""
import collections, copy, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC1_logic_repaired_v36.xml"
OUT = f"{SCR}/MDC1_logic_repaired_v38.xml"


def clean(n):
    s = re.sub(r'\s*\([^)]*\)\s*$', '', str(n))
    s = re.sub(r'\s*\*+\s*WBO\s*$', '', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip()


# short words that are real words, so a following word is a new word and not
# the tail of a typo. Without this, "Overhead Rough In Fire Protection"
# collapses to "...rough infire protection" and matches nothing.
REAL_SHORT = {"in", "of", "to", "a", "at", "on", "by", "up", "no", "is",
              "it", "or", "as", "an", "fa", "oh", "mv", "hv", "er", "dh",
              "gl", "cx", "qa", "qc", "ac", "tt"}


def squash(n):
    """glue a stray space inside a word: 'Te rminations' -> 'Terminations',
    but leave real short words alone."""
    toks, out = clean(n).lower().split(), []
    for t in toks:
        if out and len(t) > 2 and len(out[-1]) <= 2 \
                and out[-1] not in REAL_SHORT:
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return " ".join(out)


# loose activity -> the predecessors the foreman named, first match wins per
# entry; a tuple means tie to every one of them that the room has
RULES = [
    ("overhead rough in fire protection",
     [("oh branch power",), ("overhead rough in electrical/ fa",),
      ("overhead rough in/ fa",)]),          # the MV rooms' spelling
    ("electrical terminations and controls",
     [("set crah's", "electrical pulls and final connections")]),
    ("set ups", [("set electrical skids",)]),
    ("drywall framing", [("overhead rough in electrical/ fa",)]),
    ("device trim out", [("floor finishes",)]),
    ("testing and inspections",
     [("electrical terminations and controls",)]),
]
SUBSTITUTE = {("overhead rough in fire protection",
               "overhead rough in electrical/ fa"):
              "no OH Branch Power in this room"}


def d10(v):
    return str(v)[:10] if v else ""


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    r0 = len(p.relations)
    mil0 = {str(a.activity_id): d10(a.early_finish)
            for a in p.activities if ".MIL." in str(a.activity_id)}
    print(f"v36 in: {r0} relations, finish {f0}")

    W = {n.uid: n for n in p.wbs_nodes}

    def roomof(u):
        cur = u
        while cur in W:
            m = re.match(r'^(WBO\s+)?(ER|ERR|MV|HV|HVR|DH|GL)\s*(\d{3})$',
                         str(W[cur].name))
            if m:
                return f"{m.group(2)} {m.group(3)}"
            cur = W[cur].parent_uid
        return None

    byroom = collections.defaultdict(list)
    for a in p.activities:
        rm = roomof(a.wbs_uid)
        if rm:
            byroom[rm].append(a)

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
    print(f"dangling starts: {len(loose)}\n")

    added, subs, left = 0, [], []
    detail = collections.Counter()
    for a in sorted(loose, key=lambda a: str(a.activity_id)):
        rm = roomof(a.wbs_uid)
        if rm is None:
            left.append((a, "not in a room folder"))
            continue
        key = squash(a.name)
        rule = next((r for k, r in RULES if k == key), None)
        if rule is None:
            left.append((a, f"no rule for '{clean(a.name)}'"))
            continue
        pool = {squash(b.name): b for b in byroom[rm] if b.uid != a.uid}
        chosen = None
        for option in rule:
            found = [pool[n] for n in option if n in pool]
            if len(found) == len(option):
                chosen = (option, found)
                break
        if chosen is None:
            left.append((a, f"room has none of {rule}"))
            continue
        option, preds = chosen
        wrote = 0
        for q in preds:
            if reaches(a.uid, q.uid):          # q already downstream of a
                continue
            tie(q, a)
            wrote += 1
            detail[(key, squash(q.name))] += 1
        if wrote == 0:
            left.append((a, "every candidate would loop"))
            continue
        added += wrote
        note = SUBSTITUTE.get((key, option[0]))
        if note:
            subs.append((rm, clean(a.name), option[0], note))
    print(f"ties written: {added}")
    for (k, q), v in detail.most_common():
        print(f"   {v:>3}x  {k[:38]:<40}<- {q}")
    if subs:
        print(f"\nsubstitutions ({len(subs)}), flagged not hidden:")
        for rm, nm, used, why in subs[:6]:
            print(f"   {rm:<9}{nm[:34]:<36}used '{used}'  ({why})")
        if len(subs) > 6:
            print(f"   ... {len(subs)-6} more of the same")
    print(f"\nstill loose: {len(left)}")
    c = collections.Counter(w for _, w in left)
    for k, v in c.most_common(8):
        print(f"   {v:>3}  {k[:66]}")

    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish), default="")
    neg = sum(1 for a in p.activities if (a.total_float or 0) < 0)
    h2 = {r.successor_uid for r in p.relations}
    still = sum(1 for a in p.activities if a.uid not in h2 and not a.actual_start)
    moved = [(k, mil0[k], d10(a.early_finish)) for a in p.activities
             if ".MIL." in str(a.activity_id)
             for k in [str(a.activity_id)] if k in mil0
             and mil0[k] != d10(a.early_finish)]
    print(f"\nv38 out: {len(p.relations)} relations (+{len(p.relations)-r0})")
    print(f"   dangling starts {len(loose)} -> {still}")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float {neg}, milestones moved {len(moved)}")
    for k, x, y in sorted(moved)[:10]:
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
    q2 = load_xml(SRC)
    assert len(p.activities) == len(q2.activities)
    print("   checks: activity count unchanged, only ties added")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")


if __name__ == "__main__":
    main()
