"""The activities that have to match Holder: milestones and commissioning."""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
R = json.load(open(f"{SCR}/work/mdc1_exs2.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v27.xml")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


MAJOR = re.compile(
    r"substantial completion|certificate of occupancy|\bTCO\b|"
    r"level \d commissioning|commissioning (start|complete)|"
    r"energiz|owner acceptance|turnover|ready for|"
    r"level \d cx|functional test|integrated system", re.I)

hid = {str(d["Activity ID"]): d for d in R}
hname = collections.defaultdict(list)
for d in R:
    hname[re.sub(r"\s+", " ", str(d["Activity Name"])).strip().lower()].append(d)

ours = [a for a in p.activities if MAJOR.search(str(a.name))]
print(f"our major milestone / commissioning activities: {len(ours)}")
print("=" * 108)
print(f"{'activity':<22}{'name':<44}{'ours':>12}{'Holder':>12}{'diff':>8}"
      f"  match")
print("=" * 108)
rows = []
for a in sorted(ours, key=lambda x: D(x.early_finish) or dt.date(2030, 1, 1)):
    o = D(a.early_finish) or D(a.planned_finish)
    nm = re.sub(r"\s+", " ", str(a.name)).strip()
    d = hid.get(str(a.activity_id))
    how = "by id"
    if d is None:
        c = hname.get(nm.lower())
        d, how = (c[0], "by name") if c else (None, "NO MATCH")
    t = D(d.get("Finish")) if d else None
    diff = (o - t).days if o and t else None
    rows.append((a, d, o, t, diff, how))
    print(f"{str(a.activity_id):<22}{nm[:42]:<44}{str(o):>12}{str(t or '-'):>12}"
          f"{(f'{diff:+d}' if diff is not None else '-'):>8}  {how}")

print("\n" + "=" * 108)
miss = [r for r in rows if r[5] == "NO MATCH"]
off = [r for r in rows if r[4] is not None and abs(r[4]) > 5]
print(f"matched to Holder : {len(rows) - len(miss)} of {len(rows)}")
print(f"more than 5d apart: {len(off)}")
print(f"no Holder match   : {len(miss)}")
if off:
    print("\nTHE ONES THAT ARE OFF:")
    for a, d, o, t, diff, how in sorted(off, key=lambda x: -abs(x[4])):
        print(f"   {str(a.activity_id):<22}{str(a.name)[:40]:<42}"
              f"ours {o}  Holder {t}  {diff:+d}d")
if miss:
    print("\nNO HOLDER EQUIVALENT (name differs or not in his file):")
    for a, d, o, t, diff, how in miss:
        print(f"   {str(a.activity_id):<22}{str(a.name)[:48]:<50}{o}")

print("\n" + "=" * 108)
print("HOLDER'S MAJOR ONES WE MAY NOT CARRY")
print("=" * 108)
oid = {str(a.activity_id) for a in p.activities}
oname = {re.sub(r"\s+", " ", str(a.name)).strip().lower()
         for a in p.activities}
hmaj = [d for d in R if MAJOR.search(str(d["Activity Name"]))]
gap = [d for d in hmaj if str(d["Activity ID"]) not in oid
       and re.sub(r"\s+", " ", str(d["Activity Name"])).strip().lower()
       not in oname]
print(f"his major activities: {len(hmaj)}   not matched on our side: {len(gap)}")
k = collections.Counter(re.sub(r"[-:].*$|\(.*?\)", "", str(d["Activity Name"])).strip()
                        for d in gap)
for n, v in k.most_common(12):
    print(f"   {v:>5}  {n[:70]}")
