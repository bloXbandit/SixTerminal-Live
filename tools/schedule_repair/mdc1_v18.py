"""MDC-1 v18: put back the nine completions the evidence argues against.

All nine were completed by backward inference -- something downstream had
started, so this must have finished -- and all nine fail at least two of the
tests that should have contradicted that: the GC still has the work open, it
is a sign-off rather than work, it is too long to have gone unreported, or
the schedule itself expected it to start in November or December.

Reverting a completion that was inferred FROM a successor re-opens the
question the inference was answering: if this did not finish, how is its
successor moving? So each one is reverted and then its successors are
re-examined. Where a successor is only moving because of this activity, and
nothing independent supports it, it goes back too. Where the successor has
its own evidence -- a daily report, the GC, its own actual dates from the
original file -- it stays, and the pair is reported as out of sequence for
someone to settle.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
PUT = set(json.load(open(f"{SCR}/work/mdc1_putback.json")))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v17.xml")
byu = {a.uid: a for a in p.activities}
ours = {str(a.activity_id): a for a in p.activities}
B0 = collections.Counter(a.status for a in p.activities)


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


DD = D(p.data_date)
pred = collections.defaultdict(list)
succ = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r.predecessor_uid)
    succ[r.predecessor_uid].append(r.successor_uid)


def revert(a, why):
    a.status = "Not Started"
    a.actual_start = None
    a.actual_finish = None
    a.actual_duration = 0.0
    a.remaining_duration = a.planned_duration
    a.percent_complete = 0.0
    old = a.udfs.get("COMMENTS", "")
    # drop the claim we are withdrawing so the file does not carry both
    old = re.sub(r"Completed by inference[^.]*\.( Dates inferred[^.]*\.)?"
                 r"( CONFIRM WITH THE FIELD\.)?", "", old).strip()
    a.udfs["COMMENTS"] = (((old + "  ") if old else "") + why)[:248]


print("=" * 76)
print("PUTTING THE NINE BACK")
print("=" * 76)
done = []
for aid in sorted(PUT):
    a = ours.get(aid)
    if a is None or a.status != "Completed":
        continue
    revert(a, "Completion withdrawn in v18: it was inferred from a successor, "
              "and the evidence runs the other way - see the doubtful "
              "completions list. Back to Not Started pending a field call.")
    done.append(aid)
    print(f"   {aid:<26}{str(a.name)[:44]}")
print(f"\nreverted {len(done)}")

print("\n" + "=" * 76)
print("WHAT WAS MOVING BECAUSE OF THEM")
print("=" * 76)
INDEP = re.compile(r"daily report|Exhibit S|Assumed started", re.I)
cascade, kept = [], []
for aid in done:
    a = ours[aid]
    for v in succ[a.uid]:
        s = byu.get(v)
        if s is None or s.status == "Not Started":
            continue
        c = str((s.udfs or {}).get("COMMENTS", ""))
        others = [byu[q] for q in pred[s.uid]
                  if q in byu and byu[q].status == "Completed"]
        if INDEP.search(c):
            kept.append((aid, str(s.activity_id), str(s.name)[:38], s.status,
                         "has its own evidence"))
        elif "by inference" in c and not others:
            revert(s, "Withdrawn in v18: this was only moving because a "
                      "predecessor had been inferred complete, and that "
                      "inference has been withdrawn.")
            cascade.append((aid, str(s.activity_id), str(s.name)[:38]))
        else:
            kept.append((aid, str(s.activity_id), str(s.name)[:38], s.status,
                         "other predecessors are complete"))
print(f"successors also withdrawn: {len(cascade)}")
for a1, a2, n in cascade:
    print(f"   {a2:<26}{n:<40}(was moving off {a1})")
print(f"\nsuccessors kept, now out of sequence and worth settling: {len(kept)}")
for a1, a2, n, st, why in kept[:12]:
    print(f"   {a2:<26}{n:<40}{st:<13}{why}")

A1 = collections.Counter(a.status for a in p.activities)
oos = sum(1 for r in p.relations
          if byu.get(r.predecessor_uid) and byu.get(r.successor_uid)
          and byu[r.predecessor_uid].status == "Not Started"
          and byu[r.successor_uid].status in ("Completed", "In Progress"))
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
ELEC = re.compile(
    r"conduit|raceway|tray|wire|pull|\bterm\b|terminat|switchgear|panel|"
    r"light|device|feeder|busway|ground|fire alarm|\bFA\b|vesda|\bUPS\b|"
    r"transformer|\bXFMR\b|\bMSB\b|\bRMU\b|\bLBB\b|\bSCR\b|skid|energi|"
    r"start.?up|isolator|electrical|rough.?in|high steel|hanger", re.I)
oe = [a for a in p.activities
      if not WBO.search(str(a.name)) and ELEC.search(str(a.name))]
od = sum(1 for a in oe if a.status == "Completed")
od23 = sum(1 for a in oe if a.status == "Completed" and D(a.actual_finish)
           and D(a.actual_finish) <= dt.date(2026, 10, 23))
print("\n" + "=" * 76)
print(f"{'':<22}{'v17':>10}{'v18':>10}")
for s in ("Completed", "In Progress", "Not Started"):
    print(f"{s:<22}{B0.get(s, 0):>10}{A1.get(s, 0):>10}")
print(f"\nout-of-sequence pairs: {oos}")
print(f"electrical complete at our 10/31: {100 * od // len(oe)}%")
print(f"electrical complete at their 10/23: {100 * od23 // len(oe)}%   "
      f"(GC says 27%)")
print(f"actuals past the data date: "
      f"{sum(1 for a in p.activities if (D(a.actual_finish) and D(a.actual_finish) > DD) or (D(a.actual_start) and D(a.actual_start) > DD))}")
print(f"in progress at 100%: "
      f"{sum(1 for a in p.activities if a.status == 'In Progress' and (a.percent_complete or 0) >= 100)}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v18.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
