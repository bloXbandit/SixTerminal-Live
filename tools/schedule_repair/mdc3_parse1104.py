"""Parse Holder's 11/04 MDC-3 export and see what it holds that we do not.

The Start and Finish columns carry P6's own marker: a trailing "A" means the
date is actual, anything else is a forecast. That is the whole point of this
file -- it is a month newer than the 10/07 PDF and it says what has actually
happened.
"""
import collections
import datetime as dt
import json
import re
import sys

import openpyxl

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = ("/root/.claude/uploads/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/"
       "b9d95984-Copy_of_2026.11.04_-_MDC3_Schedule.xlsx")

MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def parse_date(v):
    """returns (date, is_actual)"""
    if v is None:
        return None, False
    if isinstance(v, dt.datetime):
        return v.date(), False
    if isinstance(v, dt.date):
        return v, False
    s = str(v).strip()
    act = s.endswith(" A") or s.endswith("A")
    s2 = re.sub(r"\s*A$", "", s).strip()
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})$", s2)
    if m:
        d, mo, y = int(m.group(1)), MON.get(m.group(2).title()), int(m.group(3))
        if mo:
            return dt.date(2000 + y, mo, d), act
    try:
        return dt.date.fromisoformat(s2[:10]), act
    except ValueError:
        return None, act


wb = openpyxl.load_workbook(SRC, data_only=True, read_only=True)
ws = wb["MDC3 Schedule"]
rows = list(ws.iter_rows(values_only=True))
wb.close()
hdr = [str(c or "").strip() for c in rows[0]]
print(f"columns: {hdr}")
recs = []
for r in rows[1:]:
    d = dict(zip(hdr, r))
    aid = str(d.get("Activity ID") or "").strip()
    if not aid or " " in aid or not re.match(r"^MDC3?\.", aid):
        continue          # WBS summary rows have no real id
    s, sa = parse_date(d.get("Start"))
    f, fa = parse_date(d.get("Finish"))
    recs.append({
        "id": aid, "name": str(d.get("Activity Name") or "").strip(),
        "dur": d.get("At Completion Duration"),
        "start": s, "start_actual": sa, "finish": f, "finish_actual": fa,
        "pred": str(d.get("Predecessors") or ""),
        "succ": str(d.get("Successors") or "")})
print(f"activity rows: {len(recs)}  (of {len(rows) - 1} total, "
      f"rest are WBS summaries)")
ast = [x for x in recs if x["start_actual"]]
afn = [x for x in recs if x["finish_actual"]]
print(f"   actual starts : {len(ast)}")
print(f"   actual finishes: {len(afn)}")
if afn:
    ds = [x["finish"] for x in afn if x["finish"]]
    print(f"   actual finishes run {min(ds)} .. {max(ds)}")
if ast:
    ds = [x["start"] for x in ast if x["start"]]
    print(f"   actual starts run   {min(ds)} .. {max(ds)}")
print(f"   with predecessors listed: "
      f"{sum(1 for x in recs if x['pred'].strip())}")

json.dump([{**x, "start": x["start"].isoformat() if x["start"] else None,
            "finish": x["finish"].isoformat() if x["finish"] else None}
           for x in recs], open(f"{SCR}/work/mdc3_h1104.json", "w"))
print(f"\nwrote {SCR}/work/mdc3_h1104.json")

# ---- against our v31 -------------------------------------------------
p = load_xml(f"{SCR}/MDC3_logic_repaired_v31.xml")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


DD = D(p.data_date)
ours = {str(a.activity_id): a for a in p.activities}
hid = {x["id"]: x for x in recs}
print("\n" + "=" * 86)
print(f"AGAINST OUR v31 (data date {DD})")
print("=" * 86)
print(f"our activities {len(ours)}   his {len(hid)}   "
      f"shared ids {len(set(ours) & set(hid))}")

newfin = []
newsta = []
for i in set(ours) & set(hid):
    a, h = ours[i], hid[i]
    if h["finish_actual"] and h["finish"] and a.status != "Completed":
        newfin.append((a, h))
    elif h["start_actual"] and h["start"] and a.status == "Not Started":
        newsta.append((a, h))
print(f"\nHolder shows FINISHED, we do not        : {len(newfin)}")
print(f"Holder shows STARTED, we have not started: {len(newsta)}")
dd_new = max([h["finish"] for _, h in newfin] + [DD]) if newfin else DD
print(f"\nlatest actual finish he carries that we lack: "
      f"{max([h['finish'] for _, h in newfin]) if newfin else '-'}")
c = collections.Counter()
for a, h in newfin:
    c[re.sub(r"\(.*?\)|[-–].*$", "", str(a.name)).strip()[:40]] += 1
print("\nwhat the missing completions are:")
for k, v in c.most_common(14):
    print(f"   x{v:<4}{k}")
print("\nsample:")
for a, h in sorted(newfin, key=lambda x: x[1]["finish"])[-14:]:
    print(f"   {str(a.activity_id):<24}{str(a.name)[:40]:<42}"
          f"{a.status:<13}-> finished {h['finish']}")
