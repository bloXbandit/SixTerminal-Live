"""MDC-1 v28: put the commissioning sequence back in order, and fix the names.

THE SEQUENCE. The same two defects sit in all three phases.

The Start milestone is driven BY the testing instead of driving it --
Level 3 Cx: Pre-Functional Testing runs INTO Level 3 Commissioning Start. And
the Finish milestone hangs straight off the Start milestone, bypassing the
40-to-44 day test between them. Put together, that is why Level 3
Commissioning Start and Finish for Phase 2 are both 09/29/26: the finish is
one day behind the start and the testing is not in the path at all.

Level 5 goes the same way. Level 4 Commissioning Finish drives Level 5
Commissioning Start, which drives Level 5 Commissioning Finish directly,
while the IST activity hangs off Level 4's testing instead. So Phase 2's
Level 5 Start reads 07/20/27 against a Level 5 Finish of 04/19/27 -- the
phase finishes three months before it begins. Phase 1's Level 5 Start is
06/15/27 against a Level 3 Start of 01/27/28, seven months earlier.

The order a commissioning programme actually runs in:

    Energize and Start Up
      -> L3 Start -> L3 Pre-Functional Testing -> L3 Finish
      -> L4 Start -> L4 Functional Testing     -> L4 Finish
      -> L5 Start -> L5 Integrated Systems Test -> L5 Finish
      -> Substantial Completion / TCO

Ties between those activities are rebuilt to that order. Only ties BETWEEN
the commissioning activities of a phase are touched -- everything feeding in
from the building work, and everything hanging off Substantial Completion,
is left exactly as it is.

THE NAMES. Level 3 Cx: Pre-Functional Te sting and Level 4 Cx: Functional
Te sting carry a space inside the word, which breaks any match against
Holder, who writes Testing 95 times and never Te sting. Penatration appears
on 59 activities; Holder has no penetration activity at all, so nothing
matches on it either way and it is simply misspelled.
"""
import collections
import datetime as dt
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v27.xml")
byu = {a.uid: a for a in p.activities}


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


# ===== 1. the names ==================================================
print("=" * 88)
print("1. NAMES")
print("=" * 88)
FIX = ((re.compile(r"\bTe sting\b"), "Testing"),
       (re.compile(r"Penatration", re.I), "Penetration"))
renamed = collections.Counter()
for a in p.activities:
    old = str(a.name)
    new = old
    for rx, rep in FIX:
        new = rx.sub(rep, new)
    if new != old:
        a.name = new
        renamed[f"{old}  ->  {new}"] += 1
for k, v in sorted(renamed.items(), key=lambda x: -x[1])[:10]:
    print(f"   x{v:<4}{k[:76]}")
print(f"\nrenamed {sum(renamed.values())} activities "
      f"({len(renamed)} distinct names)")

# ===== 2. the commissioning sequence =================================
print("\n" + "=" * 88)
print("2. COMMISSIONING SEQUENCE")
print("=" * 88)
ROLES = (
    ("ENERGIZE", re.compile(r"^Energize Equipment and Start Up", re.I)),
    ("L3 START", re.compile(r"^Level 3 Commissioning Start", re.I)),
    ("L3 WORK", re.compile(r"^Level 3 Cx:", re.I)),
    ("L3 FINISH", re.compile(r"^Level 3 Commissioning Finish", re.I)),
    ("L4 START", re.compile(r"^Level 4 Commissioning Start", re.I)),
    ("L4 WORK", re.compile(r"^Level 4 Cx:", re.I)),
    ("L4 FINISH", re.compile(r"^Level 4 Commissioning Finish", re.I)),
    ("L5 START", re.compile(r"^Level 5 Commissioning Start", re.I)),
    ("L5 WORK", re.compile(r"^Level 5 Cx:", re.I)),
    ("L5 FINISH", re.compile(r"^Level 5 Commissioning Finish", re.I)),
)
TAIL = re.compile(r"^Substantial Completion|^Temporary Certificate", re.I)

have = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
out = collections.defaultdict(set)
for r in p.relations:
    out[r.predecessor_uid].add(r.successor_uid)
nru = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1


def reaches(s, t, cap=400000):
    seen, stack, n = {s}, [s], 0
    while stack and n < cap:
        n += 1
        for v in out[stack.pop()]:
            if v == t:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def tie(s, t):
    global nru
    if s == t or (s, t) in have or reaches(t, s):
        return False
    p.relations = list(p.relations) + [Relation(
        uid=str(nru), predecessor_uid=s, successor_uid=t,
        type="Finish to Start", lag=0.0)]
    have.add((s, t))
    out[s].add(t)
    nru += 1
    return True


def phase_of(a):
    m = re.search(r"PH(\d)", f"{a.activity_id} {a.name}")
    return m.group(1) if m else None


added = removed = 0
for ph in ("1", "2", "3"):
    chain, tails = [], []
    for role, rx in ROLES:
        hit = [a for a in p.activities
               if phase_of(a) == ph and rx.search(str(a.name))]
        if hit:
            chain.append((role, hit[0]))
    tails = [a for a in p.activities
             if phase_of(a) == ph and TAIL.search(str(a.name))]
    uu = {a.uid for _, a in chain}
    print(f"\n--- PHASE {ph} ---   {len(chain)} of 10 roles found")
    # drop every tie that currently runs between these activities
    dead = [r for r in p.relations
            if r.predecessor_uid in uu and r.successor_uid in uu]
    for r in dead:
        q, s = byu[r.predecessor_uid], byu[r.successor_uid]
        print(f"   dropped  {str(q.name)[:40]:<42}-> {str(s.name)[:40]}")
    dk = {(r.predecessor_uid, r.successor_uid) for r in dead}
    p.relations = [r for r in p.relations
                   if (r.predecessor_uid, r.successor_uid) not in dk]
    for s, t in dk:
        have.discard((s, t))
        out[s].discard(t)
    removed += len(dead)
    # lay the proper order
    n = 0
    for i in range(len(chain) - 1):
        if tie(chain[i][1].uid, chain[i + 1][1].uid):
            n += 1
    # and out to the phase's completion markers
    if chain:
        last = chain[-1][1]
        for t in tails:
            if tie(last.uid, t.uid):
                n += 1
    added += n
    print(f"   chained {n} in order: "
          f"{' -> '.join(r for r, _ in chain)}")

# the final CO follows the last phase's TCO
fco = next((a for a in p.activities
            if re.fullmatch(r"Final Certificate of Occupancy",
                            str(a.name).strip(), re.I)), None)
if fco is not None:
    k = 0
    for a in p.activities:
        if re.match(r"^Temporary Certificate of Occupancy \(PH\d\)",
                    str(a.name), re.I) and tie(a.uid, fco.uid):
            k += 1
    added += k
    print(f"\nFinal Certificate of Occupancy now follows {k} phase TCO(s)")

print(f"\nties removed {removed}   ties added {added}")

# ===== checks ========================================================
print("\n" + "=" * 88)
print("CHECKS")
print("=" * 88)
live = {a.uid for a in p.activities}
print(f"relations pointing at a missing activity: "
      f"{sum(1 for r in p.relations if r.predecessor_uid not in live or r.successor_uid not in live)}")
o2 = collections.defaultdict(set)
for r in p.relations:
    o2[r.predecessor_uid].add(r.successor_uid)
col = collections.defaultdict(int)
cyc = 0
for s in list(live):
    if col[s]:
        continue
    st = [(s, iter(o2[s]))]
    col[s] = 1
    while st:
        n_, it = st[-1]
        adv = False
        for v in it:
            if col[v] == 1:
                cyc += 1
            elif col[v] == 0:
                col[v] = 1
                st.append((v, iter(o2[v])))
                adv = True
                break
        if not adv:
            col[n_] = 2
            st.pop()
print(f"cycles: {cyc}")
print(f"'Te sting' left: "
      f"{sum(1 for a in p.activities if 'Te sting' in str(a.name))}")
print(f"'Penatration' left: "
      f"{sum(1 for a in p.activities if re.search('Penatration', str(a.name), re.I))}")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"longest name: {max(len(str(a.name)) for a in p.activities)}  (limit 120)")
out_ = f"{SCR}/MDC1_logic_repaired_v28.xml"
write_p6_xml(p, out_, include_resources=False, include_udfs=True)
print(f"\nwrote {out_}")
