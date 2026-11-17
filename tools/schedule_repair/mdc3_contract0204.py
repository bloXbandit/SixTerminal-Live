"""Parse the MDC-3 02/04/2026 Current Contract Exhibit S into structured rows.

This is the baseline the acceleration analysis is measured against, so it is
parsed, not read off the page.

The pages are rotated ninety degrees, the same as MDC-1's Exhibit S. In the
text layer that means x is the ROW and y is the COLUMN, with y descending
left to right. The header words give the column bands:

    Activity ID               y about 1164-1170
    Activity Name             y about 1040-1056
    At Completion Duration    y about  896-924
    Start                     y about  882
    Finish                    y about  842

so a row is every word sharing an x, split into fields by which y band it
falls in. Below the Finish band is the Gantt, which is dropped.

P6's own markers are kept: a trailing A on a date is an actual, a trailing *
is a constrained date. Both matter for the comparison.
"""
import collections
import datetime as dt
import json
import re
import sys

import pymupdf

SRC, OUT = sys.argv[1], sys.argv[2]
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
# A marker may be its own token or glued to the date. The three Substantial
# Completion milestones -- the contract dates the whole analysis turns on --
# print as "11-Oct-27*" in one piece, so a strict match silently drops them.
DATE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{2})([A*]?)$")
AID = re.compile(r"^MDC3?\.[A-Z0-9][A-Z0-9.]*\d$")
# column bands, as (low, high]
BAND_ID = (1100, 1300)
BAND_NAME = (935, 1100)
BAND_DUR = (890, 935)
BAND_START = (855, 890)
BAND_FIN = (815, 855)


def pdate(s):
    m = DATE.match(s)
    if not m:
        return None
    mo = MON.get(m.group(2).title())
    if not mo:
        return None
    return dt.date(2000 + int(m.group(3)), mo, int(m.group(1)))


doc = pymupdf.open(SRC)
rows = []
for pno in range(doc.page_count):
    words = doc[pno].get_text("words")
    if not words:
        continue
    byrow = collections.defaultdict(list)
    for w in words:
        byrow[round(w[0] * 2) / 2].append(w)
    # merge rows whose x are within 1.5 of each other
    keys = sorted(byrow)
    merged, cur = [], []
    for k in keys:
        if cur and k - cur[-1] <= 1.5:
            cur.append(k)
        else:
            if cur:
                merged.append(cur)
            cur = [k]
    if cur:
        merged.append(cur)
    for grp in merged:
        ws = [w for k in grp for w in byrow[k]]
        ws.sort(key=lambda w: -w[1])          # y descending = left to right

        def band(lo, hi):
            return [w[4] for w in ws if lo < w[1] <= hi]

        ids = [t for t in band(*BAND_ID) if AID.match(t)]
        if not ids:
            continue
        name = " ".join(band(*BAND_NAME)).strip()
        dur = None
        for t in band(*BAND_DUR):
            if re.fullmatch(r"\d{1,4}", t):
                dur = int(t)
                break

        def datefield(lo, hi):
            d, flag = None, ""
            for t in band(lo, hi):
                m = DATE.match(t)
                if m and d is None:
                    d = pdate(t)
                    flag += m.group(4)
                elif t in ("A", "*") and d is not None:
                    flag += t
            return d, flag

        s, sf = datefield(*BAND_START)
        f, ff = datefield(*BAND_FIN)
        rows.append({"page": pno + 1, "id": ids[0], "name": name, "dur": dur,
                     "start": s, "start_flag": sf,
                     "finish": f, "finish_flag": ff})
doc.close()

print(f"activity rows parsed : {len(rows)}")
print(f"unique activity ids  : {len({r['id'] for r in rows})}")
ds = [r["start"] for r in rows if r["start"]] + \
     [r["finish"] for r in rows if r["finish"]]
print(f"date range           : {min(ds)} .. {max(ds)}")
print(f"with a duration      : {sum(1 for r in rows if r['dur'] is not None)}")
print(f"with a start         : {sum(1 for r in rows if r['start'])}")
print(f"with a finish        : {sum(1 for r in rows if r['finish'])}")
print(f"actual-flagged (A)   : "
      f"{sum(1 for r in rows if 'A' in r['start_flag'] + r['finish_flag'])}")
print(f"constrained (*)      : "
      f"{sum(1 for r in rows if '*' in r['start_flag'] + r['finish_flag'])}")
print(f"blank names          : {sum(1 for r in rows if not r['name'])}")
pre = collections.Counter(".".join(r["id"].split(".")[:3]) for r in rows)
print(f"\nby id prefix (top 20):")
for k, v in pre.most_common(20):
    print(f"   {v:>5}  {k}")
print("\nsample rows:")
for r in rows[:6] + rows[len(rows) // 2:len(rows) // 2 + 4]:
    print(f"   p{r['page']:<3}{r['id']:<26}{r['name'][:40]:<42}"
          f"{str(r['dur'] or ''):>5}  {r['start']}{r['start_flag']:<2} "
          f"{r['finish']}{r['finish_flag']}")
json.dump([{**r,
            "start": r["start"].isoformat() if r["start"] else None,
            "finish": r["finish"].isoformat() if r["finish"] else None}
           for r in rows], open(OUT, "w"), indent=0)
print(f"\nwrote {OUT}")
