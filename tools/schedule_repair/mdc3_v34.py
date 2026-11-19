"""MDC-3 v34: close the open ends, using MDC-1's own patterns as the source.

I said earlier there was no source for the 28 generator-room starts. That
was wrong -- MDC-1 carries exactly this pattern and I had not looked:

    Install High Steel (Gen 315)  <- Temporary Dry In
    Install High Steel (Gen 316)  <- Install High Steel (Gen 315)
    Install High Steel (Gen 317)  <- Install High Steel (Gen 316)

The first room of a type is fed by the building being dried in, and the
rest follow one another as the crew moves room to room. MDC-3 has its own
Temporary Dry In at MDC3.ROOF.1870, so the pattern transfers directly.

Every orphan here turns out to be the FIRST activity in its room's
sequence -- everything after it is already linked. So each gets the
same treatment, grouped by room type and chained in the order the rooms
are currently scheduled, which keeps the existing dates intact.

The 18 long-lead equipment items are different and are not given logic at
all. They are externally driven deliveries, so they get a Start On
constraint at their planned date: no predecessor, but the date holds
through F9 instead of collapsing onto the data date. Same for the one-offs
that have no room family to join.

Nothing existing is removed.
"""
import collections
import datetime as dt
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402
from engine.xml_writer import write_p6_xml                  # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
HRS = 8.0
DRY_IN = "MDC3.ROOF.1870"           # Temporary Dry In, as MDC-1 uses


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


p = load_xml(f"{SCR}/MDC3_logic_repaired_v33.xml")
ids = {str(a.activity_id): a for a in p.activities}
byu = {a.uid: a for a in p.activities}
Rel = type(p.relations[0])
nxt = max(int(r.uid) for r in p.relations if str(r.uid).isdigit()) + 1


def S(a):
    return D(a.actual_start) or D(a.early_start) or D(a.planned_start)


pre = {r.successor_uid for r in p.relations}
orphans = [a for a in p.activities
           if a.uid not in pre and a.status == "Not Started"]
print(f"orphaned Not Started activities: {len(orphans)}")

lle = [a for a in orphans if ".LLE." in str(a.activity_id)]
rest = [a for a in orphans if ".LLE." not in str(a.activity_id)]


def family(a):
    i = str(a.activity_id)
    if i.startswith("MDC3.GEN.") and ".RM." not in i:
        return "Generator rooms"
    if i.startswith("MDC3.MV."):
        return "MV rooms"
    if i.startswith("MDC3.HV."):
        return "HV rooms"
    return None


fam = collections.defaultdict(list)
oneoff = []
for a in rest:
    f = family(a)
    (fam[f] if f else oneoff).append(a)

# ---- 1. room families: dry-in then crew flow, as MDC-1 does ----------
added = 0
dry = ids.get(DRY_IN)
print(f"\nfeeder: {DRY_IN}  "
      f"\"{dry.name if dry else 'MISSING'}\"  "
      f"{S(dry) if dry else ''}")
for f, group in sorted(fam.items()):
    group.sort(key=lambda a: (S(a) or dt.date(2030, 1, 1), str(a.activity_id)))
    print(f"\n{f}: {len(group)} room starts, chained in date order")
    prev = None
    for a in group:
        src = prev if prev is not None else dry
        if src is None:
            continue
        p.relations.append(Rel(uid=str(nxt), predecessor_uid=src.uid,
                               successor_uid=a.uid,
                               type="Finish to Start", lag=0.0))
        nxt += 1
        added += 1
        note = ("follows Temporary Dry In (MDC-1 pattern)" if prev is None
                else f"follows {src.activity_id} (crew flow, MDC-1 pattern)")
        c = (a.udfs or {}).get("COMMENTS", "")
        a.udfs["COMMENTS"] = (f"Predecessor added in v34: {note}.  " + c)[:248]
        prev = a
    print(f"   first: {group[0].activity_id} <- {DRY_IN}")
    print(f"   then chained through {group[-1].activity_id}")

# ---- 2. LLE deliveries and one-offs: constrain, do not invent logic --
def pin(a, why):
    s = S(a)
    if not s:
        return False
    a.constraint_type = "Start On"
    a.constraint_date = s.isoformat()
    c = (a.udfs or {}).get("COMMENTS", "")
    a.udfs["COMMENTS"] = (f"v34: Start On {s:%m/%d/%y} - {why}. No "
                          f"predecessor by design; update the date when the "
                          f"supplier confirms.  " + c)[:248]
    return True


np_ = sum(pin(a, "externally driven long-lead delivery") for a in lle)
no_ = sum(pin(a, "no room family to join - CONFIRM PREDECESSOR WITH FIELD")
          for a in oneoff)
print(f"\nLLE deliveries pinned with Start On : {np_} of {len(lle)}")
print(f"one-offs pinned and flagged         : {no_} of {len(oneoff)}")
for a in oneoff:
    print(f"   {str(a.activity_id):<26}{str(a.name)[:44]:<46}{S(a)}")

# ---- checks ----------------------------------------------------------
print("\n" + "=" * 68)
pre = {r.successor_uid for r in p.relations}
suc = {r.predecessor_uid for r in p.relations}
ns = [a for a in p.activities
      if a.uid not in pre and a.status == "Not Started"]
cons = [a for a in p.activities if getattr(a, "constraint_type", None)]
print(f"ties added                     : {added}")
print(f"relations                      : {len(p.relations):,}  "
      f"({len(p.relations) / len(p.activities):.2f} per activity)")
print(f"no predecessor                 : "
      f"{sum(1 for a in p.activities if a.uid not in pre)}")
print(f"Not Started with no predecessor: {len(ns)}")
print(f"   of those, now constrained   : "
      f"{sum(1 for a in ns if getattr(a, 'constraint_type', None))}")
print(f"   still floating              : "
      f"{sum(1 for a in ns if not getattr(a, 'constraint_type', None))}")
print(f"constraints set                : {len(cons)}")
seen = set()
print(f"duplicate ties                 : "
      f"{sum(1 for r in p.relations if (r.predecessor_uid, r.successor_uid) in seen or seen.add((r.predecessor_uid, r.successor_uid)))}")
adj = collections.defaultdict(set)
for r in p.relations:
    adj[r.predecessor_uid].add(r.successor_uid)
col = collections.defaultdict(int)
loops = 0
for n in list(adj):
    if col[n]:
        continue
    st = [(n, iter(adj[n]))]
    col[n] = 1
    while st:
        u, it = st[-1]
        try:
            v = next(it)
            if col[v] == 1:
                loops += 1
            elif col[v] == 0:
                col[v] = 1
                st.append((v, iter(adj[v])))
        except StopIteration:
            col[u] = 2
            st.pop()
print(f"circular logic                 : {loops}")
out = f"{SCR}/MDC3_logic_repaired_v34.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
