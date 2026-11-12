"""What is holding Level 3 commissioning back, phase by phase?

Walks the driving chain back from each phase's Level 3 Commissioning Start
and reports what sits on it, so the gap against Holder can be attributed to
specific work rather than to the calendar.
"""
import collections
import copy
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import compute_dates
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
R = json.load(open(f"{SCR}/work/mdc1_exs2.json"))
hid = {str(d["Activity ID"]): d for d in R}
p = load_xml(f"{SCR}/MDC1_logic_repaired_v28.xml")
compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
byu = {a.uid: a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}


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

TARGET = {"PH1": "MDC1.MIL.PH1.1000", "PH2": "MDC1.MIL.PH2.9000",
          "PH3": "MDC1.MIL.PH3.9000"}
byi = {str(a.activity_id): a for a in p.activities}

for ph, tid in TARGET.items():
    a = byi.get(tid)
    if a is None:
        continue
    h = hid.get(tid)
    ht = (D(h.get("Finish")) or D(h.get("Start"))) if h else None
    ours = D(a.early_start)
    print("=" * 100)
    print(f"{ph}  Level 3 Commissioning Start -- ours {ours}, "
          f"Holder {ht}, {(ours - ht).days:+d} days")
    print("=" * 100)
    chain, cur, seen = [], a, set()
    while cur and cur.uid not in seen:
        seen.add(cur.uid)
        chain.append(cur)
        ps = [byu[r.predecessor_uid] for r in pred[cur.uid]
              if r.predecessor_uid in byu and D(byu[r.predecessor_uid].early_finish)]
        if not ps:
            break
        cur = max(ps, key=lambda q: D(q.early_finish))
    chain.reverse()
    print(f"driving chain: {len(chain)} activities")
    print(f"{'activity':<24}{'name':<44}{'dur':>5}{'start':>12}{'finish':>12}"
          f"{'status':>13}")
    for x in chain:
        print(f"{str(x.activity_id):<24}{str(x.name)[:42]:<44}"
              f"{(x.planned_duration or 0) / 8:>5,.0f}{str(D(x.early_start)):>12}"
              f"{str(D(x.early_finish)):>12}{x.status:>13}")
    tot = sum((x.planned_duration or 0) / 8 for x in chain)
    span = (D(chain[-1].early_finish) - max(D(chain[0].early_start), DD)).days
    print(f"\nduration on the chain {tot:,.0f} working days; "
          f"it occupies {span} calendar days from the data date")
    # where is that chain?
    w = collections.Counter(path(x.wbs_uid).split(" / ")[0] for x in chain)
    print("chain sits in: " + ", ".join(f"{k} x{v}" for k, v in w.most_common(5)))
    # how much of the chain does Holder also carry, and when does he have it?
    both = [(x, hid[str(x.activity_id)]) for x in chain
            if str(x.activity_id) in hid]
    print(f"\n{len(both)} of the {len(chain)} are in Holder's file too:")
    print(f"{'activity':<24}{'name':<40}{'ours fin':>12}{'his fin':>12}{'diff':>7}")
    for x, d in both:
        t = D(d.get("Finish"))
        o = D(x.early_finish)
        print(f"{str(x.activity_id):<24}{str(x.name)[:38]:<40}{str(o):>12}"
              f"{str(t or '-'):>12}"
              f"{((o - t).days if o and t else 0):>+7}")
    print()
