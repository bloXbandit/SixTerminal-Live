"""The 19 rows whose Holder date starts before one of our predecessors
finishes. For each: what is the tie, where did it come from, and which side
is more likely right?"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
byu = {a.uid: a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
NEW = re.compile(r"^MDC1\.PH\d\.(ERR20[12]|HVR101|GAL(101|102|202))\.")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


DD = D(p.data_date)
pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)

# stage order a tradesman would expect in a room
STAGE = ["high steel", "hanger", "box pen", "layout wall", "oh electrical",
         "overhead rough in", "oh rough", "in-wall rough", "wall rough",
         "drywall", "in-wall inspect", "in wall inspect", "wall close",
         "conduit", "cable tray", "tray", "bus duct", "branch power",
         "fanwal feeder", "edp/cdu", "set hvs", "set misc", "set crah",
         "set crac", "set electrical skid", "electrical pulls", "pull",
         "electrical termination", "term", "hang tape", "prime", "paint",
         "floor finish", "caulk", "light", "vesda", "device trim",
         "trim out", "mechanical final", "testing", "neta", "inspection",
         "start up", "startup"]


def stage(n):
    s = str(n).lower()
    for i, k in enumerate(STAGE):
        if k in s:
            return i, k
    return 999, "?"


rows = []
for a in p.activities:
    if not NEW.search(str(a.activity_id)):
        continue
    es = D(a.early_start)
    if not es:
        continue
    for r in pred[a.uid]:
        q = byu.get(r.predecessor_uid)
        if not q:
            continue
        qf = D(q.early_finish) or D(q.actual_finish)
        if qf and es < qf:
            rows.append((a, q, r, es, qf))

print(f"conflicts: {len(rows)}")
print("=" * 100)
verdict = collections.Counter()
for a, q, r, es, qf in sorted(rows, key=lambda x: -(x[4] - x[3]).days):
    si, sk = stage(a.name)
    qi, qk = stage(q.name)
    external = not NEW.search(str(q.activity_id))
    gap = (qf - es).days
    # which side looks right?
    if si < qi:
        v = ("OUR TIE IS BACKWARDS", f"{sk} normally precedes {qk}")
    elif external:
        v = ("LOGIC WINS", "fed from outside the room; the GC date ignores it")
    else:
        v = ("LOGIC WINS", f"{qk} genuinely precedes {sk}")
    verdict[v[0]] += 1
    print(f"\n{str(a.activity_id):<24}{str(a.name)[:42]:<44}starts {es}")
    print(f"   after {str(q.activity_id):<22}{str(q.name)[:40]:<42}"
          f"finishes {qf}   gap {gap}d  {r.type}")
    print(f"   stages: {qk} (#{qi})  ->  {sk} (#{si})"
          f"{'   [external predecessor]' if external else ''}")
    print(f"   -> {v[0]}: {v[1]}")
print("\n" + "=" * 100)
for k, v in verdict.most_common():
    print(f"   {v:>4}  {k}")
