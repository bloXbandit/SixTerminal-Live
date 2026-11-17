"""Parse the MDC3 Activity Schedule dated 25-Sep-26 -- it has a real text layer.

Unlike the 10/07 Exhibit S, this print carries embedded fonts and real text,
and its pages are NOT rotated, so it parses conventionally: y is the row, x
is the column.

    Activity ID              x  38 .. 157
    Activity Name            x 157 .. 345
    At Completion Duration   x 345 .. 384
    Start                    x 384 .. 426
    Finish                   x 426 .. 480
    beyond 480               the Gantt, dropped

Two wrinkles. A WBS summary row draws its name twice at the same position
(fill then outline), so tokens repeat and have to be de-duplicated. And P6's
actual marker "A" is its own token here rather than glued to the date, which
is the opposite of the 02/04 Exhibit S.

This matters as an independent check: it shares a data date with the
proposed Exhibit S, so where the two carry the same scope the band dates can
be compared against text instead of against my reading of a picture.
"""
import collections
import datetime as dt
import json
import re
import sys

import pymupdf

SRC, OUT = sys.argv[1], sys.argv[2]
# The name column starts at 157.0 and tokens land a hair short of it, so a
# `157 <= x` test silently drops the FIRST word of every activity name --
# "Mobilize" vanished entirely and "Building Pad : 1st Half" became "Pad :
# 1st Half". The boundary goes at 150, below the lowest name token and above
# the highest id token.
COL_ID = (36, 150)
COL_NAME = (150, 345)
# A WBS band does not use the name column at all: it prints its title in the
# left column, indented by level, so it has to be read from there instead.
COL_WBS_NAME = (36, 345)
COL_DUR = (345, 384)
COL_START = (384, 426)
COL_FIN = (426, 480)
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
DATE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{2})$")
AID = re.compile(r"^MDC3?\.[A-Z0-9][A-Z0-9.]*\d$")


def pdate(t):
    m = DATE.match(t)
    if not m:
        return None
    mo = MON.get(m.group(2).title())
    if not mo:
        return None
    try:
        return dt.date(2000 + int(m.group(3)), mo, int(m.group(1)))
    except ValueError:
        return None


doc = pymupdf.open(SRC)
rows = []
for pno in range(doc.page_count):
    ws = doc[pno].get_text("words")
    if not ws:
        continue
    by = collections.defaultdict(list)
    for w in ws:
        by[round(w[1] * 2) / 2].append(w)
    keys = sorted(by)
    merged, cur = [], []
    for k in keys:                       # a row's tokens can sit 1pt apart
        if cur and k - cur[-1] <= 1.5:
            cur.append(k)
        else:
            if cur:
                merged.append(cur)
            cur = [k]
    if cur:
        merged.append(cur)

    for grp in merged:
        toks = [w for k in grp for w in by[k]]
        toks.sort(key=lambda w: w[0])

        def col(lo, hi):
            out, seen = [], set()
            for w in toks:
                if lo <= w[0] < hi:
                    key = (round(w[0], 1), w[4])
                    if key in seen:      # WBS rows draw their text twice
                        continue
                    seen.add(key)
                    out.append(w[4])
            return out

        ids = [t for t in col(*COL_ID) if AID.match(t)]
        if ids:
            name_toks = col(*COL_NAME)
        else:                            # a WBS band titles itself on the left
            name_toks = [t for t in col(*COL_WBS_NAME) if not AID.match(t)]
        # a WBS band repeats its whole name; collapse an exact doubling
        half = len(name_toks) // 2
        if half and name_toks[:half] == name_toks[half:]:
            name_toks = name_toks[:half]
        name = " ".join(name_toks).strip()

        dur = None
        for t in col(*COL_DUR):
            if re.fullmatch(r"\d{1,4}", t):
                dur = int(t)
                break

        def datecol(lo, hi):
            d, flag = None, ""
            for t in col(lo, hi):
                if d is None:
                    d = pdate(t)
                elif t == "A":
                    flag = "A"
            return d, flag

        s, sf = datecol(*COL_START)
        f, ff = datecol(*COL_FIN)
        if not ids and not (name and dur is not None):
            continue
        rows.append({"page": pno + 1, "id": ids[0] if ids else None,
                     "name": name, "dur": dur,
                     "start": s.isoformat() if s else None, "start_a": sf,
                     "finish": f.isoformat() if f else None, "finish_a": ff,
                     "kind": "activity" if ids else "wbs"})
doc.close()

acts = [r for r in rows if r["kind"] == "activity"]
wbs = [r for r in rows if r["kind"] == "wbs"]
print(f"rows parsed      : {len(rows)}")
print(f"   activities    : {len(acts)}   ({len({r['id'] for r in acts})} "
      f"unique ids)")
print(f"   WBS bands     : {len(wbs)}")
print(f"with a duration  : {sum(1 for r in rows if r['dur'] is not None)}")
print(f"with a start     : {sum(1 for r in rows if r['start'])}")
print(f"with a finish    : {sum(1 for r in rows if r['finish'])}")
print(f"actual-flagged   : "
      f"{sum(1 for r in rows if r['start_a'] or r['finish_a'])}")
ds = [r["start"] for r in rows if r["start"]] + \
     [r["finish"] for r in rows if r["finish"]]
print(f"date range       : {min(ds)} .. {max(ds)}")
pre = collections.Counter(".".join(r["id"].split(".")[:3])
                          for r in acts if r["id"])
print(f"\nby id prefix (top 14):")
for k, v in pre.most_common(14):
    print(f"   {v:>5}  {k}")
print("\nsample activities:")
for r in acts[:4] + acts[len(acts) // 2:len(acts) // 2 + 3]:
    print(f"   p{r['page']:<4}{r['id']:<24}{r['name'][:38]:<40}"
          f"{str(r['dur'] or ''):>5}  {r['start']}{r['start_a']} .. "
          f"{r['finish']}{r['finish_a']}")
json.dump(rows, open(OUT, "w"), indent=0)
print(f"\nwrote {OUT}")
