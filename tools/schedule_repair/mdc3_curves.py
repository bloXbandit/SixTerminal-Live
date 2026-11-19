"""Build the MDC-3 baseline and proposed labour curves, contract to contract.

Method, and why it is this and not something finer.

The two contracts carry different activity sets -- the 02/04 itemises only
28% of our hours by id -- so neither curve can be built activity to
activity. Both are therefore built at SCOPE BUCKET level, and both at the
SAME level, which matters more than the level itself: spreading hours across
a window flattens peaks, so a baseline built finer than the proposal would
make the proposal look smoother and understate the very acceleration the
analysis exists to measure.

Within a bucket the internal shape is preserved rather than smeared. Each
activity keeps its relative position inside its bucket's span in our own
file, and that relative position is mapped onto the target window. A bucket
that ramps in our file still ramps in both curves; only the window moves.

Hours come from our v32, rescaled to the manager's 533,400 basis. The
loading was tested against 20 weeks of JER payroll and predicts the actual
burn within 19% as loaded, against 58% under-prediction when filtered to
electrically-named activities, so it is used as loaded and not filtered.

Scope that the 02/04 does not carry at all -- galleries and HV/HVR rooms --
is excluded from the BASELINE and carried in the PROPOSED. The difference
between the two curve totals is therefore the new scope, stated rather than
absorbed.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
sys.path.insert(0, "/tmp/claude-0/-home-user-SixTerminal-Live/"
                   "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad/work")
from engine.xml_reader import load_xml                      # noqa: E402
from mdc3_1007_bands import BANDS                           # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
# Two bases, both JER's own numbers, not derived here.
#   533,400 is the baseline -- what was planned at contract.
#   605,707 is the projection as of 08-Nov-26.
# The baseline curve no longer has to be reconstructed by subtracting scope
# from today's plan: it is a real figure, and the growth between the two is
# a stated fact rather than something this script computes.
BASIS = 533400.0            # baseline, at contract
PROJECTED = 605707.0        # JER projection as of 08-Nov-26
DATA_DATE = dt.date(2026, 9, 25)
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

# JER payroll, weeks ending Sunday, through 20-Sep-26
ACTUALS = [("2026-04-12", 5, 40), ("2026-04-26", 5, 71), ("2026-05-24", 1, 20),
           ("2026-05-31", 1, 16), ("2026-06-07", 1, 15), ("2026-06-14", 1, 20),
           ("2026-06-21", 2, 60), ("2026-06-28", 2, 47), ("2026-07-05", 3, 51),
           ("2026-07-12", 8, 176), ("2026-07-19", 8, 260),
           ("2026-07-26", 7, 175), ("2026-08-02", 6, 192),
           ("2026-08-09", 8, 223), ("2026-08-16", 6, 182),
           ("2026-08-23", 30, 310), ("2026-08-30", 22, 431),
           ("2026-09-06", 10, 317), ("2026-09-13", 9, 198),
           ("2026-09-20", 35, 748)]


def pdb(s):
    s = s.replace(" A", "").strip()
    d, m, y = s.split("-")
    return dt.date(2000 + int(y), MON[m], int(d))


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def week_end(d):                     # Sunday, matching the manager's sheet
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


# ---------------------------------------------------------------- our file
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o))


loaded = [a for a in p.activities if (a.planned_labor_units or 0) > 0]
RAW = sum(a.planned_labor_units or 0 for a in loaded)
SCALE = BASIS / RAW

GALID = re.compile(r"\.(GL|GAL)\d{3}", re.I)
DHID = re.compile(r"\.DH\d{3}", re.I)


def phase(pt):
    m = re.search(r"Phase (\d)", pt)
    return m.group(1) if m else "0"


def bucket(a):
    pt = path(a.wbs_uid)
    leaf = wbs[a.wbs_uid].name if a.wbs_uid in wbs else ""
    ph = phase(pt)
    if leaf.lower().startswith("gallery") or GALID.search(str(a.activity_id)):
        return f"Galleries - PH{ph}"
    if leaf.lower().startswith("data hall") or DHID.search(str(a.activity_id)):
        return f"Data Halls - PH{ph}"
    for key, lab in [("Electrical Rooms", "Electrical Rooms"),
                     ("Generator Rooms", "Generator Rooms"),
                     ("MV Rooms", "MV Rooms"), ("HV Rooms", "HV Rooms"),
                     ("CUP", "CUP"), ("Elevators", "Elevators"),
                     ("Dry Coolers", "Dry Coolers"),
                     ("Admin Space", "Admin Space")]:
        if key in pt:
            return f"{lab} - PH{ph}" if ph != "0" else lab
    if "Roof" in pt:
        return "Roof"
    if "Site Utilities" in pt or "Exterior" in pt:
        return "Site Utilities"
    if "Structure" in pt:
        return "Structure & Underground"
    return "Other"


# ------------------------------------------------------- the 02/04 windows
C = json.load(open(f"{SCR}/work/mdc3_contract_0204.json"))


def c_window(pred):
    ds = [D(r["start"]) for r in C if pred(r) and D(r["start"])]
    fs = [D(r["finish"]) for r in C if pred(r) and D(r["finish"])]
    return (min(ds), max(fs)) if ds and fs else None


def idpre(*pats):
    rx = re.compile("|".join(pats), re.I)
    return lambda r: bool(rx.search(r["id"]))


# The crosswalk. Left: our bucket. Middle: how the 02/04 carries it.
# Right: the 10/07 band it maps to. NEW means the 02/04 has no counterpart.
XWALK = [
    ("Site Utilities",          idpre(r"^MDC3\.EXT\.SUT"),      "Exterior/Site Utilities"),
    ("Structure & Underground", idpre(r"^MDC3\.(ST|STR|S)\."),  "Structure / Underground"),
    ("Roof",                    idpre(r"^MDC3\.ROOF"),          "Roof"),
    ("Electrical Rooms - PH1",  idpre(r"^MDC3\.PH1\.ER\."),     "Electrical Rooms - Datacenter A"),
    ("Electrical Rooms - PH2",  idpre(r"^MDC3\.PH2\.ER\."),     "Electrical Rooms - Datacenter B"),
    ("Electrical Rooms - PH3",  idpre(r"^MDC3\.PH3\.ER\."),     "Electrical Rooms - Datacenter C"),
    ("MV Rooms - PH1",          idpre(r"^MDC3\.PH1\.MV\."),     "HV / HVR / MV Rooms - Datacenter A"),
    ("MV Rooms - PH2",          idpre(r"^MDC3\.PH2\.MV\."),     "HV / MV Rooms - Datacenter B"),
    ("MV Rooms - PH3",          idpre(r"^MDC3\.PH3\.MV\."),     "HV / HVR / MV Rooms - Datacenter C"),
    ("Generator Rooms - PH1",   idpre(r"^MDC3\.GEN\.(31[5-9]|32[0-35])"), "Generator Rooms - Datacenter A"),
    ("Generator Rooms - PH2",   idpre(r"^MDC3\.GEN\.(30[2468]|31[024]|324)"), "Generator Rooms - Datacenter B"),
    ("Generator Rooms - PH3",   idpre(r"^MDC3\.GEN\.(30[13579]|31[13]|32[678])"), "Generator Rooms - Datacenter C"),
    ("Data Halls - PH1",        idpre(r"^MDC3\.PH1\.DA\.HA"),   "Data Hall 202"),
    ("Data Halls - PH2",        idpre(r"^MDC3\.PH2\.DA\.HA"),   "Data Hall 201 (Expanded)"),
    ("Data Halls - PH3",        idpre(r"^MDC3\.PH3\.DA\.HA"),   "Data Hall 101"),
    ("CUP - PH1",               idpre(r"^MDC3\.PH1\.L\.UP"),    "CUP - Datacenter A"),
    ("CUP - PH2",               idpre(r"^MDC3\.PH2\.(L\.UP|CHLR)"), "CUP - Datacenter B"),
    ("CUP - PH3",               idpre(r"^MDC3\.PH3\.(L\.UP|CHLR)"), "CUP - Datacenter C"),
    ("Admin Space - PH1",       idpre(r"^MDC3\.PH1\.(1ST|2ND)\.FL"), "Admin Space"),
    # Our "Elevators" bucket is MDC3.ST.ELEV.* -- excavate footers,
    # waterproofing and pours for the elevator PITS. Structural work. It was
    # matched to MDC3.PH1.EL.RM in the 02/04, which is Final Paint and Floor
    # Finishes in the elevator machine room: wrong trade, and Jul-Sep 2027
    # instead of during structure. The 02/04 has no elevator-pit activity of
    # its own, so the pit work maps to its structure window.
    ("Elevators - PH1",         idpre(r"^MDC3\.(ST|STR)\."),    "Elevators"),
    ("Elevators - PH2",         idpre(r"^MDC3\.(ST|STR)\."),    "Elevators"),
    ("Elevators - PH3",         idpre(r"^MDC3\.(ST|STR)\."),    "Elevators"),
    # MDC3.LLE.SUB is Long Lead Equipment SUBMITTAL development, Jan-Mar 26.
    # The dry cooler INSTALL carries the same ids in both files --
    # MDC3.PH#.CHLR.#### "Dry Coolers Rigging / Install" -- so this is a
    # direct id match and was putting 3,678 h fourteen months too early.
    ("Dry Coolers - PH1",       idpre(r"^MDC3\.PH1\.CHLR\."),  "PH1 Dry Coolers"),
    ("Dry Coolers - PH2",       idpre(r"^MDC3\.PH2\.CHLR\."),  "PH2 Dry Coolers"),
    ("Dry Coolers - PH3",       idpre(r"^MDC3\.PH3\.CHLR\."),  "PH3 Dry Coolers"),
    ("Galleries - PH1",         None,                           "Galleries (GL 203 & GL 204)"),
    ("Galleries - PH2",         None,                           "Galleries (GL 201 & GL 202)"),
    ("Galleries - PH3",         None,                           "Galleries (GL 101 & GL 102)"),
    ("HV Rooms - PH1",          None,                           "HV Rooms - Datacenter A"),
    ("HV Rooms - PH2",          None,                           "HV Rooms - Datacenter B"),
    ("HV Rooms - PH3",          None,                           "HV Rooms - Datacenter C"),
    # bucket() emits these two without a phase (they sit under Core & Shell).
    # Leaving them out of the crosswalk dropped 3,471 h from both curves.
    # ^MDC3\.GEN\. also catches MDC3.GEN.PRO.* -- the 02/04's GENERAL
    # PROCUREMENT -- which stretched this window to Dec-25..Jul-27. The
    # generator ROOMS are MDC3.GEN.### with digits. These six activities are
    # final paint and floor finishes in the gen rooms, so they belong with
    # the generator rooms, not with long lead delivery.
    ("Generator Rooms",         idpre(r"^MDC3\.GEN\.\d{3}"),    "Generator Rooms - Datacenter A"),
    ("Elevators",               idpre(r"^MDC3\.(ST|STR)\."),    "Elevators"),
    ("Other",                   idpre(r"^MDC3\.(PH\d\.(SK|XFMR|LLE|C\.OUT|CO\.CL)|SOD|LLE|REM|GEN\.PRO)"),
                                "Long Lead Equipment Delivery"),
]
BAND = {nm: (pdb(s), pdb(f)) for nm, dur, s, f, pg in BANDS}


def remap(d, src, dst):
    """Keep a date's relative position inside src, placed inside dst."""
    s0, s1 = src
    d0, d1 = dst
    span = max((s1 - s0).days, 1)
    rel = (d - s0).days / span
    rel = min(max(rel, 0.0), 1.0)
    return d0 + dt.timedelta(days=round(rel * max((d1 - d0).days, 1)))


def spread(acts, src, dst, out, scale):
    for a, u in acts:
        s = D(a.actual_start) or D(a.early_start) or D(a.planned_start)
        f = D(a.actual_finish) or D(a.early_finish) or D(a.planned_finish)
        if not s:
            # An activity with no date of its own was being skipped, which
            # silently dropped 21,070 h -- the whole of MV Rooms PH3 and HV
            # Rooms PH3 -- out of BOTH curves. It still carries hours and
            # still belongs to the bucket, so it is spread across the
            # bucket's full window instead of discarded.
            s, f = src
        if not f or f < s:
            f = s
        ms, mf = remap(s, src, dst), remap(f, src, dst)
        if mf < ms:
            ms, mf = mf, ms
        days = [ms + dt.timedelta(days=i) for i in range((mf - ms).days + 1)]
        days = [d for d in days if d.weekday() < 5] or [ms]
        for d in days:
            out[week_end(d)] += u * scale / len(days)


by_bucket = collections.defaultdict(list)
for a in loaded:
    by_bucket[bucket(a)].append((a, a.planned_labor_units or 0))

base_wk, prop_wk = collections.Counter(), collections.Counter()
rows = []
for name, pred, band in XWALK:
    acts = by_bucket.get(name, [])
    if not acts:
        continue
    hrs = sum(u for _, u in acts) * SCALE
    ds = [D(a.actual_start) or D(a.early_start) or D(a.planned_start)
          for a, _ in acts]
    fs = [D(a.actual_finish) or D(a.early_finish) or D(a.planned_finish)
          for a, _ in acts]
    ds = [d for d in ds if d]
    fs = [d for d in fs if d]
    src = (min(ds), max(fs)) if ds and fs else None
    cw = c_window(pred) if pred else None
    pw = BAND.get(band)
    # A bucket where EVERY activity is dateless has no span of its own, so
    # there is no relative shape to preserve: it is spread flat across the
    # target window rather than dropped.
    if cw:
        spread(acts, src or cw, cw, base_wk, SCALE)
    if pw:
        spread(acts, src or pw, pw, prop_wk, SCALE)
    rows.append({"bucket": name, "hours": round(hrs, 1),
                 "acts": len(acts),
                 "base_start": cw[0].isoformat() if cw else None,
                 "base_finish": cw[1].isoformat() if cw else None,
                 "prop_band": band,
                 "prop_start": pw[0].isoformat() if pw else None,
                 "prop_finish": pw[1].isoformat() if pw else None,
                 "new_scope": pred is None})

# ---- normalise each curve onto its own basis -------------------------
# The baseline carries the 533,400 planned at contract, across the scope the
# 02/04 actually contains. The proposed carries the 605,707 now projected,
# across all scope including what the 02/04 has no counterpart for.
bt = sum(base_wk.values())
if bt:
    for w in base_wk:
        base_wk[w] *= BASIS / bt

act_wk = {dt.date.fromisoformat(d): h for d, _, h in ACTUALS}
ACT_TOT = sum(h for _, _, h in ACTUALS)
fwd = {w: v for w, v in prop_wk.items() if w > DATA_DATE}
fwd_tot = sum(fwd.values())
k = (PROJECTED - ACT_TOT) / fwd_tot if fwd_tot else 0
cur_wk = collections.Counter()
for w, h in act_wk.items():
    cur_wk[w] += h
for w, v in fwd.items():
    cur_wk[w] += v * k

base_tot = sum(base_wk.values())
print(f"our v32 {RAW:,.0f} h -> basis {BASIS:,.0f} (x{SCALE:.5f})")
print(f"buckets mapped          : {len(rows)}")
newh = sum(r['hours'] for r in rows if r['new_scope'])
print(f"BASELINE curve total    : {base_tot:,.0f} h   (planned at contract, "
      f"02/04 scope, {sum(1 for r in rows if r['new_scope'])} buckets excluded)")
print(f"PROPOSED curve total    : {sum(cur_wk.values()):,.0f} h   "
      f"(actual {ACT_TOT:,.0f} + forecast {PROJECTED - ACT_TOT:,.0f})")
print(f"GROWTH booked by JER    : {PROJECTED - BASIS:,.0f} h  "
      f"({100*(PROJECTED-BASIS)/BASIS:.1f}%)")
print(f"new scope we can SEE    : {newh:,.0f} h  (galleries + HV/HVR, "
      f"no 02/04 counterpart)")
print(f"   -> unexplained gap   : {newh - (PROJECTED-BASIS):,.0f} h")
bpk = max(base_wk, key=base_wk.get)
cpk = max(cur_wk, key=cur_wk.get)
print(f"\nBASELINE peak : {base_wk[bpk]:>8,.0f} h/wk = "
      f"{base_wk[bpk] / 50:>5.0f} crew   week ending {bpk}")
print(f"PROPOSED peak : {cur_wk[cpk]:>8,.0f} h/wk = "
      f"{cur_wk[cpk] / 50:>5.0f} crew   week ending {cpk}")
print(f"manager's file peak for MDC-3: 17,500 h/wk = 350 crew at 04/04/27")

allw = sorted(set(base_wk) | set(cur_wk))
json.dump({"scale": SCALE, "basis": BASIS, "projected": PROJECTED, "data_date": DATA_DATE.isoformat(),
           "actual_total": ACT_TOT, "rows": rows,
           "weeks": [{"w": w.isoformat(),
                      "base": round(base_wk.get(w, 0), 1),
                      "cur": round(cur_wk.get(w, 0), 1),
                      "actual": round(act_wk.get(w, 0), 1)} for w in allw]},
          open(f"{SCR}/work/mdc3_curve_data.json", "w"), indent=0)
print(f"\nweeks: {len(allw)}  {allw[0]} .. {allw[-1]}")
print(f"wrote {SCR}/work/mdc3_curve_data.json")
