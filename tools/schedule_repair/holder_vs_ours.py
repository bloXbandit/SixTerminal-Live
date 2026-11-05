"""How much of Holder's 11/04 MDC-3 export lands on our file, and what it gives us.

The question is whether this export can become the spine -- so that from here
on the job is statusing rather than rebuilding logic. That turns on four
measurements:

  COVERAGE   how many of our activities exist in Holder's file, by id.
  LOGIC      how many of Holder's relationships land on two activities we
             have, how many of those we already carry, and how many are new.
  DATES      where ids match, do the dates agree.
  ACTUALS    Holder marks 104 finished and 12 in progress. Do those agree with
             what the field told us, and do they give us statusing we lack.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
H = json.load(open(f"{SCR}/work/holder_mdc3.json"))
hact = {a["id"]: a for a in H["acts"]}
hpairs = {(a, b) for a, b, t, l in H["pairs"]}

p = load_xml(f"{SCR}/MDC3_logic_repaired_v22.xml")
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}
dd = dt.date.fromisoformat(str(p.data_date)[:10])


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


print("=" * 74)
print("1. COVERAGE")
print("=" * 74)
oset, hset = set(ours), set(hact)
both = oset & hset
print(f"our file          {len(oset):>6} activities")
print(f"Holder's export   {len(hset):>6} activities")
print(f"shared id         {len(both):>6}   "
      f"({100 * len(both) // len(oset)}% of ours, "
      f"{100 * len(both) // len(hset)}% of theirs)")
print(f"ours only         {len(oset - hset):>6}")
print(f"theirs only       {len(hset - oset):>6}")
print("\nours only, by id prefix (top 12):")
for k, v in collections.Counter(
        ".".join(a.split(".")[:3]) for a in (oset - hset)).most_common(12):
    print(f"   {v:>5}  {k}")
print("\ntheirs only, by id prefix (top 14):")
for k, v in collections.Counter(
        ".".join(a.split(".")[:3]) for a in (hset - oset)).most_common(14):
    print(f"   {v:>5}  {k}")

print("\n" + "=" * 74)
print("2. LOGIC")
print("=" * 74)
opairs = {(str(byu[r.predecessor_uid].activity_id).strip(),
           str(byu[r.successor_uid].activity_id).strip())
          for r in p.relations
          if r.predecessor_uid in byu and r.successor_uid in byu}
usable = {(a, b) for a, b in hpairs if a in ours and b in ours}
print(f"Holder relationships            {len(hpairs):>6}")
print(f"   both ends exist in our file  {len(usable):>6}")
print(f"   of those, we already have    {len(usable & opairs):>6}")
print(f"   NEW to us                    {len(usable - opairs):>6}")
print(f"\nour relationships               {len(opairs):>6}")
print(f"   Holder also states           {len(opairs & hpairs):>6}")
print(f"   ours only (addressable)      "
      f"{len({(a, b) for a, b in opairs - hpairs if a in hact and b in hact}):>6}")
rev = {(b, a) for a, b in hpairs}
print(f"   running OPPOSITE to Holder   {len(opairs & rev):>6}")
print(f"\nwhere the new ties would land (top 12 by successor prefix):")
for k, v in collections.Counter(
        ".".join(b.split(".")[:3]) for a, b in (usable - opairs)).most_common(12):
    print(f"   {v:>5}  {k}")
# what it does to our open ends
osucc = {b for a, b in opairs}
opred = {a for a, b in opairs}
openp = [a for a in ours if a not in osucc]
after = {b for a, b in (opairs | usable)}
print(f"\nour activities with no predecessor: {len(openp)}")
print(f"   of those, Holder gives a predecessor: "
      f"{sum(1 for a in openp if a in after)}")
print(f"   would remain open: {sum(1 for a in openp if a not in after)}")

print("\n" + "=" * 74)
print("3. DATES, where ids match")
print("=" * 74)
d = []
for i in sorted(both):
    o, h = ours[i], hact[i]
    os_, hs = D(o.planned_start), D(h["start"])
    if os_ and hs:
        d.append((i, (os_ - hs).days))
if d:
    v = sorted(x[1] for x in d)
    print(f"comparable: {len(d)}")
    print(f"   identical            {sum(1 for _, x in d if x == 0):>6}")
    print(f"   within 7 days        {sum(1 for _, x in d if abs(x) <= 7):>6}")
    print(f"   ours EARLIER >7d     {sum(1 for _, x in d if x < -7):>6}")
    print(f"   ours LATER >7d       {sum(1 for _, x in d if x > 7):>6}")
    print(f"   median {v[len(v) // 2]:+d}d   range {v[0]:+d} .. {v[-1]:+d}")

print("\n" + "=" * 74)
print("4. ACTUALS Holder reports")
print("=" * 74)
hdone = [a for a in hact.values() if a["finished"]]
hprog = [a for a in hact.values() if a["started"] and not a["finished"]]
print(f"Holder finished: {len(hdone)}   in progress: {len(hprog)}")
m = [a for a in hdone if a["id"] in ours]
print(f"   finished ones we also have: {len(m)}")
agree = [a for a in m if ours[a["id"]].status == "Completed"]
newdone = [a for a in m if ours[a["id"]].status != "Completed"]
print(f"      we already call Completed : {len(agree)}")
print(f"      we do NOT                 : {len(newdone)}")
for a in sorted(newdone, key=lambda a: str(a["finish"]))[:24]:
    print(f"         {a['id']:<24}{a['name'][:40]:<42}fin {a['finish']}  "
          f"ours={ours[a['id']].status}")
mp = [a for a in hprog if a["id"] in ours]
print(f"\n   in-progress ones we also have: {len(mp)}")
for a in mp:
    print(f"         {a['id']:<24}{a['name'][:40]:<42}"
          f"started {a['start']}  ours={ours[a['id']].status}")
hd = max((D(a["finish"]) for a in hdone if D(a["finish"])), default=None)
print(f"\nHolder's implied data date {hd}   ours {dd}")
