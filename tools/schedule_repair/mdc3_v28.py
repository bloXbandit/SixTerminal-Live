"""MDC-3 v28: drop the duplicated roof scope, re-stitch the rest, retime to Holder.

TWO JOBS.

1. THE ROOF, SCHEDULED TWICE. ROOF.SYS (20) and ROOF.PLAT (21) carry Holder's
   own activity ids, names and durations -- every one of the 41 matches their
   export exactly. The four Segment folders (30 activities) exist in no other
   schedule: not one of their ids appears in Holder's export.

   Nine of those 30 are the same work a second time:

     Dry - In (Vapor Barrier)        C+F, D+G, E+H   vs SYS Temp Dry In
     Permanent Roof System Install   C+F, D+G, E+H   vs SYS Permanent Roof
     Install Steel                   C+F, D+G, E+H   vs PLAT Erect Steel
                                                        Platform + Detailing

   They go. The remaining 21 stay, because nothing else covers them:

     Roof Protection, Set Grating, Set Lower Mechanical Pipe, Tie Mechanical
     Pipe to Steel Platform, Set Above Steel Pipe Supports, Set Above Grating
     Mechanical Pipe -- Holder's export has zero "Grating" and zero "Roof
     Protection" activities anywhere.

     And segments A and B. Holder's roof covers C, D, E, F, G, H and the Admin
     roof; it never mentions A or B, and neither does our SYS or PLAT, even
     though both schedules carry segment A and B structural work. So
     ROOF.AB.1000 and .1010 are the only roof coverage those segments have and
     are NOT duplicates -- an earlier count of mine said 11 and was wrong.

   Deleting from the middle of a chain breaks it, so each chain is re-stitched
   onto the track that survives: Roof Protection picks up its segment pair's
   SYS permanent roof install, and Tie Mechanical Pipe picks up that pair's
   PLAT steel detailing. The tails feed the PLAT run-outs for their pair.

2. RETIMED TO HOLDER. Across 2,464 activities where our id matches theirs and
   work has not started, 1,450 sat earlier in our file, median 29 days, every
   branch of the job. Those adopt Holder's planned dates. Completed and
   in-progress work is not touched, and nothing that Holder does not carry is
   moved.
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
SRC = "Holder MDC-3 schedule export 11/04/2026"
p = load_xml(f"{SCR}/MDC3_logic_repaired_v27.xml")
H = json.load(open(f"{SCR}/work/holder_mdc3.json"))
hact = {a["id"]: a for a in H["acts"]}
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
V27A, V27R = len(p.activities), len(p.relations)


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


rels = list(p.relations)
nrel = max(int(r.uid) for r in rels if str(r.uid).isdigit()) + 1

# ===== 1. THE DUPLICATED ROOF SCOPE ===================================
print("=" * 78)
print("1. ROOF SCOPE SCHEDULED TWICE")
print("=" * 78)
DUP = ["MDC3.ROOF.CF.1000", "MDC3.ROOF.DG.1000", "MDC3.ROOF.EH.1010",
       "MDC3.ROOF.CF.1010", "MDC3.ROOF.DG.1010", "MDC3.ROOF.EH.1020",
       "MDC3.ROOF.CF.1050", "MDC3.ROOF.DG.1050", "MDC3.ROOF.EH.1050"]
# segment pair -> the surviving SYS permanent roof and PLAT steel detailing
PAIR = {"CF": ("MDC3.ROOF.SYS.1050", "MDC3.ROOF.PLAT.1160", "MDC3.ROOF.PLAT.1260"),
        "DG": ("MDC3.ROOF.SYS.1030", "MDC3.ROOF.PLAT.1110", "MDC3.ROOF.PLAT.1230"),
        "EH": ("MDC3.ROOF.SYS.1010", "MDC3.ROOF.PLAT.1070", "MDC3.ROOF.PLAT.1180")}
print(f"{'deleting':<24}{'activity':<48}{'in Holder?':>11}")
gone = set()
for aid in DUP:
    a = ours.get(aid)
    if a is None:
        print(f"{aid:<24}already absent")
        continue
    print(f"{aid:<24}{str(a.name)[:46]:<48}"
          f"{'yes' if aid in hact else 'no':>11}")
    gone.add(a.uid)
p.activities = [a for a in p.activities if a.uid not in gone]
rels = [r for r in rels
        if r.predecessor_uid not in gone and r.successor_uid not in gone]
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id).strip(): a for a in p.activities}
have = {(r.predecessor_uid, r.successor_uid) for r in rels}
nxt = collections.defaultdict(set)
prv = collections.defaultdict(set)
for r in rels:
    nxt[r.predecessor_uid].add(r.successor_uid)
    prv[r.successor_uid].add(r.predecessor_uid)
print(f"\ndeleted {len(gone)} activities and the relationships touching them")


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


def tie(s, d, why, typ="Finish to Start", lag=0.0):
    global nrel
    if s is None or d is None or s.uid == d.uid:
        return False
    if (s.uid, d.uid) in have or reaches(d.uid, s.uid) or reaches(s.uid, d.uid):
        return False
    rels.append(Relation(uid=str(nrel), predecessor_uid=s.uid,
                         successor_uid=d.uid, type=typ, lag=lag))
    nrel += 1
    have.add((s.uid, d.uid))
    nxt[s.uid].add(d.uid)
    prv[d.uid].add(s.uid)
    old = d.udfs.get("COMMENTS", "")
    d.udfs["COMMENTS"] = ((old + "  ") if old else "") + why
    return True


print("\nre-stitching the chains onto the track that survives:")
n = 0
for pf, (sysid, platid, runid) in PAIR.items():
    prot = next((ours[k] for k in ours
                 if k.startswith(f"MDC3.ROOF.{pf}.")
                 and "Roof Protection" in str(ours[k].name)), None)
    if tie(ours.get(sysid), prot,
           f"Lead-in rebuilt in v28: the duplicate Dry-In and Permanent Roof "
           f"System Install for segment {pf[0]}+{pf[1]} were deleted, so this "
           f"now follows {sysid}, which carries Holder's own id for the same "
           f"work."):
        n += 1
        print(f"   {sysid} -> {prot.activity_id}  {str(prot.name)[:40]}")
    tiep = ours.get(f"MDC3.ROOF.{pf}.1060")
    if tie(ours.get(platid), tiep,
           f"Lead-in rebuilt in v28: the duplicate Install Steel for segment "
           f"{pf[0]}+{pf[1]} was deleted, so this now follows {platid}, which "
           f"is Holder's own platform steel for the same segment pair."):
        n += 1
        print(f"   {platid} -> {tiep.activity_id}  {str(tiep.name)[:40]}")
    # the deleted Install Steel sat between Set Lower Mechanical Pipe and this
    # one, so the pipe needs reconnecting too. Tying the platform to the pipe
    # takes both: the lower pipe set, and the steel up to tie it to.
    low = ours.get(f"MDC3.ROOF.{pf}.1040")
    if tie(low, tiep,
           f"Lead-in rebuilt in v28: with the duplicate Install Steel gone, "
           f"the lower mechanical pipe feeds this directly. Tying pipe to the "
           f"platform needs both the pipe set and {platid}'s steel up, so this "
           f"activity carries both predecessors."):
        n += 1
        print(f"   {low.activity_id} -> {tiep.activity_id}  (pipe set, was via "
              f"the deleted Install Steel)")
    tail = ours.get(f"MDC3.ROOF.{pf}.1090")
    if tie(tail, ours.get(runid),
           f"Tie-out added in v28: the above-grating mechanical pipe feeds "
           f"the chilled water run-outs for this segment pair."):
        n += 1
        print(f"   {tail.activity_id} -> {runid}  (run-outs)")
# A+B: real scope with no counterpart; give it a lead-in off the segment's
# own structure and a tie-out to the phase gate
ab0 = ours.get("MDC3.ROOF.AB.1000")
ab2 = ours.get("MDC3.ROOF.AB.1020")
struct = [a for a in p.activities
          if re.search(r"Segment\s+[AB]\b", str(a.name))
          and str(a.activity_id).startswith(("MDC3.ST.", "MDC3.STR."))
          and D(a.planned_finish)]
if struct and ab0 is not None:
    src = max(struct, key=lambda x: D(x.planned_finish))
    if tie(src, ab0,
           "Lead-in added in v28: segments A and B have no roof work in "
           "Holder's export or in our SYS/PLAT track, so this folder is their "
           "only roof coverage. Released by the last segment A/B structural "
           "work in the file."):
        n += 1
        print(f"   {src.activity_id} -> {ab0.activity_id}  (A+B has no "
              f"SYS/PLAT counterpart)")
if tie(ab2, ours.get("MDC3.MIL.PH1.1000"),
       "Tie-out added in v28: segment A+B roof protection gates Level 3 "
       "commissioning, there being no platform track for these segments."):
    n += 1
    print(f"   {ab2.activity_id} -> MDC3.MIL.PH1.1000")
print(f"{n} ties added")

# ===== 2. RETIME TO HOLDER ============================================
print("\n" + "=" * 78)
print("2. RETIMED TO HOLDER'S DATES")
print("=" * 78)
moved, delta = 0, []
skip_prog = 0
bybr = collections.defaultdict(list)
for aid, a in ours.items():
    h = hact.get(aid)
    if h is None:
        continue
    hs, hf = D(h["start"]), D(h["finish"])
    if not hs or not hf:
        continue
    if a.status != "Not Started":
        if a.status == "In Progress":
            skip_prog += 1
        continue
    os_ = D(a.planned_start)
    if os_ and os_ == hs and D(a.planned_finish) == hf:
        continue
    d = (hs - os_).days if os_ else None
    a.planned_start, a.planned_finish = hs.isoformat(), hf.isoformat()
    if h["dur"]:
        a.planned_duration = float(h["dur"]) * 8.0
        a.remaining_duration = float(h["dur"]) * 8.0
    old = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((old + "  ") if old else "") + (
        f"Retimed in v28 to the {SRC}: {hs} to {hf}"
        + (f", moved {d:+d} calendar days from {os_}." if d is not None else "."))
    moved += 1
    if d is not None:
        delta.append(d)
        bybr[".".join(aid.split(".")[:3])].append(d)
print(f"retimed {moved} Not Started activities to Holder's dates")
print(f"   left alone: {skip_prog} in progress, "
      f"{sum(1 for a in ours.values() if a.status == 'Completed')} completed, "
      f"{sum(1 for a in ours.values() if str(a.activity_id) not in hact)} "
      f"not in Holder's export")
if delta:
    s = sorted(delta)
    print(f"   moved later {sum(1 for x in s if x > 0)}   "
          f"earlier {sum(1 for x in s if x < 0)}   median {s[len(s) // 2]:+d}d   "
          f"range {s[0]:+d} .. {s[-1]:+d}")
    print(f"\n   {'branch':<22}{'n':>5}{'median move':>13}")
    for k, v in sorted(bybr.items(), key=lambda x: -len(x[1]))[:14]:
        v = sorted(v)
        print(f"   {k:<22}{len(v):>5}{v[len(v) // 2]:>+12d}d")

# ===== verify =========================================================
p.relations = rels
alive = {a.uid for a in p.activities}
ind = collections.Counter()
s2 = collections.defaultdict(list)
for r in rels:
    ind[r.successor_uid] += 1
    s2[r.predecessor_uid].append(r.successor_uid)
q = collections.deque(u for u in alive if not ind[u])
i2, nn = dict(ind), 0
while q:
    u = q.popleft()
    nn += 1
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
viol = tot = 0
for r in rels:
    a, b = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
    if not a or not b or r.type != "Finish to Start":
        continue
    fa, sb = D(a.planned_finish), D(b.planned_start)
    if not fa or not sb:
        continue
    tot += 1
    viol += sb < fa
dang = sum(1 for r in rels
           if r.predecessor_uid not in alive or r.successor_uid not in alive)
print("\n" + "=" * 78)
print(f"{'':<40}{'v27':>9}{'v28':>9}")
print(f"{'activities':<40}{V27A:>9}{len(p.activities):>9}")
print(f"{'relationships':<40}{V27R:>9}{len(rels):>9}")
print(f"{'islands':<40}{5:>9}{comp:>9}")
print(f"{'largest network':<40}{'99%':>9}{f'{100 * big // len(alive)}%':>9}")
print(f"{'no predecessor, Not Started':<40}{75:>9}"
      f"{sum(1 for a in nop if a.status == 'Not Started'):>9}")
print(f"{'no successor':<40}{19:>9}{len(nos):>9}")
print(f"{'acyclic':<40}{'all':>9}"
      f"{'all' if nn == len(alive) else f'{nn}/{len(alive)}':>9}")
print(f"{'dangling relationships':<40}{0:>9}{dang:>9}")
print(f"{'FS ties successor starts too early':<40}{'1300 (19%)':>9}"
      f"{f'{viol} ({100 * viol // max(tot, 1)}%)':>9}")
print(f"\nstatus: " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
    a.status for a in p.activities).most_common()))
out = f"{SCR}/MDC3_logic_repaired_v28.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
json.dump({"deleted": DUP, "ties": n, "retimed": moved,
           "median": sorted(delta)[len(delta) // 2] if delta else 0,
           "by_branch": {k: sorted(v)[len(v) // 2] for k, v in bybr.items()}},
          open(f"{SCR}/work/mdc3_v28.json", "w"), indent=1)
