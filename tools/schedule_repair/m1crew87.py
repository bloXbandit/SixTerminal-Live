"""The 87 activities that carry hours but no electrician count.

Three ways to infer one, in order of how much they are worth:

  A  the same activity name elsewhere already has a count. A Wall Rough-In is
     a Wall Rough-In whichever room it is in, so the peer count carries.
  B  no name twin, but the work sits in a family that does -- MEP Underground
     Power against the other underground runs.
  C  nothing to match on, so it falls back to the job's own trend: hours
     divided by duration divided by the hours a single electrician puts in
     per day across the whole file.
"""
import collections
import re
import statistics
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
p = load_xml(f"{SCR}/MDC1_logic_repaired_v26.xml")
wbs = {w.uid: w for w in p.wbs_nodes}


def path(u):
    o, g = [], 0
    while u in wbs and g < 40:
        o.append(wbs[u].name)
        u = wbs[u].parent_uid
        g += 1
    return " / ".join(reversed(o)).replace("25-1539-INT-5 / ", "")


def ne(a):
    v = (a.udfs or {}).get("Number of Electricians")
    try:
        n = int(float(str(v).strip()))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def norm(n):
    s = re.sub(r"\*+\s*WBO|\(.*?\)", "", str(n), flags=re.I)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s).strip().lower())


def dd(a):
    return max((a.planned_duration or 8) / 8, 1)


def h(a):
    return a.planned_labor_units or 0


work = [a for a in p.activities
        if not str(a.activity_id).startswith("MDC1.LAB")]
have = [a for a in work if ne(a) and h(a) > 0]
need = [a for a in work if not ne(a) and h(a) > 0]
print(f"activities needing a count: {len(need)}")

# the job's own trend: hours per electrician per day
rate = statistics.median([h(a) / (ne(a) * dd(a)) for a in have])
print(f"job trend: one electrician books a median {rate:,.2f} h per day")

byname = collections.defaultdict(list)
for a in have:
    byname[norm(a.name)].append(a)
# a coarser family key: the leading words of the name
def fam(n):
    w = norm(n).split()
    return " ".join(w[:2]) if len(w) >= 2 else norm(n)


byfam = collections.defaultdict(list)
for a in have:
    byfam[fam(a.name)].append(a)

print("\n" + "=" * 104)
print(f"{'activity':<24}{'name':<40}{'hours':>7}{'days':>5}"
      f"{'crew':>6}{'how':>6}{'peers':>7}{'h/elec/day':>12}")
print("=" * 104)
out = []
src = collections.Counter()
for a in sorted(need, key=lambda x: (norm(x.name), str(x.activity_id))):
    peers = byname.get(norm(a.name))
    how, n = "", None
    if peers:
        # scale the peer count by how this activity's hours compare
        pc = statistics.median([ne(x) for x in peers])
        ph = statistics.median([h(x) for x in peers])
        n = max(1, round(pc * (h(a) / ph))) if ph else int(pc)
        how, k = "A name", len(peers)
    else:
        peers = byfam.get(fam(a.name))
        if peers:
            pc = statistics.median([ne(x) for x in peers])
            ph = statistics.median([h(x) for x in peers])
            n = max(1, round(pc * (h(a) / ph))) if ph else int(pc)
            how, k = "B fam", len(peers)
        else:
            n = max(1, round(h(a) / (dd(a) * rate)))
            how, k = "C rate", 0
    src[how] += 1
    out.append((a, n, how, k))
    print(f"{str(a.activity_id):<24}{str(a.name)[:38]:<40}{h(a):>7,.0f}"
          f"{dd(a):>5,.0f}{n:>6}{how:>6}{k:>7}{h(a) / (n * dd(a)):>12,.1f}")

print("\n" + "=" * 104)
for k, v in src.most_common():
    print(f"   {v:>4}  inferred from {k}")
print(f"\nassignments this adds: {sum(n for _, n, _, _ in out):,}")
print(f"hours they carry     : {sum(h(a) for a, _, _, _ in out):,.0f}")
r2 = [h(a) / (n * dd(a)) for a, n, _, _ in out]
print(f"implied h/electrician/day: median {statistics.median(r2):,.1f}  "
      f"(job median {rate:,.1f})")
odd = [(a, n) for a, n, _, _ in out if h(a) / (n * dd(a)) > 12]
print(f"any implying over 12 h/electrician/day: {len(odd)}")
for a, n in odd:
    print(f"   {str(a.activity_id):<24}{str(a.name)[:40]:<42}"
          f"{h(a):,.0f}h / {n} / {dd(a):,.0f}d")
