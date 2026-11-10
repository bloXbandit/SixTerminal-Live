"""MDC-1 v16: status the schedule from JER's own daily reports, 09/27 - 10/31.

The GC's print only reaches 10/23 and only elaborates their own scope, which
left the 10/24-10/31 week blank and a lot of JER work unreported. The daily
reports cover it: 98 MDC-1 reports in this window, 64,216 hours, naming the
work, the room and the foreman.

HOW A REPORT IS MATCHED TO AN ACTIVITY. Report text is free-form, so a match
needs two things to agree, never one:

  the work   a distinctive phrase from the activity's name -- ladder tray,
             LBB to LBB, vesda rack, device trim, wire pull, branch power --
             matched against the report's task text
  the place  a room or area named in both, or the activity's own room folder

A match on work alone is ignored: "conduit" appears in sixty reports and two
hundred activities and proves nothing about which.

WHAT IT SETS. An activity with a match becomes In Progress from the date of
the first report that names it, unless a report whose status is "Work
Completed as Planned" names it, in which case it is Completed on that date.
Remaining duration is recut from the data date. Nothing already Completed is
touched, nothing is dated past the 10/31 data date, and every activity
carries a comment naming the report count, the foremen and the dates, so each
one can be checked against the source.

This is field reporting, not a certified update: a foreman writing "conduit
install 3rd floor" tells us work happened there, not which of our activities
it was. Confirm before issuing.
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
LO, DD = dt.date(2026, 9, 27), dt.date(2026, 10, 31)
WORK = frozenset({0, 1, 2, 3, 4, 5})
R = json.load(open(f"{SCR}/work/mdc1_daily.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v15_final.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
byu = {a.uid: a for a in p.activities}
B0 = collections.Counter(a.status for a in p.activities)


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def wdays(a, b):
    if not a or not b or b < a:
        return 1
    return max(1, sum(1 for i in range((b - a).days + 1)
                      if (a + dt.timedelta(days=i)).weekday() in WORK))


# ---- the vocabulary that identifies a PARTICULAR piece of work -----------
# Every pattern is word-bounded. Without that, "stem" matched inside
# "Roofing SYSTEM Installation" and completed the roof off reports about
# heat trace being run on it.
# SPECIFIC work names a particular thing and can stand on a general place.
# GENERIC work is said about half the job, so it needs a room code to mean
# anything -- "support" plus "2nd floor" completed an admin-rooms summary.
SPECIFIC = [
    ("ladder tray", r"\bladder tray\b|\bcable tray\b"),
    ("LBB to LBB", r"\blbb to lbb\b|jumpers? from \blbb\b|\blbb\b.*jumper"),
    ("gen to LBB", r"\bgen to lbb\b|\blbb to gen\b"),
    ("vesda", r"\bvesda\b"),
    ("horn strobe", r"\bhorn\b|\bstrobe\b"),
    ("fire alarm EMT", r"fire alarm (red )?\bemt\b|red fire alarm|red \bemt\b|"
                       r"\bfa\b \bemt\b"),
    ("heat trace", r"\bheat.?trace\b"),
    ("load bank", r"\bload bank\b"),
    ("branch power", r"\bbranch power\b|\bbranch electrical\b"),
]
GENERIC = [
    ("device trim", r"\bdevice install\b|\bdevice trim\b|\btrim out\b|"
                    r"\breceptacle\b|\blabeling\b"),
    ("wire pull", r"\bwire pull\b|pulling wire\b|\bpull wire\b|pulled? wire\b"),
    ("lighting", r"install(ing)? \blight\b|\blighting\b|light fixture"),
    ("conduit", r"\bconduit\b|\bemt\b|\brigid\b"),
    ("boxes", r"\bbox(es)?\b|\bj-?box(es)?\b"),
    ("supports", r"\bsupports?\b|\bstrut\b|\bstems?\b|\bclips?\b|\bhangers?\b"),
    ("grounding", r"\bground(ing)?\b|\bbond(ing)?\b"),
]
WORKS = SPECIFIC + GENERIC
SPEC_K = {k for k, _ in SPECIFIC}
# stages that follow the install and must be reported in their own right
LATER = re.compile(r"\binspect|\btest(ing)?\b|\bQA\b|\bQC\b|\bmeg\b|"
                   r"start.?up|\bcommission|\benergi|punch|\bsign.?off\b",
                   re.I)
ROOM = re.compile(
    r"\b(ER\s?R?\s?\d{3}|ERR\s?\d{3}|MV\s?\d{3}|HV\s?\d{3}|GEN\s?\d{3}|"
    r"GL\s?\d{3}|DH\s?\d{3}|POE\s?\d{3}|ELEC\s?\d{3}|MECH\s?\d{3})", re.I)
AREA = re.compile(
    r"\b(penthouse|data hall|gallery|roof|cup|"
    r"[1-4](?:st|nd|rd|th)\s*floor|level\s*[1-4])\b", re.I)
PLACE = re.compile(f"{ROOM.pattern}|{AREA.pattern}", re.I)


def norm_place(s):
    s = re.sub(r"\s+", "", str(s)).upper()
    s = s.replace("ERR", "ERR").replace("ER R", "ERR")
    return s


def works_in(t):
    return {k for k, rx in WORKS if re.search(rx, str(t), re.I)}


def rooms_in(t):
    return {norm_place(m.group(1)) for m in ROOM.finditer(str(t))}


def areas_in(t):
    return {norm_place(m.group(1)) for m in AREA.finditer(str(t))}


def places_in(t):
    return rooms_in(t) | areas_in(t)


win = [r for r in R if D(r["date"]) and LO <= D(r["date"]) <= DD]
print(f"reports in {LO} .. {DD}: {len(win)}  "
      f"({sum(float(r['hours'] or 0) for r in win):,.0f} hours)")

# index the activities we are allowed to touch
WBO = re.compile(r"\*+\s*WBO|by others", re.I)
cands = []
for a in p.activities:
    if a.status == "Completed" or WBO.search(str(a.name)):
        continue
    nm = str(a.name)
    rm = rooms_in(nm) | rooms_in(path(a.wbs_uid))
    ar = areas_in(nm) | areas_in(path(a.wbs_uid))
    wk = works_in(nm)
    if wk and (rm or ar):
        cands.append((a, wk, rm, ar))
print(f"activities with both a work type and a place: {len(cands)} "
      f"of {sum(1 for a in p.activities if a.status != 'Completed')} unfinished")

hits = collections.defaultdict(list)
for r in win:
    txt = f"{r['tasks']} {r['comments']}"
    rw = works_in(txt)
    rrm, rar = rooms_in(txt), areas_in(txt)
    if not rw or not (rrm or rar):
        continue
    for a, wk, rm, ar in cands:
        shared = wk & rw
        if not shared:
            continue
        # a report saying "installing vesda rack" does not put the INSPECTION
        # of that vesda in progress. A verification step only counts when the
        # report says verification happened.
        if LATER.search(str(a.name)) and not LATER.search(txt):
            continue
        # a specific kind of work can be placed by room or by area;
        # a generic one needs the room to agree
        if shared & SPEC_K:
            ok = bool((rm & rrm) or (ar & rar))
        else:
            ok = bool(rm & rrm)
        if ok:
            hits[a.uid].append(r)
print(f"activities matched by at least one report: {len(hits)}")

done, prog = [], []
for uid, rs in hits.items():
    a = byu[uid]
    ds = sorted(D(r["date"]) for r in rs if D(r["date"]))
    first, last = ds[0], ds[-1]
    fore = sorted({str(r["foreman"]) for r in rs})
    comp = [r for r in rs
            if "completed as planned" in str(r["status"]).lower()]
    note = (f"Statused from JER daily reports: {len(rs)} report"
            f"{'s' if len(rs) > 1 else ''} {first} to {last} "
            f"({', '.join(fore)[:60]}) name this work in this area. ")
    if comp:
        cd = max(D(r["date"]) for r in comp if D(r["date"]))
        a.status = "Completed"
        a.actual_start = first.isoformat()
        a.actual_finish = min(cd, DD).isoformat()
        a.actual_duration = a.planned_duration
        a.remaining_duration = 0.0
        a.percent_complete = 100.0
        note += (f"A report of {cd} is marked Work Completed as Planned. "
                 f"CONFIRM: a report naming the work is not a sign-off.")
        done.append((str(a.activity_id), str(a.name)[:40], first, cd, len(rs)))
    else:
        a.status = "In Progress"
        a.actual_start = first.isoformat()
        a.actual_finish = None
        rem = max(1, round((a.planned_duration or 8) / 8) -
                  wdays(first, min(last, DD)))
        a.remaining_duration = float(rem) * 8.0
        a.percent_complete = 50.0
        note += (f"All reports read Work Partially Completed, so it is left "
                 f"running with {rem} days remaining. CONFIRM percent "
                 f"complete with the field.")
        prog.append((str(a.activity_id), str(a.name)[:40], first, last, len(rs)))
    old = a.udfs.get("COMMENTS", "")
    a.udfs["COMMENTS"] = ((((old + "  ") if old else "") + note)[:248])

print(f"\nset Completed: {len(done)}   set In Progress: {len(prog)}")
print(f"\n--- completed ---")
for i, n, f, l, c in sorted(done, key=lambda x: x[3]):
    print(f"   {i:<26}{n:<42}{f} .. {l}  ({c} reports)")
print(f"\n--- in progress (first 24) ---")
for i, n, f, l, c in sorted(prog, key=lambda x: -x[4])[:24]:
    print(f"   {i:<26}{n:<42}from {f}  ({c} reports to {l})")

A1 = collections.Counter(a.status for a in p.activities)
bad = sum(1 for a in p.activities if a.status == "Completed"
          and D(a.actual_finish) and D(a.actual_finish) > DD)
noip = sum(1 for a in p.activities if a.status == "In Progress"
           and (a.remaining_duration or 0) <= 0)
print("\n" + "=" * 74)
print(f"{'':<22}{'before':>10}{'after':>10}")
for s in ("Completed", "In Progress", "Not Started"):
    print(f"{s:<22}{B0.get(s, 0):>10}{A1.get(s, 0):>10}")
print(f"\ncompleted past the data date: {bad}")
print(f"in progress with no remaining duration: {noip}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}")
out = f"{SCR}/MDC1_logic_repaired_v16.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
json.dump({"completed": [[i, n, str(f), str(l), c] for i, n, f, l, c in done],
           "in_progress": [[i, n, str(f), str(l), c] for i, n, f, l, c in prog]},
          open(f"{SCR}/work/mdc1_v16_log.json", "w"), indent=1)
print(f"\nwrote {out}")
