"""Parse the GC's printed MDC-1 Exhibit S (11/03/2026).

The pages are rotated 90 degrees, which is what defeated the first attempt:
in the extracted word coordinates x runs DOWN the printed rows and y runs
ACROSS the printed columns, decreasing left to right. So activities are
grouped by x, and each word's column is decided by its y:

    Activity ID      y > 1050
    Activity Name    750 < y <= 1050
    Duration         700 < y <= 750
    Start            650 < y <= 700
    Finish                 y <= 650

A name wraps over several y values and has to be read in descending y order.
A date is followed by a separate "A" token when it is an actual.

The print carries no predecessor or successor columns, so what comes out is
progress and dates, not logic.
"""
import collections
import datetime as dt
import json
import re
import sys

import fitz

MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
AID = re.compile(r"^(MDC1\.[A-Z0-9][A-Za-z0-9.\-]*|A\d{3,6})$")
DATE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})$")


def pdate(tok):
    m = DATE.match(tok.strip())
    if not m:
        return None
    return dt.date(2000 + int(m.group(3)), MON[m.group(2).title()],
                   int(m.group(1)))


doc = fitz.open(sys.argv[1])
acts = {}
rowcount = 0
for pno in range(doc.page_count):
    words = doc[pno].get_text("words")
    if not words:
        continue
    rows = collections.defaultdict(list)
    for w in words:
        rows[round(w[0] / 4)].append(w)
    for key in sorted(rows):
        ws = rows[key]
        ids = [w for w in ws if w[1] > 1050 and AID.match(w[4])]
        if not ids:
            continue
        rowcount += 1
        aid = ids[0][4]
        name = " ".join(w[4] for w in
                        sorted([w for w in ws if 750 < w[1] <= 1050],
                               key=lambda w: -w[1]))
        name = re.sub(r"\s+-\s+", " - ", name).strip()
        dur = None
        for w in ws:
            if 690 < w[1] <= 790 and re.fullmatch(r"\d+", w[4]):
                dur = int(w[4])
                break
        st = fi = None
        sa = fa = False
        for w in ws:
            d = pdate(w[4])
            if d is None:
                continue
            flag = any(x[4] == "A" and abs(x[1] - w[1]) < 14 and x[0] == w[0]
                       for x in ws)
            if 640 < w[1] <= 700:
                st, sa = d, flag
            elif w[1] <= 640:
                fi, fa = d, flag
        if aid in acts and (acts[aid]["start"] or acts[aid]["finish"]):
            continue
        acts[aid] = {"id": aid, "name": name, "dur": dur, "start": st,
                     "finish": fi, "started": sa, "finished": fa}

print(f"pages {doc.page_count}   rows with an activity id {rowcount}")
print(f"activities parsed: {len(acts)}")
named = sum(1 for a in acts.values() if a["name"])
dated = sum(1 for a in acts.values() if a["start"] or a["finish"])
print(f"   with a name {named}   with a date {dated}")
done = [a for a in acts.values() if a["finished"]]
prog = [a for a in acts.values() if a["started"] and not a["finished"]]
nots = [a for a in acts.values() if not a["started"]]
print(f"   finished {len(done)}   in progress {len(prog)}   not started {len(nots)}")
ds = sorted(a["finish"] for a in done if a["finish"])
ss = sorted(a["start"] for a in acts.values() if a["started"] and a["start"])
if ds:
    print(f"   actual finishes {ds[0]} .. {ds[-1]}")
if ss:
    print(f"   actual starts   {ss[0]} .. {ss[-1]}")
C = dt.date(2026, 9, 30)
print(f"\nACTUALS AFTER 09/30/2026:  finishes "
      f"{sum(1 for d in ds if d > C)}   starts {sum(1 for d in ss if d > C)}")
print(f"\nsample:")
for a in list(acts.values())[:10]:
    print(f"   {a['id']:<24}{str(a['name'])[:40]:<42}{str(a['dur']):>4}  "
          f"{a['start']}{' A' if a['started'] else '  '} .. "
          f"{a['finish']}{' A' if a['finished'] else ''}")
json.dump([{**a, "start": str(a["start"]), "finish": str(a["finish"])}
           for a in acts.values()], open(sys.argv[2], "w"), indent=1,
          default=str)
print(f"\nwrote {sys.argv[2]}")
