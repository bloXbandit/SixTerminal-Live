"""Foundations logic, judged properly.

The first two passes got this wrong twice and both are worth stating.

Their data spells it "Exacavate", so a regex for "excavat" classified every
excavation as something else. And an excavation is named "Exacavate Footers &
Crossings - Segment E - Pour 1", so a pattern looking for "Pour <n>" at the
end of the name read it as the pour itself.

The bigger error was comparing stages without the pour number. A concrete
crew pours Pour 3 and then moves to excavate Pour 4 -- that tie runs
"backwards" by stage and is exactly right by trade. A tie is only wrong if it
runs backwards WITHIN one pour of one segment.
"""
import collections
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
byu = {a.uid: a for a in p.activities}

STAGE = [(10, re.compile(r"e[xa]{1,3}cavat", re.I)),
         (20, re.compile(r"form\s*&?\s*tie\s*rebar|rebar", re.I)),
         (25, re.compile(r"sleeve", re.I)),
         (30, re.compile(r"pour", re.I)),
         (40, re.compile(r"cure", re.I)),
         (50, re.compile(r"strip|backfill", re.I)),
         (60, re.compile(r"turnover", re.I))]
NAMES = {10: "excavate", 20: "rebar", 25: "sleeves", 30: "pour",
         40: "cure", 50: "strip", 60: "turnover"}


def stage(n):
    s = str(n)
    if re.search(r"cure", s, re.I):
        return 40          # "Pour 1 - 7-Day Cure Time" is a cure, not a pour
    for v, rx in STAGE:
        if rx.search(s):
            return v
    return None


def key(n):
    """(segment, pour) -- the physical pour this activity belongs to"""
    s = str(n)
    m = re.search(r"Segment ([A-Z])\b", s)
    q = re.search(r"Pour (\d+)", s)
    return (m.group(1) if m else None, int(q.group(1)) if q else None)


AREA = re.compile(r"\.ST\.SHF\.")
acts = [a for a in p.activities if AREA.search(str(a.activity_id))]
uu = {a.uid for a in acts}
print(f"shallow foundation activities: {len(acts)}")
st = collections.Counter(NAMES.get(stage(a.name), "?") for a in acts)
print(f"   by stage: {dict(st)}")
pk = collections.Counter(key(a.name) for a in acts)
print(f"   distinct segment/pour groups: "
      f"{len([k for k in pk if k[0] and k[1]])}")

within, across, bad = 0, 0, []
for r in p.relations:
    if r.predecessor_uid not in uu or r.successor_uid not in uu:
        continue
    q, s = byu[r.predecessor_uid], byu[r.successor_uid]
    sq, ss = stage(q.name), stage(s.name)
    kq, ks = key(q.name), key(s.name)
    if sq is None or ss is None:
        continue
    if kq == ks and kq[0] is not None and kq[1] is not None:
        within += 1
        if sq > ss:
            bad.append((q, s, r, sq, ss))
    else:
        across += 1
print(f"\nties inside shallow foundations: {within + across}")
print(f"   within one segment+pour : {within}   of which backwards: {len(bad)}")
print(f"   between pours (crew flow): {across}   <- correct by trade, "
      f"not judged")

print("\n" + "=" * 100)
print("GENUINELY BACKWARDS -- same segment, same pour, stage runs the wrong way")
print("=" * 100)
for q, s, r, sq, ss in sorted(bad, key=lambda x: str(x[0].activity_id)):
    print(f"   {str(q.activity_id):<22}{str(q.name)[:46]:<48}[{NAMES[sq]}]")
    print(f"      -> {str(s.activity_id):<19}{str(s.name)[:46]:<48}"
          f"[{NAMES[ss]}]   {r.type}")
    print(f"      {q.status} -> {s.status}")
print(f"\n{len(bad)} backwards ties")

print("\n" + "=" * 100)
print("THE 10 CURRENTLY OUT OF SEQUENCE -- are they backwards logic or status?")
print("=" * 100)
badset = {(q.uid, s.uid) for q, s, r, a, b in bad}
oos = [(byu[r.predecessor_uid], byu[r.successor_uid]) for r in p.relations
       if byu.get(r.predecessor_uid) and byu.get(r.successor_uid)
       and byu[r.predecessor_uid].status == "Not Started"
       and byu[r.successor_uid].status in ("Completed", "In Progress")]
for q, s in oos:
    kq, ks = key(q.name), key(s.name)
    tag = ("BACKWARDS LOGIC" if (q.uid, s.uid) in badset
           else "crew flow between pours" if kq != ks
           else "status only")
    print(f"   {str(q.activity_id):<22}{str(q.name)[:38]:<40}-> "
          f"{str(s.activity_id):<20}{str(s.name)[:30]:<32}{tag}")
