"""Fill in the missing MV room steps, on whatever file you point it at.

    python3 mdc3_patch_mv.py IN.xml OUT.xml           JER scope only (default)
    python3 mdc3_patch_mv.py IN.xml OUT.xml --all     JER plus the WBO placeholders

Six rooms -- MV 106, 107, 110, 111, 114, 115 -- carry 16 of the 26 steps the
other nine have. The activity numbering proves they are unfinished rather
than different: every room uses slots 1010 to 1260 and these six have holes
at exactly the slots whose steps are absent.

Two of the ten are JER electrical and are real scope:

    1190  MSG to CUP Feeder Pulls            4d
    1200  MSG to MSG pull / Load Bank Loop   3d

The other eight are **WBO placeholders for other trades. They carry no JER
hours, and the complete rooms file them under a separate "WBO MV nnn" WBS
node, so adding them needs that node to exist. Off by default; --all turns
them on and they go in the room's own node rather than inventing WBS.

Everything is copied from MV 101: duration, labour units, calendar, the
Number of Electricians UDF, and the predecessor pattern between slots.
Nothing already in the file is changed.
"""
import collections, re, sys
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SRC, OUT = sys.argv[1], sys.argv[2]
ALL = "--all" in sys.argv
TEMPLATE = "101"
JER_SLOTS = {"1190", "1200"}


def slot(aid):
    return str(aid).split(".")[-1]


def room(aid):
    m = re.search(r'\.MV\.(\d{3})\.', str(aid))
    return m.group(1) if m else None


p = load_xml(SRC)
byu = {a.uid: a for a in p.activities}
ids = {str(a.activity_id): a for a in p.activities}
tpl = {slot(a.activity_id): a for a in p.activities
       if room(a.activity_id) == TEMPLATE}
if not tpl:
    sys.exit(f"no MV {TEMPLATE} template found in {SRC}")
rooms = sorted({r for a in p.activities if (r := room(a.activity_id))})
have = collections.defaultdict(set)
for a in p.activities:
    r = room(a.activity_id)
    if r:
        have[r].add(slot(a.activity_id))
# the template's internal logic, slot to slot
tpl_rel = [(slot(byu[r.predecessor_uid].activity_id),
            slot(byu[r.successor_uid].activity_id), r.type, r.lag)
           for r in p.relations
           if byu.get(r.predecessor_uid) and byu.get(r.successor_uid)
           and room(byu[r.predecessor_uid].activity_id) == TEMPLATE
           and room(byu[r.successor_uid].activity_id) == TEMPLATE]

Act = type(p.activities[0])
Rel = type(p.relations[0])
nact = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
nrel = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
added = relinked = 0
print(f"template MV {TEMPLATE}: {len(tpl)} slots, {len(tpl_rel)} internal ties")
print(f"scope: {'JER + WBO placeholders' if ALL else 'JER electrical only'}\n")
for r in rooms:
    missing = sorted(set(tpl) - have[r])
    if not ALL:
        missing = [s for s in missing if s in JER_SLOTS]
    if not missing:
        continue
    # file new work in whichever node this room already uses most
    node = collections.Counter(a.wbs_uid for a in p.activities
                               if room(a.activity_id) == r).most_common(1)[0][0]
    print(f"   MV {r}: adding {len(missing)}  ({', '.join(missing)})")
    for s in missing:
        t = tpl[s]
        aid = f"MDC3.MV.{r}.{s}"
        if aid in ids:
            continue
        a = Act(uid=str(nact), activity_id=aid,
                name=re.sub(rf'\bMV\s*{TEMPLATE}\b', f"MV {r}", str(t.name)),
                wbs_uid=node, calendar_uid=t.calendar_uid,
                planned_duration=t.planned_duration,
                remaining_duration=t.planned_duration,
                status="Not Started")
        a.planned_labor_units = t.planned_labor_units
        a.remaining_labor_units = t.planned_labor_units
        a.udfs = dict(t.udfs or {})
        a.udfs["COMMENTS"] = (
            f"Added to match the MV {TEMPLATE} template; slot {s} was reserved "
            f"in this room's numbering and never filled. Duration, hours and "
            f"logic copied from MV {TEMPLATE}. CONFIRM with the field.")[:248]
        p.activities.append(a)
        ids[aid] = a
        byu[a.uid] = a
        nact += 1
        added += 1
# wire the new activities using the template's slot-to-slot pattern
exists = {(x.predecessor_uid, x.successor_uid) for x in p.relations}
for r in rooms:
    for ps, ss, typ, lag in tpl_rel:
        a, b = ids.get(f"MDC3.MV.{r}.{ps}"), ids.get(f"MDC3.MV.{r}.{ss}")
        if not a or not b or (a.uid, b.uid) in exists:
            continue
        p.relations.append(Rel(uid=str(nrel), predecessor_uid=a.uid,
                               successor_uid=b.uid, type=typ, lag=lag))
        exists.add((a.uid, b.uid))
        nrel += 1
        relinked += 1
# Anything still without a predecessor takes whatever already feeds that
# room's earliest activity. The rooms are crew-chained to each other, so a
# new first-slot activity belongs on the same incoming tie rather than
# floating to the data date.
pre_now = {x.successor_uid for x in p.relations}
feeders = collections.defaultdict(list)
for x in p.relations:
    r = room(byu[x.successor_uid].activity_id) if x.successor_uid in byu else None
    if r and room(byu[x.predecessor_uid].activity_id) != r:
        feeders[r].append(x.predecessor_uid)
fixed = 0
for a in list(p.activities):
    r = room(a.activity_id)
    if not r or a.uid in pre_now or a.status != "Not Started":
        continue
    if not str((a.udfs or {}).get("COMMENTS", "")).startswith("Added to match"):
        continue
    for fu in feeders.get(r, [])[:1]:
        p.relations.append(Rel(uid=str(nrel), predecessor_uid=fu,
                               successor_uid=a.uid, type="Finish to Start",
                               lag=0.0))
        nrel += 1; relinked += 1; fixed += 1
if fixed:
    print(f"   {fixed} new first-slot activities tied into the room-to-room chain")

print(f"\nactivities added {added}   relationships added {relinked}")
compute_dates(p, apply_dates=False)
import datetime as dt
D = lambda v: dt.date.fromisoformat(str(v)[:10]) if v else None
fin = max(D(a.actual_finish) or D(a.early_finish) for a in p.activities
          if a.actual_finish or a.early_finish)
pre = {x.successor_uid for x in p.relations}
print(f"finish {fin}   hours {sum(a.planned_labor_units for a in p.activities):,.0f}")
print(f"orphans {sum(1 for a in p.activities if a.uid not in pre and a.status=='Not Started' and not a.constraint_type)}")
write_p6_xml(p, OUT, include_resources=False, include_udfs=True)
print(f"wrote {OUT}")
