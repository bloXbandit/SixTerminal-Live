"""MDC-3 v27: close the open ends that the file itself tells us how to close.

Only fixes with a precedent already in this schedule go in. Where the file
gives no answer the gap is reported instead of guessed -- the three times this
project went wrong, it was from reading an inference back as fact.

What gets fixed:

1. HANG SCR, 27 rooms. Gen 303 already carries OH Branch Raceway -> Hang SCR
   as Finish to Start with no lag, and the activity ids are positional in
   every generator room (.1040 is the raceway, .1050 is the SCR). The other 27
   rooms just never got the tie. Hang SCR already has successors, so this is
   only the missing lead-in.

2. ISOLATED CHAIN TAILS. The admin space and the generator rooms' final
   finishes each run as a clean internal chain that ends nowhere. Their last
   activity feeds the Substantial Completion milestone for its phase, which is
   the only thing downstream of them that is not already in the chain.

3. ROOM READY FOR LOAD. The six data-hall milestones chain east to west and
   then stop. The west one of each pair feeds its phase's Substantial
   Completion: a phase is not substantially complete before its data halls can
   take load.

Everything is redundancy-guarded, so a tie the network already implies is
rejected rather than added -- the guard that was missing when Holder's logic
first went in and pushed redundancy from 15% to 33%.

What is reported and NOT fixed, because the file does not say:

  - INSTALL HIGH STEEL, 28 generator rooms. No instance of this work is tied
    anywhere in our file, and Holder's export carries no High Steel activity
    at all, so there is no precedent to copy from either side.
  - THE SEALER HAND-OFFS. Four first-floor slab pours (areas 5, 9, 15, 43)
    seal and then release nothing, while the Phase 1 electrical and MV rooms
    have no lead-in. The established pattern is sealer -> room, and 67 of
    those ties already exist, but which room sits on which pour is not in the
    schedule. The Phase 1 rooms are second-floor rooms (ER 2xx), so their
    release is a Level 2 pour, not these Level 1 ones.
  - THE ROOF, SCHEDULED THREE TIMES. Segments A+B / C+F / D+G / E+H, ROOF.SYS
    and ROOF.PLAT all carry dry-in, permanent roof and platform steel for the
    same segments on different dates. Wiring the duplicates together would
    manufacture dependencies between activities that are the same work.
  - FOUR DATELESS DUPLICATE SEALERS. Areas 21, 25, 42 and 43 each carry a
    second sealer activity with no dates and no successor, alongside the dated
    one that is properly tied. They want deleting, not tying.
  - THE LLE BRANCHES. 578 long-lead equipment activities across the three
    phases have no predecessor by design: they are deliveries, not work.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Relation
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC3_logic_repaired_v26.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
V26A, V26R = len(p.activities), len(p.relations)


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


rels = list(p.relations)
nrel = max(int(r.uid) for r in rels if str(r.uid).isdigit()) + 1
have = {(r.predecessor_uid, r.successor_uid) for r in rels}
nxt = collections.defaultdict(set)
prv = collections.defaultdict(set)
for r in rels:
    nxt[r.predecessor_uid].add(r.successor_uid)
    prv[r.successor_uid].add(r.predecessor_uid)


def reaches(a, b):
    if a == b:
        return True
    seen, q = {a}, collections.deque([a])
    while q:
        u = q.popleft()
        for v in nxt[u]:
            if v == b:
                return True
            if v not in seen:
                seen.add(v)
                q.append(v)
    return False


rejected = []


def tie(src, dst, why, typ="Finish to Start", lag=0.0):
    global nrel
    if src is None or dst is None or src.uid == dst.uid:
        return False
    if (src.uid, dst.uid) in have:
        rejected.append((src.activity_id, dst.activity_id, "already present"))
        return False
    if reaches(dst.uid, src.uid):
        rejected.append((src.activity_id, dst.activity_id, "would make a loop"))
        return False
    if reaches(src.uid, dst.uid):
        rejected.append((src.activity_id, dst.activity_id,
                         "logic already implies it"))
        return False
    rels.append(Relation(uid=str(nrel), predecessor_uid=src.uid,
                         successor_uid=dst.uid, type=typ, lag=lag))
    nrel += 1
    have.add((src.uid, dst.uid))
    nxt[src.uid].add(dst.uid)
    prv[dst.uid].add(src.uid)
    old = dst.udfs.get("COMMENTS", "")
    dst.udfs["COMMENTS"] = ((old + "  ") if old else "") + why
    return True


# ===== 1. HANG SCR ====================================================
print("=" * 78)
print("1. HANG SCR -- the lead-in 27 generator rooms were missing")
print("=" * 78)
tmpl = []
for a in p.activities:
    if "Hang SCR" in str(a.name) and prv[a.uid]:
        for u in prv[a.uid]:
            tmpl.append((byu[u].activity_id, a.activity_id))
print(f"precedent in the file: {tmpl}")
scr = [a for a in p.activities if "Hang SCR" in str(a.name)]
done, nopair = 0, []
for a in scr:
    if prv[a.uid]:
        continue
    m = re.match(r"(MDC3\.GEN\.\d+)\.(\d+)$", str(a.activity_id))
    if not m:
        nopair.append(str(a.activity_id))
        continue
    rw = ours.get(f"{m.group(1)}.1040")
    if rw is None or "Branch Raceway" not in str(rw.name):
        nopair.append(str(a.activity_id))
        continue
    if tie(rw, a, "Lead-in added in v27: OH Branch Raceway -> Hang SCR, "
                  "copying the tie Gen 303 already carries (Finish to Start, "
                  "no lag). The raceway goes in overhead before the SCR is "
                  "hung so the work is not done around it."):
        done += 1
print(f"Hang SCR activities {len(scr)}   ties added {done}   "
      f"no matching raceway {len(nopair)}")
if nopair:
    print(f"   unmatched: {nopair}")

# ===== 2. ISOLATED CHAIN TAILS ========================================
print("\n" + "=" * 78)
print("2. CHAIN TAILS THAT ENDED NOWHERE")
print("=" * 78)
SC = {1: "MDC3.MIL.PH1.1070", 2: "MDC3.MIL.PH2.9070", 3: "MDC3.MIL.PH3.9070"}
tails = [("MDC3.PH1.2ND.FL.1040", 1,
          "the admin space ran as a 22-activity island; its last activity now "
          "feeds Substantial Completion (PH1), which cannot be reached before "
          "the support and administrative rooms are finished"),
         ("MDC3.GEN.RM.1990", 3,
          "the generator rooms' final finishes ran as a 6-activity island; "
          "the last floor finish now feeds Substantial Completion (PH3)")]
for aid, ph, why in tails:
    a = ours.get(aid)
    if a is None:
        print(f"   {aid}: not in the file")
        continue
    m = ours.get(SC[ph])
    ok = tie(a, m, f"Tie-out added in v27: {why}.")
    print(f"   {aid:<24}{str(a.name)[:40]:<42}-> {SC[ph]}  "
          f"{'tied' if ok else 'rejected'}")

# ===== 3. ROOM READY FOR LOAD =========================================
print("\n" + "=" * 78)
print("3. ROOM READY FOR LOAD -> SUBSTANTIAL COMPLETION")
print("=" * 78)
for a in sorted([x for x in p.activities if "Room Ready for Load" in str(x.name)],
                key=lambda x: str(x.activity_id)):
    if nxt[a.uid]:
        print(f"   {a.activity_id:<24}{str(a.name)[:38]:<40}already feeds "
              f"{', '.join(byu[u].activity_id for u in nxt[a.uid])}")
        continue
    m = re.match(r"MDC3\.PH(\d)\.", str(a.activity_id))
    ph = int(m.group(1)) if m else 1
    mil = ours.get(SC[ph])
    ok = tie(a, mil, f"Tie-out added in v27: a data hall ready for load is a "
                     f"precondition for Substantial Completion (PH{ph}), and "
                     f"this milestone previously ended the chain.")
    print(f"   {a.activity_id:<24}{str(a.name)[:38]:<40}-> {SC[ph]}  "
          f"{'tied' if ok else 'rejected'}")

print(f"\nrejected by the guards ({len(rejected)}):")
for s, d, why in rejected:
    print(f"   {s:<24}-> {d:<24}{why}")

# ===== verify =========================================================
p.relations = rels
alive = {a.uid for a in p.activities}
ind = collections.Counter()
s2 = collections.defaultdict(list)
for r in rels:
    if r.predecessor_uid in alive and r.successor_uid in alive:
        ind[r.successor_uid] += 1
        s2[r.predecessor_uid].append(r.successor_uid)
q = collections.deque(u for u in alive if not ind[u])
i2, n = dict(ind), 0
while q:
    u = q.popleft()
    n += 1
    for v in s2[u]:
        i2[v] -= 1
        if not i2[v]:
            q.append(v)
pr = {r.successor_uid for r in rels}
su = {r.predecessor_uid for r in rels}
nop = [a for a in p.activities if a.uid not in pr]
nos = [a for a in p.activities if a.uid not in su]
adj = collections.defaultdict(set)
for r in rels:
    adj[r.predecessor_uid].add(r.successor_uid)
    adj[r.successor_uid].add(r.predecessor_uid)
seen, comp, big = set(), 0, 0
for u in alive:
    if u in seen:
        continue
    comp += 1
    c, qq = 0, collections.deque([u])
    seen.add(u)
    while qq:
        x = qq.popleft()
        c += 1
        for y in adj[x] - seen:
            seen.add(y)
            qq.append(y)
    big = max(big, c)
print("\n" + "=" * 78)
print(f"{'':<40}{'v26':>9}{'v27':>9}")
print(f"{'activities':<40}{V26A:>9}{len(p.activities):>9}")
print(f"{'relationships':<40}{V26R:>9}{len(rels):>9}")
print(f"{'no predecessor (all)':<40}{733:>9}{len(nop):>9}")
print(f"{'no predecessor, Not Started':<40}{102:>9}"
      f"{sum(1 for a in nop if a.status == 'Not Started'):>9}")
print(f"{'no successor':<40}{24:>9}{len(nos):>9}")
print(f"{'islands':<40}{7:>9}{comp:>9}")
print(f"{'largest network':<40}{'98%':>9}{f'{100 * big // len(alive)}%':>9}")
print(f"{'acyclic':<40}{'all':>9}"
      f"{'all' if n == len(alive) else f'{n}/{len(alive)}':>9}")
print(f"{'duplicate relationships':<40}{0:>9}"
      f"{len(rels) - len({(r.predecessor_uid, r.successor_uid) for r in rels}):>9}")

out = f"{SCR}/MDC3_logic_repaired_v27.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
