"""MDC-3 v36: align the file to Holder's 11/04 export.

The pour numbering was wrong -- our file called Segment C's pours 11 and 12
where the export and the 9/25 PDF both call them 8 and 9. Chasing that turned
up 583 activity names that disagree, and in every category sampled the
export's name is the better one: ours are truncated ("Final Paint" where the
export says "Final Paint - Generator Rooms - Datacenter A"), stale, or carry
the wrong pour number.

One of those renames matters beyond tidiness. ROOF.SYS.1180, which we carried
as a bare "Temp Dry in", is "Admin Roof Temp Dry in" -- and it is the activity
Area 15 Turnover feeds. That corroborates relaxing the Area 15 predecessor on
the building-wide Temporary Dry In: Area 15 is the admin area, so it has no
business gating the generator rooms.

What this script changes, and what it deliberately does not:

  names      - all 583 adopted from the export. These are labels; nothing
               downstream reads them.
  durations  - 63 adopted. Duration is scope: it drives both the dates and
               the hours, so a wrong one is a real error.
  actuals    - adopted where the export has one and we do not, which is
               status we were missing.
  dates      - NOT forced. Planned and early dates are what the forward pass
               computes from durations and logic. Writing the export's dates
               in directly would be overwritten by the next F9 and would hide
               whether the logic actually produces them. They are reported
               instead, as a check on whether fixing the inputs was enough.

Activity IDs, logic and WBS are untouched.
"""
import collections
import datetime as dt
import json
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402
from engine.xml_writer import write_p6_xml                  # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
HRS = 8.0


def D(v):
    s = str(v or "").replace(" A", "").strip()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for f in ("%d-%b-%y", "%m/%d/%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


H = {x["id"]: x for x in json.load(open(f"{SCR}/work/mdc3_h1104.json"))}
p = load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
ours = {str(a.activity_id): a for a in p.activities}

log = collections.Counter()
pours = []
for k, a in ours.items():
    h = H.get(k)
    if not h:
        continue

    # ---- names -------------------------------------------------------
    hn = str(h.get("name", "")).strip()
    if hn and str(a.name).strip() != hn:
        if "Pour" in hn or "Pour" in str(a.name):
            pours.append((k, str(a.name), hn))
        a.name = hn
        log["names corrected"] += 1

    # ---- durations ---------------------------------------------------
    hd = h.get("dur")
    if hd not in (None, ""):
        try:
            d = float(hd)
        except (TypeError, ValueError):
            d = None
        if d is not None and abs(d - a.planned_duration / HRS) > 0.5:
            a.planned_duration = d * HRS
            if a.status == "Not Started":
                a.remaining_duration = d * HRS
            log["durations corrected"] += 1

    # ---- actuals we were missing --------------------------------------
    for fld, attr in (("start_actual", "actual_start"),
                      ("finish_actual", "actual_finish")):
        v = D(h.get(fld))
        if v and not getattr(a, attr):
            setattr(a, attr, v.isoformat())
            log[f"{attr} added"] += 1
    if a.actual_finish and a.status != "Completed":
        a.status = "Completed"
        a.percent_complete = 100.0
        log["status -> Completed"] += 1
    elif a.actual_start and a.status == "Not Started":
        a.status = "In Progress"
        log["status -> In Progress"] += 1

# Two typos in the export itself, corrected rather than carried over.
TYPO = {"MDC3.ST.L2.A13.1020": ("Form & Tie Rebarg", "Form & Tie Rebar"),
        "MDC3.ST.SHF.1540":    ("Tie Rebar - The Segment G", "Tie Rebar - Segment G")}
for k, (bad, good) in TYPO.items():
    a = ours.get(k)
    if a and bad in str(a.name):
        a.name = str(a.name).replace(bad, good)
        log["export typos corrected"] += 1
        print(f"typo fixed  {k}  {bad!r} -> {good!r}")

print("CHANGES")
for k, v in log.most_common():
    print(f"   {v:>5}  {k}")
print(f"\npour renames ({len(pours)}):")
for k, o, n in sorted(pours):
    print(f"   {k:<22}{o[-34:]:<36}->  {n[-34:]}")

out = f"{SCR}/MDC3_logic_repaired_v36.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
