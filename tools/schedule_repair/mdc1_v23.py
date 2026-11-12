"""MDC-1 v23: build the six rooms we carry nothing for, and give the data
halls a tracked finish.

IT IS SIX ROOMS, NOT TWELVE. The earlier count double-counted the galleries:
Holder's activity IDs read GAL101/GAL102/GAL202 while the activity NAMES read
"(GL202 South-East)", so GL and GAL are the same three rooms seen two ways.

WHAT IS JER'S, BY HOLDER'S OWN LIST:

  ERR 201 (PH2) + ERR 202 (PH1)   58 activities, 26 JER electrical
  HVR 101 (PH3)                   22 activities, 11 JER electrical
  GAL 101 (PH3) 102 (PH3) 202(PH2) 160 activities, 55 JER electrical

The two ERR rooms and HVR 101 are electrical and HV rooms, and Holder's steps
in them are our steps -- OH Rough-In Electrical/FA, Device Trim Out, Testing
and Inspections, Start ups of Equipment, Set CRAH's. So they are cloned from
our own room template rather than invented: ER 203's 24-activity backbone for
the ERR rooms, HV 101's 19 for HVR 101, hours, durations, calendars and the
internal logic along with them.

The galleries are not electrical rooms, so the ER backbone does not fit --
there is no high steel or bus duct in a gallery. Only 34% of Holder's gallery
scope is JER's, and his own names give it exactly: MEP OH Hangers, In-wall
Rough-In, In-Wall Inspections, OH Device Trim-Out, FanWal Feeders / Conduit,
Install EDP/CDU Feeder Conduit, Install Vesda, Install Lights, NETA Testing.
Nine steps, taken at our usual one-backbone-per-room granularity rather than
Holder's per-sub-area split, and loaded from what the same work carries
elsewhere in our file where it exists.

HOLDER HAS PROGRESS IN ALL SIX. 48 of his activities in these rooms are
finished and we hold no record of any of them. So each new row is matched to
his by work stage and takes his actual dates where he has them -- building
them all Not Started would book a month of finished work as outstanding.

THE DATA HALLS stay as they are. Holder carries 241 activities per hall
against our 16, but 205 of his are per-circuit Level 3 Cx, and we are not
carrying that scope. What was missing is the finish: there was nothing in our
file to track a data hall being done against. Each hall now gets a completion
milestone dated off the last of Holder's own Cx activities for it, tied to
the back of our own work in that hall, so the finish can be tracked without
importing the circuits.
"""
import collections
import datetime as dt
import json
import re
import statistics
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.schedule_model import Activity, Relation, WBSNode
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
R = json.load(open(f"{SCR}/work/mdc1_exs2.json"))
p = load_xml(f"{SCR}/MDC1_logic_repaired_v22.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


DD = D(p.data_date)


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


def toks(n):
    return {t for t in norm(n).split() if len(t) > 2}


B = dict(acts=len(p.activities), rels=len(p.relations),
         wbs=len(p.wbs_nodes),
         bud=sum(a.planned_labor_units or 0 for a in p.activities))
nau = max(int(a.uid) for a in p.activities if str(a.uid).isdigit()) + 1
nru = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
nwu = max(int(w.uid) for w in p.wbs_nodes if str(w.uid).isdigit()) + 1
have = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
out = collections.defaultdict(set)
for r in p.relations:
    out[r.predecessor_uid].add(r.successor_uid)


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


def tie(s, t, typ="Finish to Start", lag=0.0):
    global nru
    if s == t or (s, t) in have or reaches(t, s):
        return False
    p.relations = list(p.relations) + [Relation(
        uid=str(nru), predecessor_uid=s, successor_uid=t, type=typ, lag=lag)]
    have.add((s, t))
    out[s].add(t)
    nru += 1
    return True


def folder(name, parent, code):
    global nwu
    w = WBSNode(uid=str(nwu), name=name, code=code, parent_uid=parent,
                sequence_num=0)
    p.wbs_nodes = list(p.wbs_nodes) + [w]
    wbs[w.uid] = w
    nwu += 1
    return w


def phase_node(ph):
    for w in p.wbs_nodes:
        if re.fullmatch(rf"Phase {ph} \(Build-Out\)", str(w.name).strip()):
            return w
    return None


def child(parent_uid, name):
    for w in p.wbs_nodes:
        if w.parent_uid == parent_uid and str(w.name).strip() == name:
            return w
    return None


# ---- Holder's rows for these rooms, by room -------------------------
hold = collections.defaultdict(list)
for d in R:
    s = f"{d['Activity ID']} {d['Activity Name']}"
    for rm, rx in (("ERR201", r"ERR\s*201|ERR201"),
                   ("ERR202", r"ERR\s*202|ERR202"),
                   ("HVR101", r"HVR\s*101|HVR101"),
                   ("GAL101", r"GAL101|GL101"),
                   ("GAL102", r"GAL102|GL102"),
                   ("GAL202", r"GAL202|GL202")):
        if re.search(rx, s, re.I):
            hold[rm].append(d)
            break

# ---- loading reference ----------------------------------------------
ref = collections.defaultdict(list)
for a in p.activities:
    h = a.planned_labor_units or 0
    if h:
        ref[norm(a.name)].append(h)
med = {k: statistics.median(v) for k, v in ref.items()}

GALLERY = ["MEP OH Hangers", "In-wall Rough-In", "In-Wall Inspections",
           "FanWal Feeders / Conduit", "Install EDP/CDU Feeder Conduit",
           "Install Lights", "Install Vesda", "OH Device Trim-Out",
           "NETA Testing"]
GAL_H = {"MEP OH Hangers": 110, "In-wall Rough-In": 112,
         "In-Wall Inspections": 70, "FanWal Feeders / Conduit": 141,
         "Install EDP/CDU Feeder Conduit": 141, "Install Lights": 148,
         "Install Vesda": 77, "OH Device Trim-Out": 106, "NETA Testing": 211}

# ---- plan: room -> (phase, parent folder name, template or GALLERY) --
TPL_ER = next((w for w in p.wbs_nodes
               if re.search(r"/ Electrical Rooms / ER 203$", path(w.uid))), None)
TPL_HV = next((w for w in p.wbs_nodes
               if re.search(r"/ HV Rooms / HV 101$", path(w.uid))), None)
PLAN = [("ERR 201", "ERR201", 2, "Electrical Rooms", TPL_ER),
        ("ERR 202", "ERR202", 1, "Electrical Rooms", TPL_ER),
        ("HVR 101", "HVR101", 3, "HV Rooms", TPL_HV),
        ("GAL 101", "GAL101", 3, "Galleries", None),
        ("GAL 102", "GAL102", 3, "Galleries", None),
        ("GAL 202", "GAL202", 2, "Galleries", None)]

print("=" * 84)
print("BUILDING THE SIX ROOMS")
print("=" * 84)
summary = []
for rname, rkey, ph, parentname, tpl in PLAN:
    pn = phase_node(ph)
    if pn is None:
        print(f"   {rname}: no Phase {ph} node, skipped")
        continue
    par = child(pn.uid, parentname) or folder(parentname, pn.uid,
                                              f"MDC1.PH{ph}.{parentname[:3].upper()}")
    f = folder(rname, par.uid, f"MDC1.PH{ph}.{rkey}")
    hrows = hold.get(rkey, [])
    hbuild = [d for d in hrows
              if not re.search(r"Level \d Cx", str(d["Activity Name"]))]

    # ---- the rows to create
    if tpl is not None:
        src = sorted([a for a in p.activities if a.wbs_uid == tpl.uid],
                     key=lambda a: str(a.activity_id))
        # Some template rows carry the template's own room in the name --
        # "VESDA Rack Install - ER203" -- which would clone through as
        # "VESDA Rack Install - ER203 (ERR 201)". Strip it before renaming.
        tn = str(tpl.name).strip()
        strip = re.compile(
            r"\s*[-:]?\s*\(?" + tn.replace(" ", r"\s*") + r"\)?\b", re.I)
        rows = [(strip.sub("", str(a.name)).strip(" -:"),
                 a.planned_labor_units or 0,
                 a.planned_duration or 8.0, a.calendar_uid, a.activity_type,
                 a) for a in src]
    else:
        rows = [(n, float(GAL_H.get(n, med.get(norm(n), 110))), 8.0 * 3,
                 None, "Task Dependent", None) for n in GALLERY]

    made, carried = [], 0
    newmap = {}
    for i, (nm, h, dur, cal, atype, srca) in enumerate(rows):
        cal = cal or sorted({a.calendar_uid for a in p.activities},
                            key=lambda c: -sum(1 for a in p.activities
                                               if a.calendar_uid == c))[0]
        a = Activity(
            uid=str(nau), activity_id=f"MDC1.PH{ph}.{rkey}.{1000 + i * 10}",
            name=f"{nm} ({rname})"[:118], wbs_uid=f.uid, calendar_uid=cal,
            activity_type=atype or "Task Dependent", status="Not Started",
            planned_duration=float(dur), remaining_duration=float(dur),
            actual_duration=0.0, percent_complete=0.0,
            planned_labor_units=float(h), actual_labor_units=0.0,
            remaining_labor_units=float(h))
        # Carry Holder's actuals. A gallery is split into sub-areas on his
        # side -- GAL 202 is 64 rows, two passes of 32 -- so a stage can be
        # finished in one sub-area and not the other. Taking the best single
        # match would then call the whole stage done off the finished half,
        # which is how FanWal Feeders came out complete ahead of its own
        # inspection. So every row matching the stage is collected and the
        # stage is only complete when all of them are.
        gt = toks(nm)
        hits = []
        for d in hbuild:
            ht = toks(d["Activity Name"])
            if not ht or not gt:
                continue
            if any((w in gt) != (w in ht) for w in
                   ("rough", "close", "term", "test", "pull", "set", "trim",
                    "inspect", "startup", "hanger", "light", "vesda")):
                continue
            j = len(ht & gt) / len(ht | gt)
            if j >= 0.5:
                hits.append(d)
        note = (f"Added in v23: Holder carries {rname} in Phase {ph} and our "
                f"file had no activity for it. ")
        fin = [D(d.get("Finish")) for d in hits
               if str(d.get("Finish Status")) == "Actual" and D(d.get("Finish"))]
        sta = [D(d.get("Start")) for d in hits
               if str(d.get("Start Status")) == "Actual" and D(d.get("Start"))]
        ids = ", ".join(str(d["Activity ID"]) for d in hits[:2])
        if hits and len(fin) == len(hits) and max(fin) <= DD:
            a.status = "Completed"
            a.actual_start = (min(sta) if sta else max(fin)).isoformat()
            a.actual_finish = max(fin).isoformat()
            a.actual_duration = float(dur)
            a.remaining_duration = 0.0
            a.percent_complete = 100.0
            a.actual_labor_units = 0.0
            a.remaining_labor_units = 0.0
            carried += 1
            note += (f"Complete in all {len(hits)} of Holder's rows for this "
                     f"stage ({ids}), last {max(fin):%m/%d/%y}. Hours sit on "
                     f"the payroll record.")
        elif sta and min(sta) <= DD:
            a.status = "In Progress"
            a.actual_start = min(sta).isoformat()
            pc = round(100.0 * len(fin) / len(hits), 1) if hits else 50.0
            a.percent_complete = min(pc if pc else 50.0, 95.0)
            a.remaining_duration = float(dur) * (1 - a.percent_complete / 100)
            a.remaining_labor_units = float(h) * (1 - a.percent_complete / 100)
            carried += 1
            note += (f"Started {min(sta):%m/%d/%y} per Holder; {len(fin)} of "
                     f"{len(hits)} of his rows for this stage are finished, "
                     f"so {a.percent_complete:.0f}% - confirm with the field.")
        elif hits:
            note += f"Not started per Holder ({ids})."
        else:
            note += ("Steps and loadings follow our own room template."
                     if tpl is not None else
                     "Steps are JER's scope from Holder's gallery list.")
        a.udfs["COMMENTS"] = note[:248]
        p.activities = list(p.activities) + [a]
        if srca is not None:
            newmap[srca.uid] = a.uid
        made.append(a)
        nau += 1

    # ---- internal logic
    inside = 0
    if tpl is not None:
        for r in p.relations[:]:
            if r.predecessor_uid in newmap and r.successor_uid in newmap:
                if tie(newmap[r.predecessor_uid], newmap[r.successor_uid],
                       r.type, r.lag):
                    inside += 1
    else:
        for i in range(len(made) - 1):
            if tie(made[i].uid, made[i + 1].uid):
                inside += 1

    # ---- lead-in and lead-out, copied from where the template sits
    lin = lout = 0
    if tpl is not None:
        srcuids = set(newmap)
        for r in p.relations[:]:
            if r.successor_uid in newmap and r.predecessor_uid not in srcuids \
                    and r.predecessor_uid not in newmap.values():
                if tie(r.predecessor_uid, newmap[r.successor_uid],
                       r.type, r.lag):
                    lin += 1
            elif r.predecessor_uid in newmap and r.successor_uid not in srcuids \
                    and r.successor_uid not in newmap.values():
                if tie(newmap[r.predecessor_uid], r.successor_uid,
                       r.type, r.lag):
                    lout += 1
    else:
        # A gallery has no template, so it had nothing to copy ties from and
        # would sit as an island. Wire it the way our own rooms are wired,
        # and to the hall it feeds -- the EDPs live in the gallery, so the
        # hall cannot be commissioned until their feeders are in.
        def first(rx, scope=None):
            for a in p.activities:
                if not re.search(rx, str(a.name), re.I):
                    continue
                if scope and scope not in path(a.wbs_uid):
                    continue
                return a
            return None

        byname = {norm(x.name.split(" (")[0]): x for x in made}
        fa = first(r"FA Permit Approved")
        cxm = first(rf"Level 3 Commissioning Start \(PH{ph}\)")
        fad = first(rf"Fire Alarm Devices Complete \(PH{ph}\)")
        ves = byname.get(norm("Install Vesda"))
        neta = byname.get(norm("NETA Testing"))
        hang = byname.get(norm("MEP OH Hangers"))
        for q, s in ((fa, ves), (fa, hang)):
            if q is not None and s is not None and tie(q.uid, s.uid):
                lin += 1
        for q, s in ((neta, cxm), (ves, fad)):
            if q is not None and s is not None and tie(q.uid, s.uid):
                lout += 1
        # the gallery -> data hall tie is made once the hall's finish
        # milestone exists, further down
    summary.append((rname, ph, len(made), carried, inside, lin, lout,
                    sum(x.planned_labor_units or 0 for x in made),
                    len(hbuild)))
    print(f"\n{rname}  (Phase {ph})  ->  {path(f.uid)[:70]}")
    print(f"   {len(made)} activities built"
          f"{f' from {path(tpl.uid).split(chr(47))[-1].strip()}' if tpl else ' from Holder gallery scope'}"
          f"   {sum(x.planned_labor_units or 0 for x in made):,.0f} h")
    print(f"   Holder has {len(hbuild)} build activities here; "
          f"{carried} of ours take his actual dates")
    print(f"   logic: {inside} internal, {lin} lead-in, {lout} lead-out")

# ===== close the sequence inside the new rooms =======================
# Holder has no row for some prep steps -- Layout Wall Penatration, Install
# Hangers, the *WBO drywall framing -- so they stayed Not Started while the
# work that sits on top of them came back complete from his record. A wall
# cannot be rough-in complete before it is framed, so the predecessor is
# closed out behind its successor and the inference is recorded on the row.
print("\n" + "=" * 84)
print("CLOSING THE SEQUENCE INSIDE THE NEW ROOMS")
print("=" * 84)
NEWRX = re.compile(r"^MDC1\.PH\d\.(ERR20[12]|HVR101|GAL(101|102|202))\.")
newu = {a.uid for a in p.activities if NEWRX.search(str(a.activity_id))}
WORKDAYS = frozenset({0, 1, 2, 3, 4})


def back(d, days):
    n = 0
    while n < max(int(days), 1):
        d -= dt.timedelta(days=1)
        if d.weekday() in WORKDAYS:
            n += 1
    return d


byu = {a.uid: a for a in p.activities}
closed = []
for _ in range(8):
    hit = False
    for r in p.relations:
        q, s = byu.get(r.predecessor_uid), byu.get(r.successor_uid)
        if not q or not s or q.uid not in newu:
            continue
        if q.status != "Not Started" or s.status not in ("Completed",
                                                         "In Progress"):
            continue
        anchor = D(s.actual_start) or D(s.actual_finish)
        if not anchor:
            continue
        dur = (q.planned_duration or 8.0) / 8
        q.status = "Completed"
        q.actual_finish = anchor.isoformat()
        q.actual_start = back(anchor, dur).isoformat()
        q.actual_duration = q.planned_duration
        q.remaining_duration = 0.0
        q.percent_complete = 100.0
        q.remaining_labor_units = 0.0
        c = (q.udfs or {}).get("COMMENTS", "")
        q.udfs["COMMENTS"] = ((((c + "  ") if c else "") + (
            f"Closed out in v23 by inference: {s.activity_id} is "
            f"{s.status.lower()} and cannot be, with this outstanding. "
            f"Holder carries no row for this step. CONFIRM WITH THE "
            f"FIELD."))[:248])
        closed.append((str(q.activity_id), str(q.name), str(s.activity_id),
                       anchor))
        hit = True
    if not hit:
        break
print(f"predecessors closed out: {len(closed)}")
for i, n, src, d in closed:
    print(f"   {i:<24}{n[:42]:<44}behind {src:<22}{d}")

# ===== data hall finish ==============================================
print("\n" + "=" * 84)
print("DATA HALL FINISH MILESTONES")
print("=" * 84)
pred = collections.defaultdict(list)
for r in p.relations:
    pred[r.successor_uid].append(r)
for dh, ph in (("DH 101", 3), ("DH 201", 2), ("DH 202", 1)):
    f = next((w for w in p.wbs_nodes if dh in str(w.name)), None)
    if f is None:
        continue
    kids = {f.uid} | {x.uid for x in p.wbs_nodes if x.parent_uid == f.uid}
    ours = [a for a in p.activities if a.wbs_uid in kids]
    hcx = [D(d.get("Finish")) for d in R
           if re.search(dh.replace(" ", r"[\s\.]*"),
                        f"{d['Activity ID']} {d['Activity Name']}", re.I)
           and re.search(r"Level \d Cx", str(d["Activity Name"]))
           and D(d.get("Finish"))]
    if not hcx:
        print(f"   {dh}: Holder has no Cx finish date, skipped")
        continue
    last = max(hcx)
    a = Activity(
        uid=str(nau), activity_id=f"MDC1.PH{ph}.{dh.replace(' ', '')}.9000",
        name=f"{dh} Complete - Level 3 Cx Done (per Holder)",
        wbs_uid=f.uid, calendar_uid=ours[0].calendar_uid if ours else None,
        activity_type="Finish Milestone", status="Not Started",
        planned_duration=0.0, remaining_duration=0.0, actual_duration=0.0,
        percent_complete=0.0, planned_labor_units=0.0,
        actual_labor_units=0.0, remaining_labor_units=0.0,
        planned_start=last.isoformat(), planned_finish=last.isoformat())
    a.udfs["COMMENTS"] = (
        f"Finish marker for {dh}, added in v23. Holder carries "
        f"{len(hcx)} Level 3 Cx activities for this hall, the last finishing "
        f"{last:%m/%d/%y}; we are not carrying that per-circuit scope, so "
        f"this is what the hall's completion is tracked against.")[:248]
    p.activities = list(p.activities) + [a]
    nau += 1
    n = 0
    for x in ours:
        if x.status != "Completed" and not any(
                r.predecessor_uid == x.uid for r in p.relations):
            if tie(x.uid, a.uid):
                n += 1
    if n == 0:
        for x in ours:
            if tie(x.uid, a.uid):
                n += 1
                break
    # The EDPs that feed this hall sit in the galleries of the same phase, so
    # the hall's Cx cannot finish before their feeder conduit is in.
    gal = 0
    for x in p.activities:
        if re.search(r"EDP/CDU Feeder Conduit", str(x.name), re.I) \
                and f"Phase {ph} (Build-Out)" in path(x.wbs_uid):
            if tie(x.uid, a.uid):
                gal += 1
    print(f"   {dh}: milestone {a.activity_id} at {last:%m/%d/%Y}  "
          f"({len(hcx)} Holder Cx activities, {len(ours)} of ours, "
          f"{n} tie(s) in, {gal} gallery feeder tie(s))")

# ===== checks ========================================================
print("\n" + "=" * 84)
print("CHECKS")
print("=" * 84)
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
isl = []
for rname, rkey, ph, _, _ in PLAN:
    us = [a for a in p.activities
          if str(a.activity_id).startswith(f"MDC1.PH{ph}.{rkey}.")]
    uu = {a.uid for a in us}
    pin = sum(1 for r in p.relations
              if r.successor_uid in uu and r.predecessor_uid not in uu)
    pout = sum(1 for r in p.relations
               if r.predecessor_uid in uu and r.successor_uid not in uu)
    if not pin or not pout:
        isl.append((rname, pin, pout))
    print(f"   {rname:<9}{len(us):>3} acts   lead-in {pin:>3}   lead-out {pout:>3}"
          f"{'   <-- ISLAND' if (not pin or not pout) else ''}")
mx = max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()),
         default=0)
print(f"\nlongest activity id: "
      f"{max(len(str(a.activity_id)) for a in p.activities)}  (P6 limit 40)")
print(f"longest name: {max(len(str(a.name)) for a in p.activities)}  "
      f"(limit 120)")
print(f"longest UDF: {mx}  (limit 255)")

A = dict(acts=len(p.activities), rels=len(p.relations),
         wbs=len(p.wbs_nodes),
         bud=sum(a.planned_labor_units or 0 for a in p.activities))
print("\n" + "=" * 84)
print(f"{'':<22}{'v22':>12}{'v23':>12}{'change':>11}")
for k, lbl in (("acts", "activities"), ("rels", "relationships"),
               ("wbs", "WBS folders"), ("bud", "budget hours")):
    print(f"{lbl:<22}{B[k]:>12,.0f}{A[k]:>12,.0f}{A[k] - B[k]:>+11,.0f}")
print(f"\n{'room':<10}{'ph':>4}{'built':>7}{'actuals':>9}{'hours':>9}"
      f"{'Holder build':>14}")
for rn, ph, n, c, ins, li, lo, h, hb in summary:
    print(f"{rn:<10}{ph:>4}{n:>7}{c:>9}{h:>9,.0f}{hb:>14}")
outp = f"{SCR}/MDC1_logic_repaired_v23.xml"
write_p6_xml(p, outp, include_resources=False, include_udfs=True)
print(f"\nwrote {outp}")
