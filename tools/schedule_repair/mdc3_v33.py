"""MDC-3 v33: import the logic Holder's 11/04 export carries, and close the
worst of the open ends.

Three things, in the order they have to happen.

1. The 11/04 export names 15,821 predecessor ties. 1,668 of them join two
   activities we carry and are absent from our file. They were never
   imported -- that export was used for actuals only -- which is most of why
   v32 runs at 1.44 relationships per activity where a healthy file runs
   past 2.

   It names them as bare ids: no type, no lag. Importing them all as FS-0
   would be wrong for a seventh of them, because 122 have a successor that
   already starts before its predecessor finishes. So the type is inferred
   from the geometry the pair already has (mdc3_logic_infer.py) and the ones
   that need more than 30 days of lag under every type are dropped rather
   than guessed at.

2. Adding those ties gives many orphans a predecessor for free, so the open
   ends are re-counted AFTER the import, not before.

3. Ten pairs read out of sequence. Five are crew flow between pours, which
   is correct by trade and left alone; the rest are status disagreements
   that logic cannot fix, so they are reported for the field rather than
   papered over.

Nothing existing is removed, per the standing rule: this only adds.
"""
import collections
import datetime as dt
import json
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402
from engine.xml_writer import write_p6_xml                  # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
HRS = 8.0                      # the file stores duration and lag in hours
TYPE = {"FS": "Finish to Start", "SS": "Start to Start",
        "FF": "Finish to Finish"}

p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
ties = json.load(open(f"{SCR}/work/mdc3_new_ties.json"))
ours = {str(a.activity_id): a for a in p.activities}
byu = {a.uid: str(a.activity_id) for a in p.activities}


def openends(proj):
    pre = {r.successor_uid for r in proj.relations}
    suc = {r.predecessor_uid for r in proj.relations}
    nopre = [a for a in proj.activities if a.uid not in pre]
    nosuc = [a for a in proj.activities if a.uid not in suc]
    ns = [a for a in nopre if a.status == "Not Started"]
    return nopre, nosuc, ns


b_pre, b_suc, b_ns = openends(p)
print("BEFORE")
print(f"   relations            : {len(p.relations):,}  "
      f"({len(p.relations) / len(p.activities):.2f} per activity)")
print(f"   no predecessor       : {len(b_pre)}")
print(f"   no successor         : {len(b_suc)}")
print(f"   Not Started, no pred : {len(b_ns)}")

# ---- 1. add the inferred ties ---------------------------------------
Rel = type(p.relations[0])
nxt = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
added = collections.Counter()
for t in ties:
    a, b = ours.get(t["pred"]), ours.get(t["succ"])
    if a is None or b is None:
        continue
    p.relations.append(Rel(uid=str(nxt), predecessor_uid=a.uid,
                           successor_uid=b.uid,
                           type=TYPE[t["type"]],
                           lag=float(t["lag"]) * HRS))
    nxt += 1
    added[t["type"]] += 1
print(f"\nties added from the 11/04 export: {sum(added.values())}  "
      f"{dict(added)}")

a_pre, a_suc, a_ns = openends(p)
print("\nAFTER THE IMPORT")
print(f"   relations            : {len(p.relations):,}  "
      f"({len(p.relations) / len(p.activities):.2f} per activity)")
print(f"   no predecessor       : {len(a_pre)}   "
      f"({len(a_pre) - len(b_pre):+})")
print(f"   no successor         : {len(a_suc)}   "
      f"({len(a_suc) - len(b_suc):+})")
print(f"   Not Started, no pred : {len(a_ns)}   "
      f"({len(a_ns) - len(b_ns):+})")

# ---- 2. what is left with no predecessor and not started -------------
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


print("\nNOT STARTED WITH NO PREDECESSOR -- these float to the data date "
      "on F9")
if a_ns:
    c = collections.Counter(path(x.wbs_uid).split(" / ")[0] for x in a_ns)
    for k, v in c.most_common(10):
        print(f"   {v:>4}  {k[:62]}")
    print("   examples:")
    for x in a_ns[:8]:
        print(f"      {str(x.activity_id):<26}{str(x.name)[:46]}")

# ---- 3. out of sequence ---------------------------------------------
byu2 = {a.uid: a for a in p.activities}
oos = [(byu2[r.predecessor_uid], byu2[r.successor_uid], r)
       for r in p.relations
       if byu2.get(r.predecessor_uid) and byu2.get(r.successor_uid)
       and byu2[r.predecessor_uid].status == "Not Started"
       and byu2[r.successor_uid].status in ("Completed", "In Progress")]
print(f"\nOUT OF SEQUENCE: {len(oos)} pairs")
for q, s, r in oos:
    print(f"   {str(q.activity_id):<24}{str(q.name)[:34]:<36}[{q.status}]")
    print(f"      -> {str(s.activity_id):<21}{str(s.name)[:34]:<36}"
          f"[{s.status}]  {r.type}")

print("\n" + "=" * 70)
print("CHECKS")
print("=" * 70)
seen = set()
dup = 0
for r in p.relations:
    k = (r.predecessor_uid, r.successor_uid)
    if k in seen:
        dup += 1
    seen.add(k)
print(f"   duplicate ties        : {dup}")
print(f"   self-referencing ties : "
      f"{sum(1 for r in p.relations if r.predecessor_uid == r.successor_uid)}")
adj = collections.defaultdict(set)
for r in p.relations:
    adj[r.predecessor_uid].add(r.successor_uid)
WHITE, GREY, BLACK = 0, 1, 2
col = collections.defaultdict(int)
loops = 0
for n in list(adj):
    if col[n] != WHITE:
        continue
    st = [(n, iter(adj[n]))]
    col[n] = GREY
    while st:
        u, it = st[-1]
        try:
            v = next(it)
            if col[v] == GREY:
                loops += 1
            elif col[v] == WHITE:
                col[v] = GREY
                st.append((v, iter(adj[v])))
        except StopIteration:
            col[u] = BLACK
            st.pop()
print(f"   circular logic        : {loops}")
D = (lambda v: dt.date.fromisoformat(str(v)[:10])
     if v and str(v)[:4].isdigit() else None)
dd = D(p.data_date)
print(f"   data date             : {dd}")
print(f"   actuals past it       : "
      f"{sum(1 for a in p.activities if (D(a.actual_finish) and D(a.actual_finish) > dd) or (D(a.actual_start) and D(a.actual_start) > dd))}")

out = f"{SCR}/MDC3_logic_repaired_v33.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
