"""The foundations and precast logic: which ties run against the trade order?

Only ten pairs currently read as out of sequence, but that is a statement
about status, not about logic -- a backwards tie between two not-started
activities is just as wrong and shows up as nothing. This looks at every tie
in the area against the order the work actually runs in.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
byu = {a.uid: a for a in p.activities}
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("MDC-3 (Exhibit S) / ", "")


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


# the order concrete foundation work runs in
STAGE = [
    (10, re.compile(r"e[xa]{1,3}cavat", re.I)),
    (20, re.compile(r"form .*tie rebar|form & tie|rebar", re.I)),
    (25, re.compile(r"MEP Underground Sleeves|sleeve", re.I)),
    (30, re.compile(r"-\s*Pour\s*\d+\s*$|^Segment .* Pour \d+$", re.I)),
    (40, re.compile(r"cure time|7-day cure", re.I)),
    (50, re.compile(r"strip|backfill", re.I)),
    (60, re.compile(r"turnover", re.I)),
]


def stage(n):
    for s, rx in STAGE:
        if rx.search(str(n)):
            return s
    return None


def seg(n):
    m = re.search(r"Segment ([A-Z])", str(n))
    p_ = re.search(r"Pour (\d+)", str(n))
    return (m.group(1) if m else None, int(p_.group(1)) if p_ else None)


AREA = re.compile(r"\.ST\.SHF\.|\.ST\.PC\.|\.STR\.UDG\.|\.ST\.ELEV\.")
acts = [a for a in p.activities if AREA.search(str(a.activity_id))]
print(f"foundations / precast / underground activities: {len(acts)}")
c = collections.Counter(re.sub(r"\d+", "#", str(a.activity_id).split(".")[2]
                               if len(str(a.activity_id).split(".")) > 2
                               else "?") for a in acts)
print(f"   by group: {dict(c)}")
st = collections.Counter(stage(a.name) for a in acts)
print(f"   by stage: {dict(sorted((k or 0, v) for k, v in st.items()))}")
print("   (stage 10 excavate, 20 rebar, 25 sleeves, 30 pour, 40 cure, "
      "50 strip/backfill, 60 turnover; None = unclassified)")

uu = {a.uid for a in acts}
bad, fine, cross = [], 0, []
for r in p.relations:
    if r.predecessor_uid not in uu or r.successor_uid not in uu:
        continue
    q, s = byu[r.predecessor_uid], byu[r.successor_uid]
    sq, ss = stage(q.name), stage(s.name)
    if sq is None or ss is None:
        continue
    gq, gs = seg(q.name), seg(s.name)
    if sq > ss:
        bad.append((q, s, r, sq, ss, gq, gs))
    else:
        fine += 1
print(f"\nties inside the area with both ends classified: {len(bad) + fine}")
print(f"   running the right way : {fine}")
print(f"   running BACKWARDS     : {len(bad)}")

print("\n" + "=" * 104)
print("THE BACKWARDS TIES")
print("=" * 104)
NAMES = {10: "excavate", 20: "rebar", 25: "sleeves", 30: "pour",
         40: "cure", 50: "strip", 60: "turnover"}
k = collections.Counter()
for q, s, r, sq, ss, gq, gs in sorted(bad, key=lambda x: (-x[3], x[4])):
    same = "same segment" if gq[0] and gq[0] == gs[0] else (
        f"{gq[0]}->{gs[0]}" if gq[0] or gs[0] else "")
    k[f"{NAMES[sq]} -> {NAMES[ss]}"] += 1
    print(f"   {str(q.activity_id):<22}{str(q.name)[:36]:<38}"
          f"-> {str(s.activity_id):<22}{str(s.name)[:34]:<36}"
          f"[{NAMES[sq]}->{NAMES[ss]}] {same}")
print("\nby kind:")
for kk, v in k.most_common():
    print(f"   {v:>4}  {kk}")
print(f"\nof these, how many are currently out of sequence by status: "
      f"{sum(1 for q, s, r, a_, b_, c_, d_ in bad if q.status == 'Not Started' and s.status in ('Completed', 'In Progress'))}")
