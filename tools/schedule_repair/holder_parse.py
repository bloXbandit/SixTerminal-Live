"""Parse Holder's 11/04 MDC-3 export: activities, logic, and actuals.

The export is the real thing -- Activity ID, name, duration, start, finish,
Predecessors AND Successors -- so it carries the whole relationship set rather
than one side of it. Dates carrying a trailing "A" are actuals, "*" is a
constraint, and the WBS appears as indented rows with no activity id.
"""
import collections
import datetime as dt
import json
import re
import sys

import openpyxl

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
AID = re.compile(r"^(MDC3\.[A-Z0-9][A-Za-z0-9.\-]*)$")
REL = re.compile(r"([A-Za-z0-9][A-Za-z0-9.\-]*?)(?::\s*)?"
                 r"(FS|SS|FF|SF)?\s*(?:([+-]\s*\d+)\s*d?)?$")


def pdate(v):
    """returns (date, is_actual, is_constrained)"""
    if isinstance(v, dt.datetime):
        return v.date(), False, False
    if isinstance(v, dt.date):
        return v, False, False
    s = str(v or "").strip()
    if not s or s == "None":
        return None, False, False
    act = s.endswith(" A") or s.endswith("A")
    con = "*" in s
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})", s)
    if m:
        return (dt.date(2000 + int(m.group(3)), MON[m.group(2).title()],
                        int(m.group(1))), act, con)
    try:
        return dt.date.fromisoformat(s[:10]), act, con
    except ValueError:
        return None, act, con


wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
ws = wb[wb.sheetnames[0]]
rows = list(ws.iter_rows(values_only=True))
wb.close()
H = {str(x).strip(): i for i, x in enumerate(rows[0]) if x}
print(f"{len(rows)} rows   columns: {list(H)}")


def g(r, k):
    i = H.get(k)
    return r[i] if i is not None and i < len(r) else None


acts, wbsrows = {}, []
for r in rows[1:]:
    raw = g(r, "Activity ID")
    nm = g(r, "Activity Name")
    if raw is None:
        continue
    s = str(raw).strip()
    if not AID.match(s):
        if nm or s:
            wbsrows.append(str(nm or s).strip())
        continue
    st, sa, sc = pdate(g(r, "Start"))
    fi, fa, fc = pdate(g(r, "Finish"))
    acts[s] = {"id": s, "name": str(nm or "").strip(),
               "dur": g(r, "At Completion Duration"),
               "start": st, "finish": fi,
               "started": sa, "finished": fa, "constrained": sc or fc,
               "pred_raw": str(g(r, "Predecessors") or "").strip(),
               "succ_raw": str(g(r, "Successors") or "").strip()}
print(f"\n{len(acts)} activities, {len(wbsrows)} WBS/summary rows")

# ---- status implied by the actual flags -------------------------------
done = [a for a in acts.values() if a["finished"]]
prog = [a for a in acts.values() if a["started"] and not a["finished"]]
nots = [a for a in acts.values() if not a["started"]]
print(f"finished (actual finish): {len(done)}")
print(f"in progress (actual start only): {len(prog)}")
print(f"not started: {len(nots)}")
ds = sorted(a["finish"] for a in done if a["finish"])
if ds:
    print(f"actual finishes run {ds[0]} .. {ds[-1]}")
dd = max(ds) if ds else None
print(f"implied data date (latest actual): {dd}")

# ---- the relationship set ---------------------------------------------
def split(s):
    out = []
    for tok in re.split(r"[,;]\s*", s):
        tok = tok.strip()
        if not tok:
            continue
        m = REL.match(tok)
        if not m:
            continue
        i = m.group(1).strip()
        if not i.upper().startswith("MDC3"):
            continue
        typ = {"FS": "Finish to Start", "SS": "Start to Start",
               "FF": "Finish to Finish",
               "SF": "Start to Finish"}.get(m.group(2) or "FS",
                                            "Finish to Start")
        lag = int((m.group(3) or "0").replace(" ", ""))
        out.append((i, typ, lag))
    return out


pairs = {}
npre = nsuc = 0
for a in acts.values():
    for i, t, l in split(a["pred_raw"]):
        pairs[(i, a["id"])] = (t, l)
        npre += 1
    for i, t, l in split(a["succ_raw"]):
        pairs[(a["id"], i)] = (t, l)
        nsuc += 1
print(f"\nrelationships: {npre} from predecessor cells, {nsuc} from successor "
      f"cells, {len(pairs)} distinct pairs")
both = sum(1 for k in pairs
           if k[0] in acts and k[1] in acts)
print(f"   pairs where both ends are activities in this export: {both}")
print(f"   types: " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
    t for t, _ in pairs.values()).most_common()))
lags = [l for _, l in pairs.values() if l]
print(f"   with a lag: {len(lags)}"
      + (f"   range {min(lags)} .. {max(lags)}d" if lags else ""))

# open ends on Holder's side
succ_of = collections.defaultdict(set)
pred_of = collections.defaultdict(set)
for (a, b) in pairs:
    succ_of[a].add(b)
    pred_of[b].add(a)
nop = [a for a in acts if not pred_of[a]]
nos = [a for a in acts if not succ_of[a]]
print(f"\nHolder's own open ends: {len(nop)} with no predecessor, "
      f"{len(nos)} with no successor")

# ---- id prefix shape --------------------------------------------------
print(f"\nactivity-id prefixes (top 18):")
for k, v in collections.Counter(
        ".".join(a.split(".")[:3]) for a in acts).most_common(18):
    print(f"   {v:>5}  {k}")

json.dump({"acts": [{**a, "start": str(a["start"]), "finish": str(a["finish"])}
                    for a in acts.values()],
           "pairs": [[a, b, t, l] for (a, b), (t, l) in pairs.items()],
           "wbs": wbsrows},
          open(f"{SCR}/work/holder_mdc3.json", "w"), indent=1, default=str)
print(f"\nwrote {SCR}/work/holder_mdc3.json")
