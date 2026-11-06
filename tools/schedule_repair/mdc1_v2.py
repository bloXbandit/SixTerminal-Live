"""MDC-1: break the scheduling loops and move the data date to 10/31/2026.

TWO JOBS.

1. THE LOOPS. The file is not schedulable as it stands: 24 clusters holding 94
   activities are circular, and 303 activities cannot be ordered at all once
   everything downstream of a loop is counted. P6 will refuse to run a forward
   pass through these.

   Most of them are one wiring error repeated across the electrical rooms:

     Fire Protection -> Mechanical -> Cable Tray -> Conduit -> Branch Power
            ^                                                        |
            +--------------------------------------------------------+

   The file's own dates say fire protection runs LAST in these rooms,
   concurrent with branch power, not first -- in ER 213 the mechanical starts
   07/16 while fire protection does not finish until 08/12. So the
   Fire Protection -> Mechanical tie runs 27 days backwards and is the edge
   that closes the loop. Seven rooms carrying the same five activities have no
   such tie and schedule cleanly, which is the pattern the rest should match.

   Rather than assume that shape everywhere, each circular cluster is handled
   on its own evidence: the relationship inside it that runs furthest
   backwards against the activities' own dates is cut, and the cluster is
   re-tested. That repeats until nothing is circular. Every cut is listed.

   This is the one case where the standing rule against removing logic has to
   give: a loop is not a sequencing opinion, it is a schedule that cannot be
   calculated. Nothing else is removed.

2. THE DATA DATE moves from 09/30/2026 to 10/31/2026, as asked, not to 11/04.
   The file carries no actual dates after 09/30, so October is unstatused:
   moving the date does not invent progress, it exposes the month that needs
   statusing. That is what the catch-up sheet is for -- fill it in, then run
   F9 once.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SRC = sys.argv[1]
SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
NEW_DD = dt.date(2026, 10, 31)
p = load_xml(SRC)
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
OLD_DD = dt.date.fromisoformat(str(p.data_date)[:10])
A0, R0 = len(p.activities), len(p.relations)


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def sccs(rels):
    """every group of 2+ activities that can all reach each other"""
    nxt = collections.defaultdict(set)
    for r in rels:
        if r.predecessor_uid in byu and r.successor_uid in byu:
            nxt[r.predecessor_uid].add(r.successor_uid)
    index, low, on, stk, out, idx = {}, {}, {}, [], [], [0]
    for root in byu:
        if root in index:
            continue
        index[root] = low[root] = idx[0]
        idx[0] += 1
        stk.append(root)
        on[root] = True
        work = [(root, iter(nxt[root]))]
        while work:
            v, it = work[-1]
            adv = False
            for w in it:
                if w not in byu:
                    continue
                if w not in index:
                    index[w] = low[w] = idx[0]
                    idx[0] += 1
                    stk.append(w)
                    on[w] = True
                    work.append((w, iter(nxt[w])))
                    adv = True
                    break
                if on.get(w):
                    low[v] = min(low[v], index[w])
            if adv:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stk.pop()
                    on[w] = False
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(set(comp))
    return out


print("=" * 78)
print("1. CIRCULAR LOGIC")
print("=" * 78)
rels = list(p.relations)
cl = sccs(rels)
print(f"circular clusters {len(cl)}   activities caught in them "
      f"{sum(len(c) for c in cl)}")
cut, rounds = [], 0
while True:
    cl = sccs(rels)
    if not cl:
        break
    rounds += 1
    if rounds > 200:
        print("  stopping: too many rounds")
        break
    worst = None
    for comp in cl:
        for r in rels:
            if r.predecessor_uid in comp and r.successor_uid in comp:
                a, b = byu[r.predecessor_uid], byu[r.successor_uid]
                fa, sb = D(a.planned_finish), D(b.planned_start)
                if not fa or not sb:
                    continue
                back = (fa - sb).days
                if worst is None or back > worst[0]:
                    worst = (back, r, a, b)
    if worst is None:
        # no dated edge to judge; cut any edge in the first cluster
        comp = cl[0]
        r = next(r for r in rels
                 if r.predecessor_uid in comp and r.successor_uid in comp)
        worst = (0, r, byu[r.predecessor_uid], byu[r.successor_uid])
    back, r, a, b = worst
    rels.remove(r)
    cut.append((back, a, b, r))
print(f"\nrelationships cut to make the schedule calculable: {len(cut)}")
print(f"{'days back':>10}  {'predecessor':<26}{'':<36}{'successor':<26}")
for back, a, b, r in sorted(cut, key=lambda x: -x[0]):
    print(f"{back:>8}d   {str(a.activity_id):<26}{str(a.name)[:34]:<36}"
          f"-> {str(b.activity_id):<26}{str(b.name)[:30]}")
    print(f"{'':>12}{r.type}, pred {D(a.planned_start)}..{D(a.planned_finish)}"
          f"   succ {D(b.planned_start)}..{D(b.planned_finish)}"
          f"   [{path(a.wbs_uid)[:46]}]")
by = collections.Counter(f"{str(a.name)[:34]} -> {str(b.name)[:30]}"
                         for _, a, b, _ in cut)
print(f"\ncut by kind:")
for k, v in by.most_common():
    print(f"   {v:>4}  {k}")
p.relations = rels

# ===== 2. DATA DATE ===================================================
print("\n" + "=" * 78)
print("2. DATA DATE")
print("=" * 78)
af = [D(a.actual_finish) for a in p.activities if D(a.actual_finish)]
asd = [D(a.actual_start) for a in p.activities if D(a.actual_start)]
print(f"moving {OLD_DD} -> {NEW_DD}")
print(f"latest actual start {max(asd) if asd else '-'}   "
      f"latest actual finish {max(af) if af else '-'}")
print(f"actuals dated after the new data date: "
      f"starts {sum(1 for x in asd if x > NEW_DD)}  "
      f"finishes {sum(1 for x in af if x > NEW_DD)}")
p.data_date = NEW_DD.isoformat()
newly = [a for a in p.activities if a.status == "Not Started"
         and D(a.planned_start) and OLD_DD <= D(a.planned_start) < NEW_DD]
run = [a for a in p.activities if a.status == "In Progress"
       and D(a.planned_finish) and OLD_DD <= D(a.planned_finish) < NEW_DD]
print(f"\nwork the month exposes, now needing a status call before F9:")
print(f"   not started, should have begun in October: {len(newly)}")
print(f"   in progress, should have finished in October: {len(run)}")

# ===== verify =========================================================
alive = set(byu)
left = sccs(rels)
ind = collections.Counter()
s2 = collections.defaultdict(list)
for r in rels:
    ind[r.successor_uid] += 1
    s2[r.predecessor_uid].append(r.successor_uid)
q = collections.deque(u for u in alive if not ind[u])
i2, n = dict(ind), 0
while q:
    u = q.popleft()
    n += 1
    for v in s2[u]:
        i2[v] -= 1
        if not i2[v]:
            q.append(v)
adj = collections.defaultdict(set)
for r in rels:
    adj[r.predecessor_uid].add(r.successor_uid)
    adj[r.successor_uid].add(r.predecessor_uid)
seen, comp, big = set(), 0, 0
for u in alive:
    if u in seen:
        continue
    comp += 1
    c, qq = 0, collections.deque([u])
    seen.add(u)
    while qq:
        x = qq.popleft()
        c += 1
        for y in adj[x] - seen:
            seen.add(y)
            qq.append(y)
    big = max(big, c)
pr = {r.successor_uid for r in rels}
su = {r.predecessor_uid for r in rels}
print("\n" + "=" * 78)
print(f"{'':<40}{'before':>10}{'after':>10}")
print(f"{'activities':<40}{A0:>10}{len(p.activities):>10}")
print(f"{'relationships':<40}{R0:>10}{len(rels):>10}")
print(f"{'circular clusters':<40}{24:>10}{len(left):>10}")
print(f"{'activities that cannot be ordered':<40}{303:>10}{len(alive) - n:>10}")
print(f"{'islands':<40}{6:>10}{comp:>10}")
print(f"{'largest network':<40}{'97%':>10}{f'{100 * big // len(alive)}%':>10}")
print(f"{'no predecessor':<40}{175:>10}"
      f"{sum(1 for a in p.activities if a.uid not in pr):>10}")
print(f"{'no successor':<40}{26:>10}"
      f"{sum(1 for a in p.activities if a.uid not in su):>10}")
print(f"{'data date':<40}{str(OLD_DD):>10}{str(NEW_DD):>10}")
print(f"\nstatus: " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
    a.status for a in p.activities).most_common()))
mx = max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()),
         default=0)
print(f"longest UDF value {mx}  (P6 rejects over 255)")
out = f"{SCR}/MDC1_logic_repaired_v2.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
