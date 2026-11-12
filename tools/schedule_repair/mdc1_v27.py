"""MDC-1 v27: write the inferred electrician counts onto the 87.

1,547 of the 1,634 activities that carry hours have a Number of Electricians
value; 87 do not, so a filter on that field in P6 leaves them behind and they
get no resource line items at all.

The counts written here are the ones on tab 3 of the assignment worksheet,
derived the same way so the two agree: an exact name twin where one exists
(42 of them -- a Bus Duct Install is a Bus Duct Install whichever room it is
in, scaled by how this activity's hours compare to the peers'), a name family
where there is no exact twin (5), and the project's own trend of 5.07 hours
per electrician per day for the rest (40).

Nothing else changes. No date, no status, no relationship, no labour unit --
only the UDF, and a note on each saying it was inferred and from what, so
anything that looks wrong can be found and overridden in P6 by filtering the
COMMENTS field.
"""
import collections
import re
import statistics
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
wbs = {w.uid: w for w in p.wbs_nodes}
FIELD = "Number of Electricians"


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


def ne(a):
    v = (a.udfs or {}).get(FIELD)
    try:
        n = int(float(str(v).strip()))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


def fam(n):
    w = norm(n).split()
    return " ".join(w[:2]) if len(w) >= 2 else norm(n)


def dd(a):
    return max((a.planned_duration or 8) / 8, 1)


def h(a):
    return a.planned_labor_units or 0


work = [a for a in p.activities
        if not str(a.activity_id).startswith("MDC1.LAB")]
have = [a for a in work if ne(a) and h(a) > 0]
need = [a for a in work if not ne(a) and h(a) > 0]
rate = statistics.median([h(a) / (ne(a) * dd(a)) for a in have])
print(f"carrying hours: {len(have) + len(need)}   "
      f"with a count {len(have)}   without {len(need)}")
print(f"project trend: {rate:,.2f} h per electrician per day")

byname = collections.defaultdict(list)
byfam = collections.defaultdict(list)
for a in have:
    byname[norm(a.name)].append(a)
    byfam[fam(a.name)].append(a)

print("\n" + "=" * 96)
print(f"{'activity':<24}{'name':<40}{'hours':>7}{'crew':>6}{'how':>18}"
      f"{'h/elec/day':>12}")
print("=" * 96)
src = collections.Counter()
done = []
for a in sorted(need, key=lambda x: (norm(x.name), str(x.activity_id))):
    peers, how = byname.get(norm(a.name)), "exact name match"
    if not peers:
        peers, how = byfam.get(fam(a.name)), "similar work"
    if peers:
        pc = statistics.median([ne(x) for x in peers])
        ph = statistics.median([h(x) for x in peers])
        n = max(1, round(pc * (h(a) / ph))) if ph else int(pc)
        ev = (f"{len(peers)} peer activities of this kind, median crew "
              f"{pc:,.0f} on {ph:,.0f} h")
    else:
        n = max(1, round(h(a) / (dd(a) * rate)))
        how = "project trend"
        ev = (f"{h(a):,.0f} h over {dd(a):,.0f} days at the project's "
              f"{rate:,.2f} h per electrician per day")
    a.udfs[FIELD] = str(n)
    # The note goes FIRST, not appended. COMMENTS is capped at 248 characters
    # for P6, and 61 of these 87 already carry a comment long enough that an
    # appended note is sliced straight off -- the count would be set with no
    # record of where it came from. Leading with it means the flag always
    # survives and the older note is what gets trimmed.
    c = (a.udfs or {}).get("COMMENTS", "")
    note = f"CREW {n} INFERRED v27 ({how}): {ev}. Override if the field differs."
    a.udfs["COMMENTS"] = (note + (f"  {c}" if c else ""))[:248]
    src[how] += 1
    done.append((a, n, how))
    print(f"{str(a.activity_id):<24}{str(a.name)[:38]:<40}{h(a):>7,.0f}"
          f"{n:>6}{how:>18}{h(a) / (n * dd(a)):>12,.1f}")

print("\n" + "=" * 96)
for k, v in src.most_common():
    print(f"   {v:>4}  {k}")
print(f"\nassignments these add: {sum(n for _, n, _ in done):,}")
r2 = [h(a) / (n * dd(a)) for a, n, _ in done]
print(f"implied h/electrician/day: median {statistics.median(r2):,.1f}  "
      f"(project {rate:,.1f})")

# ---- checks ---------------------------------------------------------
after_have = [a for a in work if ne(a) and h(a) > 0]
after_need = [a for a in work if not ne(a) and h(a) > 0]
print("\n" + "=" * 96)
print("CHECKS")
print("=" * 96)
print(f"activities carrying hours with a count: "
      f"{len(have)} -> {len(after_have)}")
print(f"activities carrying hours WITHOUT a count: "
      f"{len(need)} -> {len(after_need)}")
tot = sum(ne(a) for a in after_have)
print(f"total resource line items the file now implies: {tot:,}")
print(f"total budget (unchanged): "
      f"{sum(h(a) for a in work):,.0f} h")
print(f"activities {len(p.activities)}   relations {len(p.relations)}")
print(f"completed {sum(1 for a in p.activities if a.status == 'Completed')}   "
      f"in progress {sum(1 for a in p.activities if a.status == 'In Progress')}")
print(f"longest UDF: "
      f"{max((len(str(v)) for a in p.activities for v in (a.udfs or {}).values()), default=0)}"
      f"   (P6 limit 255)")
c = collections.Counter(ne(a) for a in after_have)
print(f"\ncrew values now in the file: {len(c)} distinct")
print(f"   {dict(sorted(c.items()))}")
out = f"{SCR}/MDC1_logic_repaired_v27.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
