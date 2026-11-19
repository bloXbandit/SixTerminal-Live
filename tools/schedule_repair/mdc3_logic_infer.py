"""Infer relationship TYPE and LAG for the ties missing from v32.

Holder's 11/04 export names predecessors as bare activity ids -- no type,
no lag. Importing all of them as FS-0 would be wrong for a seventh of them:
122 of the 1,668 have a successor that already starts before its
predecessor finishes, and an FS-0 tie there invents a constraint that does
not exist. That is the same mistake that put 11 false FS ties into MDC-1
before it was caught.

So the type is read off the geometry the two activities already have. For a
tie A -> B, each type implies a lag:

    FS   lag = workdays(A.finish -> B.start)
    SS   lag = workdays(A.start  -> B.start)
    FF   lag = workdays(A.finish -> B.finish)

whichever implies the smallest lag is the relationship that best explains
where the two activities sit today, so that is the one used, carrying its
lag. A tie that needs a big lag under every type is not really driving
anything, and is dropped rather than guessed at.

Existing logic is never touched -- this only adds, per the standing rule.
"""
import collections
import datetime as dt
import json
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
MAX_LAG = 30            # beyond this the tie is not driving; drop it


def D(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def wd(a, b):
    """Working days from a to b, signed."""
    if a is None or b is None:
        return None
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if b >= a else (b, a)
    n, d = 0, lo
    while d < hi:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return sign * n


H = json.load(open(f"{SCR}/work/mdc3_h1104.json"))
p = load_xml(f"{SCR}/MDC3_logic_repaired_v32.xml")
ours = {str(a.activity_id): a for a in p.activities}
byu = {a.uid: str(a.activity_id) for a in p.activities}
have = {(byu[r.predecessor_uid], byu[r.successor_uid])
        for r in p.relations
        if r.predecessor_uid in byu and r.successor_uid in byu}

cand = set()
for h in H:
    tgt = h["id"]
    if tgt not in ours:
        continue
    for tok in re.split(r"[,;]\s*", str(h.get("pred") or "")):
        tok = tok.strip()
        if tok in ours and tok != tgt and (tok, tgt) not in have:
            cand.add((tok, tgt))
cand = sorted(cand)
print(f"candidate ties (both ends ours, not already present): {len(cand)}")


def S(a):
    return D(a.actual_start) or D(a.early_start) or D(a.planned_start)


def F(a):
    return D(a.actual_finish) or D(a.early_finish) or D(a.planned_finish)


decided, dropped = [], collections.Counter()
for a, b in cand:
    A, B = ours[a], ours[b]
    sa, fa, sb, fb = S(A), F(A), S(B), F(B)
    if not all((sa, fa, sb, fb)):
        dropped["missing dates"] += 1
        continue
    opts = [("FS", wd(fa, sb)), ("SS", wd(sa, sb)), ("FF", wd(fa, fb))]
    opts = [(t, l) for t, l in opts if l is not None]
    t, lag = min(opts, key=lambda x: abs(x[1]))
    if abs(lag) > MAX_LAG:
        dropped[f"lag over {MAX_LAG}d under every type"] += 1
        continue
    if lag < 0 and t == "FS":
        # a negative FS lag is a lead; the geometry is really SS
        t, lag = "SS", wd(sa, sb)
        if abs(lag) > MAX_LAG:
            dropped["negative FS, SS also too far"] += 1
            continue
    decided.append((a, b, t, max(lag, 0) if t == "FS" else lag))

print(f"typed from the dates : {len(decided)}")
for k, v in dropped.most_common():
    print(f"   dropped, {k}: {v}")

ty = collections.Counter(t for _, _, t, _ in decided)
print(f"\ntype mix: {dict(ty)}")
lags = [l for _, _, _, l in decided]
print(f"lag: zero on {sum(1 for l in lags if l == 0)}, "
      f"positive {sum(1 for l in lags if l > 0)}, "
      f"negative {sum(1 for l in lags if l < 0)}, "
      f"median {sorted(lags)[len(lags) // 2]}")

# ---- cycle check, adding one at a time -------------------------------
adj = collections.defaultdict(set)
for x, y in have:
    adj[x].add(y)


def reaches(src, dst, limit=200000):
    if src == dst:
        return True
    seen, stack, n = {src}, [src], 0
    while stack and n < limit:
        u = stack.pop()
        n += 1
        for v in adj.get(u, ()):
            if v == dst:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


keep, cyc = [], 0
for a, b, t, lag in decided:
    if reaches(b, a):
        cyc += 1
        continue
    adj[a].add(b)
    keep.append((a, b, t, lag))
print(f"\nwould have closed a loop, skipped : {cyc}")
print(f"TIES TO ADD                       : {len(keep)}")
ty = collections.Counter(t for _, _, t, _ in keep)
print(f"   final type mix: {dict(ty)}")
json.dump([{"pred": a, "succ": b, "type": t, "lag": l}
           for a, b, t, l in keep],
          open(f"{SCR}/work/mdc3_new_ties.json", "w"), indent=0)
print(f"\nwrote {SCR}/work/mdc3_new_ties.json")
print("\nsample of what will be added:")
for a, b, t, l in keep[:12]:
    print(f"   {a:<24} -> {b:<24} {t}{'' if l == 0 else f'{l:+}d'}")
