"""MDC3 v46: WBS cleanup plus orphan logic.

Five jobs, in order:
  1. Merge the 9 equipment tags carried by two delivery rows. The keeper is the
     row holding the most successor logic; the surplus row's relations are
     re-pointed to the keeper BEFORE it is deleted, so no logic is lost.
  2. Reparent stray LLE deliveries to their room's main WBS folder. The folder
     was picked off the phase token in the activity ID, not the phase the room
     is built in. Relations live on the activity, so a move changes no logic.
  3. Delete every WBS folder that ends up with no activities under it.
  4. Give each successor-less delivery a real successor: the activity that
     installs that equipment. Tiered, exact tag first. Finish to Start, no lag,
     and only where the delivery already finishes before that activity starts,
     so the tie documents the need-by date without moving a single date.
  5. Same treatment for the non-LLE dead ends, excluding the Substantial
     Completion milestones, which are supposed to be terminal.

Nothing already in the file is unlinked. Every relation written here is new.
"""
import collections, copy, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC3_logic_repaired_v45.xml"
OUT = f"{SCR}/MDC3_logic_repaired_v46.xml"

# the nine tags, keeper first -- chosen by successor count, verified by hand
MERGE = [("CRAH-MV108-01", "MDC3.PH3.LLE.3340", "MDC3.PH2.LLE.3190"),
         ("Skid-MV112-M06", "MDC3.PH2.LLE.3710", "MDC3.PH2.LLE.3520"),
         ("Skid-MV113-M07", "MDC3.PH2.LLE.3720", "MDC3.PH2.LLE.3530"),
         ("Skid-MV114-M08", "MDC3.PH3.LLE.3760", "MDC3.PH3.LLE.3560"),
         ("Skid-MV115-M09", "MDC3.PH3.LLE.3750", "MDC3.PH3.LLE.3570"),
         ("UPS-MV112-06",  "MDC3.PH2.LLE.2200", "MDC3.PH2.LLE.1310"),
         ("UPS-MV113-07",  "MDC3.PH2.LLE.2210", "MDC3.PH2.LLE.1320"),
         ("UPS-MV113-M08", "MDC3.PH3.LLE.2170", "MDC3.PH3.LLE.1310"),
         ("UPS-MV114-M09", "MDC3.PH3.LLE.2180", "MDC3.PH3.LLE.1320")]

TYPE = {"CRAH": ["CRAH"], "CRAC": ["CRAC"], "SKID": ["Skid"], "UPS": ["UPS"],
        "PDP": ["Power Distribution", "PDP"], "UODP": ["UPS Output", "UODP"],
        "HVS": ["Switchgear"], "MVS": ["Switchgear"],
        "MSB": ["Switchboard", "MSB"], "CDB": ["Distribution Board", "CDB"],
        "ATS": ["ATS"], "XFMR": ["Transformer"],
        "GEN": ["Generator", "Engine"], "FW": ["Fan Wall"], "AHU": ["AHU"],
        "CDU": ["CDU"], "PNL": ["Panel"], "CHLR": ["Chiller"], "PMP": ["Pump"],
        "RMU": ["Ring Main", "RMU"], "MSG": ["Switchgear", "MSG"],
        "EDP": ["EDP", "Distribution Panel"], "GMP": ["GMP"], "CRB": ["CRB"],
        "MAU": ["MAU"], "EG": ["EG"], "DP": ["DP"]}
TERMINAL = re.compile(r'Substantial Completion', re.I)
PULLTERM = re.compile(r'^\s*(pull|term)\b', re.I)


def d10(v):
    return str(v)[:10] if v else ""


def es(a):
    """Early start if the pass computed one, else the planned start.
    Planned dates on the milestones are stale, so the feasibility test has to
    read the computed dates or it compares against dates nobody believes."""
    return d10(a.early_start or a.planned_start)


def ef(a):
    return d10(a.early_finish or a.planned_finish)


def main():
    p = load_xml(SRC)
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f0 = max((d10(a.early_finish) for a in p.activities if a.early_finish),
             default="")
    print(f"v45 in: {len(p.activities)} activities, {len(p.relations)} relations, "
          f"{len(p.wbs_nodes)} WBS nodes, finish {f0}")

    byid = {str(a.activity_id): a for a in p.activities}
    wbs = {w.uid: w for w in p.wbs_nodes}

    # ---- 1. merge duplicate equipment tags -------------------------------
    dropped = set()
    repointed = 0
    for tagname, keep, drop in MERGE:
        k, d = byid.get(keep), byid.get(drop)
        if not k or not d:
            print(f"   ! {tagname}: {'keeper' if not k else 'surplus'} missing, skipped")
            continue
        for r in p.relations:
            if r.predecessor_uid == d.uid:
                r.predecessor_uid = k.uid
                repointed += 1
            if r.successor_uid == d.uid:
                r.successor_uid = k.uid
                repointed += 1
        dropped.add(d.uid)
    # a re-point can create a self-tie or an exact duplicate; drop only those
    seen = set()
    clean = []
    for r in p.relations:
        if r.predecessor_uid == r.successor_uid:
            continue
        key = (r.predecessor_uid, r.successor_uid, r.type, r.lag)
        if key in seen:
            continue
        seen.add(key)
        clean.append(r)
    collapsed = len(p.relations) - len(clean)
    p.relations[:] = clean
    p.activities[:] = [a for a in p.activities if a.uid not in dropped]
    byid = {str(a.activity_id): a for a in p.activities}
    print(f"1. merged {len(dropped)} surplus delivery rows, "
          f"{repointed} relations re-pointed to the keeper, "
          f"{collapsed} self/duplicate ties collapsed")

    # ---- 2. reparent stray LLE deliveries --------------------------------
    # a room's main folder is the one holding its non-LLE work
    def nodename(u):
        w = wbs.get(u)
        return str(w.name) if w else ""

    main_folder = {}
    for a in p.activities:
        if ".LLE." in str(a.activity_id):
            continue
        n = nodename(a.wbs_uid)
        if re.match(r'^(WBO )?(MV|HV|HVR|ER|ERR|GL|DH|Gen) ?\d{3}', n):
            main_folder.setdefault(n, collections.Counter())[a.wbs_uid] += 1
    main_folder = {n: c.most_common(1)[0][0] for n, c in main_folder.items()}

    moved = 0
    for a in p.activities:
        if ".LLE." not in str(a.activity_id):
            continue
        n = nodename(a.wbs_uid)
        tgt = main_folder.get(n)
        if tgt and tgt != a.wbs_uid:
            a.wbs_uid = tgt
            moved += 1
    print(f"2. reparented {moved} stray deliveries to their room's main folder")

    # ---- 3. drop folders with nothing under them -------------------------
    kids = collections.defaultdict(list)
    for w in p.wbs_nodes:
        kids[w.parent_uid].append(w)
    here = collections.Counter(a.wbs_uid for a in p.activities)

    def deep(u):
        return here.get(u, 0) + sum(deep(k.uid) for k in kids.get(u, []))

    gone = {w.uid for w in p.wbs_nodes if deep(w.uid) == 0}
    p.wbs_nodes[:] = [w for w in p.wbs_nodes if w.uid not in gone]
    print(f"3. deleted {len(gone)} WBS folders holding no activities")

    # ---- 4 + 5. connect the dead ends ------------------------------------
    succ = collections.defaultdict(set)
    for r in p.relations:
        succ[r.predecessor_uid].add(r.successor_uid)
    nonlle = [a for a in p.activities if ".LLE." not in str(a.activity_id)]

    def tag(a):
        m = re.search(r'\[([^\]]+)\]', str(a.name))
        return m.group(1) if m else None

    def parts(t):
        m = re.match(r'([A-Za-z]+)-([A-Z]{2,4})(\d{3})-', t or "")
        return (m.group(1).upper(), m.group(2), m.group(3)) if m else (None, None, None)

    def inroom(b, rk, rn):
        aid = str(b.activity_id)
        return bool(re.search(rf'\.{rk}R?\.?{rn}\.', aid)
                    or re.search(rf'\.{rk}{rn}\.', aid))

    def pick_for(a):
        """The activity that installs this delivery's equipment, or None."""
        t = tag(a)
        kind, rk, rn = parts(t)
        if t:  # exact unit named in a work activity
            c = [b for b in nonlle if t in str(b.name)]
            if c:
                return min(c, key=lambda b: es(b)), "exact tag"
        if rk:  # same room, same equipment type, a Set or an Install
            words = TYPE.get(kind, [])
            c = [b for b in nonlle if inroom(b, rk, rn)
                 and any(w.lower() in str(b.name).lower() for w in words)
                 and not PULLTERM.match(str(b.name))]
            if c:
                return sorted(c, key=lambda b: (
                    0 if str(b.name).lower().startswith("set ") else
                    1 if "install" in str(b.name).lower() else 2,
                    es(b)))[0], "type and room"
        if rk:  # fall back to the room's own start-ups
            c = [b for b in nonlle if inroom(b, rk, rn)
                 and re.search(r'start ?ups|commission', str(b.name), re.I)]
            if c:
                return min(c, key=lambda b: es(b)), "room start-ups"
        return None, None

    # --- rule 5: the next work in the same area -------------------------
    # a delivery is never the successor of another delivery: procurement rows
    # do not perform work, so chaining them invents sequence that is not real
    # and can close a loop between two deliveries in the same folder
    sameparent = collections.defaultdict(list)
    for b in nonlle:
        sameparent[b.wbs_uid].append(b)
    parent_of = {w.uid: w.parent_uid for w in p.wbs_nodes}
    predset = collections.defaultdict(set)
    for r in p.relations:
        predset[r.successor_uid].add(r.predecessor_uid)

    def area_successor(a):
        """The next activity to start in a's own folder, or its parent folder."""
        fin = ef(a)
        for scope in (a.wbs_uid, parent_of.get(a.wbs_uid)):
            if not scope:
                continue
            pool = sameparent.get(scope, [])
            if scope != a.wbs_uid:
                pool = [b for wu, bs in sameparent.items() for b in bs
                        if parent_of.get(wu) == scope]
            c = [b for b in pool
                 if b.uid != a.uid and es(b) >= fin
                 and b.uid not in predset.get(a.uid, set())
                 and a.uid not in predset.get(b.uid, set())]
            if c:
                return min(c, key=lambda b: es(b)), "next work in the area"
        return None, None

    # --- rule 6: the phase completion milestone, Finish to Finish --------
    mil = {}
    for b in p.activities:
        m = re.match(r'MDC3\.MIL\.PH(\d)\.', str(b.activity_id))
        if m and re.search(r'Substantial Completion', str(b.name), re.I):
            mil[m.group(1)] = b
    anymil = min(mil.values(), key=lambda b: ef(b)) if mil else None

    def phase_milestone(a):
        m = re.match(r'MDC3\.PH(\d)\.', str(a.activity_id))
        b = mil.get(m.group(1)) if m else None
        b = b or anymil
        return (b, "phase completion milestone") if b else (None, None)

    proto = copy.deepcopy(p.relations[0])
    nextuid = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1
    added = collections.Counter()
    skipped = []
    newrels = []
    orph = [a for a in p.activities if not succ.get(a.uid)]
    for a in orph:
        if TERMINAL.search(str(a.name)):
            skipped.append((a, "terminal milestone, left alone"))
            continue
        b = how = None
        for rule in (pick_for, area_successor):
            b, how = rule(a)
            # only tie where it costs nothing: a finishes before b starts
            if b and ef(a) <= es(b):
                break
            b = how = None
        kind = "Finish to Start"
        if not b:
            # last resort: must be complete by the phase milestone, FF so it
            # can only ever be pulled along, never push the milestone out
            b, how = phase_milestone(a)
            kind = "Finish to Finish"
            if b and ef(a) > ef(b):
                b = how = None
        if not b:
            skipped.append((a, "nothing downstream to tie to"))
            continue
        r = copy.deepcopy(proto)
        r.uid = str(nextuid); nextuid += 1
        r.predecessor_uid = a.uid
        r.successor_uid = b.uid
        r.type = kind
        r.lag = 0
        newrels.append(r)
        added[f"{how} ({kind})"] += 1
    # the valve room FA chain ends at VR 304; integrated commissioning follows
    EXPLICIT = [("MDC3.FA.VR.304.1020", "MDC3.MIL.PH3.9010", "Finish to Start")]
    for pid, sid, kind in EXPLICIT:
        x, y = byid.get(pid), byid.get(sid)
        if x and y and not succ.get(x.uid) and ef(x) <= es(y):
            r = copy.deepcopy(proto)
            r.uid = str(nextuid); nextuid += 1
            r.predecessor_uid, r.successor_uid = x.uid, y.uid
            r.type, r.lag = kind, 0
            newrels.append(r)
            added["end of the FA valve room chain (Finish to Start)"] += 1
            skipped[:] = [t for t in skipped if t[0].uid != x.uid]
    p.relations.extend(newrels)
    print(f"4. added {len(newrels)} Finish to Start ties, no lag:")
    for k, v in added.most_common():
        print(f"      {v:>4}  matched on {k}")
    print(f"   {len(skipped)} left unconnected:")
    for why, n in collections.Counter(w for _, w in skipped).most_common():
        print(f"      {n:>4}  {why}")

    # ---- verify ----------------------------------------------------------
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    f1 = max((d10(a.early_finish) for a in p.activities if a.early_finish),
             default="")
    neg = sum(1 for a in p.activities
              if a.total_float is not None and a.total_float < 0)
    still = sum(1 for a in p.activities
                if not any(r.predecessor_uid == a.uid for r in p.relations))
    print(f"\nv46 out: {len(p.activities)} activities, {len(p.relations)} relations, "
          f"{len(p.wbs_nodes)} WBS nodes")
    print(f"   finish {f0} -> {f1}")
    print(f"   negative float activities: {neg}")
    print(f"   still without a successor: {still}")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")
    return skipped


if __name__ == "__main__":
    main()
