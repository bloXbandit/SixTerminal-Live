"""What MDC-3's precast turnovers feed, and what MDC-1 does with its own.

MDC-3's "Turnover South" activities appear to dead-end. The questions are
whether they should chain area to area, or hand off into another scope, and
what MDC-1 -- the clean file -- actually does. Read-only.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def load(f):
    p = load_xml(f)
    byu = {a.uid: a for a in p.activities}
    wbs = {w.uid: w for w in p.wbs_nodes}

    def path(u):
        o, g = [], 0
        while u in wbs and g < 40:
            o.append(wbs[u].name)
            u = wbs[u].parent_uid
            g += 1
        return " / ".join(reversed(o))
    pre = collections.defaultdict(list)
    suc = collections.defaultdict(list)
    for r in p.relations:
        if r.predecessor_uid in byu and r.successor_uid in byu:
            pre[r.successor_uid].append((byu[r.predecessor_uid], r))
            suc[r.predecessor_uid].append((byu[r.successor_uid], r))
    return p, byu, path, pre, suc


PC = re.compile(r"precast|erection|turnover", re.I)

for f, lbl in ((f"{SCR}/MDC3_logic_repaired_v21.xml", "MDC-3"),
               (f"{SCR}/MDC1_logic_repaired_v12.xml", "MDC-1"),
               (f"{SCR}/MDC2_logic_repaired_v11.xml", "MDC-2")):
    p, byu, path, pre, suc = load(f)
    pc = [a for a in p.activities
          if PC.search(str(a.name)) or "Precast" in path(a.wbs_uid)]
    print("=" * 84)
    print(f"{lbl}   data date {str(p.data_date)[:10]}   "
          f"{len(pc)} precast-ish activities")
    print("=" * 84)
    if not pc:
        print("   none\n")
        continue

    ids = {a.uid for a in pc}
    print(f"\n{'activity':<24}{'name':<36}{'start':>11}  succ (outside precast)")
    for a in sorted(pc, key=lambda a: str(a.activity_id)):
        outs = [(q, r) for q, r in suc[a.uid] if q.uid not in ids]
        ins = [(q, r) for q, r in suc[a.uid] if q.uid in ids]
        tag = ""
        if outs:
            tag = "  ==> " + "; ".join(
                f"{q.activity_id} {str(q.name)[:30]} [{r.type[:4]}]"
                for q, r in outs[:2])
        elif ins:
            tag = "  (within precast: " + ", ".join(
                f"{q.activity_id}" for q, _ in ins[:3]) + ")"
        else:
            tag = "  <== NOTHING FOLLOWS"
        print(f"{str(a.activity_id):<24}{str(a.name)[:34]:<36}"
              f"{str(D(a.planned_start)):>11}{tag}")

    dead = [a for a in pc if not suc[a.uid]]
    print(f"\n{len(dead)} precast activities with no successor at all")
    if dead:
        for k, v in collections.Counter(
                re.sub(r"\s*\d+\s*", " ", str(a.name)).strip()
                for a in dead).most_common():
            print(f"   {v:>3}x  {k}")

    # what the whole precast block feeds, as kinds of work
    outbound = collections.Counter()
    for a in pc:
        for q, r in suc[a.uid]:
            if q.uid not in ids:
                outbound[(re.sub(r"\s*\([^)]*\)", "", str(q.name)).strip()[:44],
                          r.type)] += 1
    print(f"\nwhat precast hands off to ({sum(outbound.values())} ties):")
    for (nm, t), c in outbound.most_common(14):
        print(f"   {c:>3}x  {nm:<46} {t}")
    if not outbound:
        print("   nothing - the precast block is a closed island")

    # and what feeds precast
    inbound = collections.Counter()
    for a in pc:
        for q, r in pre[a.uid]:
            if q.uid not in ids:
                inbound[(re.sub(r"\s*\([^)]*\)", "", str(q.name)).strip()[:44],
                         r.type)] += 1
    print(f"\nwhat feeds precast ({sum(inbound.values())} ties):")
    for (nm, t), c in inbound.most_common(10):
        print(f"   {c:>3}x  {nm:<46} {t}")
    if not inbound:
        print("   nothing")
    print()
