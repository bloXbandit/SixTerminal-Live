"""
id_normalizer.py — bring stray activity codes back onto the job's own pattern.

A real schedule is coded to a convention: MDC1.MIL.1130 for milestones,
MDC1.FDG.1290 in foundations. Activities added later — through the grid, a
paste, or the agent — get a generic A1000/A1010 instead, and after a few
months the file carries two coding systems at once. Exported to P6 that reads
as sloppy; worse, an id no longer tells you where the work sits.

This works out what the job's convention actually IS, per folder, from the ids
already in the file — nothing is configured — and proposes a conforming id for
every row that drifted off it. Renaming is safe for the network: relations
bind activities by uid, and activity_id is the user-visible code only.

Two rules keep the result predictable:

  The number is kept when it can be. A1290 in a folder coded MDC1.FDG. becomes
  MDC1.FDG.1290, not the next free slot — whatever meaning the number carried
  survives, and re-running changes nothing.

  A folder with no convention of its own inherits one. New sub-folders are
  usually where the generic ids collect, so the nearest ancestor that IS coded
  supplies the prefix rather than the folder being skipped.

Nothing here writes. plan() reports what it would do; the caller applies it.
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .schedule_model import Project

# An id this can reason about ends in digits: "MDC1.FDG.1290", "A1000".
# Anything else ("MILESTONE-A", "1290b") is left strictly alone — a code we
# cannot take apart is not one to guess at.
_ID_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")
_SEP_RE = re.compile(r"[.\-_/ ]+")

# Below this share of the file, there is no single convention to normalize
# TOWARDS — a genuinely half-and-half schedule is a decision for the user, not
# something to pick a winner in silently.
_MIN_SHARE = 0.6

# A runner-up convention this close to the leader means the file carries two
# coding systems, not one system and some drift. Four stray codes beside a
# thousand is 0.4%; three against two is 67%, and only the second is a
# decision the user has to make.
_RIVAL_SHARE = 0.4

_DEFAULT_STRIDE = 10


def parse_id(activity_id: str) -> Optional[Tuple[str, int, int]]:
    """(prefix, number, digit width), or None when the id has no trailing number."""
    m = _ID_RE.match((activity_id or "").strip())
    if not m:
        return None
    digits = m.group("num")
    return m.group("prefix"), int(digits), len(digits)


def family(prefix: str) -> str:
    """
    The leading segment of a prefix — "MDC1.FDG." and "MDC1.MIL." are one
    family, "A" is another. This is what separates the job's coding from the
    generic ids that drifted in beside it.
    """
    for part in _SEP_RE.split(prefix or ""):
        if part:
            return part.upper()
    return (prefix or "").upper()


def _stride(numbers: List[int]) -> int:
    """The step the folder is already numbered in — 10 unless it says otherwise."""
    ordered = sorted(set(numbers))
    diffs = Counter(b - a for a, b in zip(ordered, ordered[1:]) if b > a)
    if not diffs:
        return _DEFAULT_STRIDE
    step, _ = diffs.most_common(1)[0]
    return step if step > 0 else _DEFAULT_STRIDE


class _Allocator:
    """Hands out ids that are free now and stay free for the rest of the run."""

    def __init__(self, project: Project):
        self.taken = {(a.activity_id or "").strip() for a in project.activities}

    def claim(self, candidate: str) -> bool:
        if candidate in self.taken:
            return False
        self.taken.add(candidate)
        return True

    def next_free(self, prefix: str, start: int, stride: int, width: int) -> str:
        n = max(start, 0)
        for _ in range(100000):
            candidate = f"{prefix}{n:0{width}d}"
            if self.claim(candidate):
                return candidate
            n += stride
        raise RuntimeError("Could not find a free activity id")


def is_structured(prefix: str) -> bool:
    """
    Is this a coded prefix, or the generic one P6 hands out?

    "MDC1.PH2.ER." is a convention — it carries the job, the phase and the
    area. "A" is what an activity gets when nobody coded it. The separator is
    what tells them apart, and the distinction is load-bearing: without it the
    normalizer picks whichever family is MORE COMMON, and in a folder where
    the strays outnumber the coded rows it renames MDC1.PH2.ER.1000 to A2030 —
    normalizing backwards, onto the junk, which is the exact opposite of the
    job this module exists to do.
    """
    return bool(_SEP_RE.search((prefix or "").strip()))


def _dominant_family(project: Project) -> Tuple[Optional[str], float, float]:
    """
    The convention to normalize TOWARDS: (family, share, rival ratio).

    A structured family always beats a generic one, however many generic ids
    there are — those are the rows being FIXED, not evidence of a convention.
    The share is then measured against the other structured families, because
    the question is "is there one convention here", and letting the strays
    dilute it would refuse to run on precisely the files that need it most.

    The rival ratio is the runner-up measured against the leader, and it is
    what share alone cannot say. Three XYZ ids against two MDC1 ids is 60% —
    over any sane threshold — while plainly being two live conventions rather
    than one convention and some drift. A thousand MDC1 ids against four
    strays is the same 60%-plus and is not ambiguous at all. Comparing the top
    two directly separates those cases; the share cannot.
    """
    counts, structured = Counter(), Counter()
    for a in project.activities:
        parsed = parse_id(a.activity_id)
        if not parsed:
            continue
        fam = family(parsed[0])
        counts[fam] += 1
        if is_structured(parsed[0]):
            structured[fam] += 1
    if not counts:
        return None, 0.0, 0.0
    pool = structured or counts
    ranked = pool.most_common()
    name, n = ranked[0]
    rival = (ranked[1][1] / n) if len(ranked) > 1 and n else 0.0
    return name, n / sum(pool.values()), rival


def _descendants(project: Project, root_uid: str) -> set:
    out, grew = {root_uid}, True
    while grew:
        grew = False
        for w in project.wbs_nodes:
            if w.parent_uid in out and w.uid not in out:
                out.add(w.uid)
                grew = True
    return out


def _wbs_path(project: Project, uid: Optional[str]) -> str:
    by_uid = {w.uid: w for w in project.wbs_nodes}
    parts, cur, guard = [], by_uid.get(uid), 0
    while cur is not None and guard < 200:
        parts.insert(0, cur.name)
        cur = by_uid.get(cur.parent_uid)
        guard += 1
    return " / ".join(parts)


def plan(project: Project, root_uid: Optional[str] = None) -> Dict[str, Any]:
    """
    Work out which activity ids are off the job's pattern, and what each
    should become. Writes nothing.

    Returns {convention, share, changes, left_alone, skipped, scanned}.
    `changes` is the reviewable list — one entry per activity, carrying the
    uid it applies to so the caller can apply exactly what was shown.
    """
    fam, share, rival = _dominant_family(project)
    if fam is None:
        return {"convention": None, "share": 0.0, "changes": [], "left_alone": [],
                "skipped": ["No activity id in this schedule ends in a number, "
                            "so there is no pattern to follow."],
                "scanned": len(project.activities)}
    if share < _MIN_SHARE or rival >= _RIVAL_SHARE:
        return {"convention": fam, "share": share, "changes": [], "left_alone": [],
                "skipped": [f"No single convention covers this schedule — "
                            f"'{fam}' is only {share:.0%} of the coded ids, and "
                            f"another convention is nearly as common. "
                            f"Normalizing would be picking a winner rather than "
                            f"following one."],
                "scanned": len(project.activities)}

    in_scope = _descendants(project, root_uid) if root_uid else None
    acts = [a for a in project.activities
            if in_scope is None or a.wbs_uid in in_scope]

    # What each folder is coded as, judged only on ids that already conform.
    direct: Dict[Optional[str], Counter] = {}
    numbers: Dict[str, List[int]] = {}
    widths: Dict[str, Counter] = {}
    for a in project.activities:                 # whole project, so an in-scope
        parsed = parse_id(a.activity_id)         # folder can inherit from outside
        if not parsed or family(parsed[0]) != fam:
            continue
        prefix, num, width = parsed
        direct.setdefault(a.wbs_uid, Counter())[prefix] += 1
        numbers.setdefault(prefix, []).append(num)
        widths.setdefault(prefix, Counter())[width] += 1

    by_uid = {w.uid: w for w in project.wbs_nodes}
    project_wide = Counter()
    for counter in direct.values():
        project_wide.update(counter)

    prefix_cache: Dict[Optional[str], Optional[str]] = {}

    def target_prefix(wbs_uid: Optional[str]) -> Optional[str]:
        """
        The prefix this folder's rows should carry: its own if it has one,
        otherwise the nearest coded ancestor's, otherwise the project's.
        """
        if wbs_uid in prefix_cache:
            return prefix_cache[wbs_uid]
        own = direct.get(wbs_uid)
        if own:
            prefix_cache[wbs_uid] = own.most_common(1)[0][0]
            return prefix_cache[wbs_uid]
        # everything nested under this folder, then upwards through its parents
        node = by_uid.get(wbs_uid)
        if node is not None:
            branch = Counter()
            for uid in _descendants(project, node.uid):
                branch.update(direct.get(uid) or {})
            if branch:
                prefix_cache[wbs_uid] = branch.most_common(1)[0][0]
                return prefix_cache[wbs_uid]
            cur, guard = by_uid.get(node.parent_uid), 0
            while cur is not None and guard < 200:
                up = Counter()
                for uid in _descendants(project, cur.uid):
                    up.update(direct.get(uid) or {})
                if up:
                    prefix_cache[wbs_uid] = up.most_common(1)[0][0]
                    return prefix_cache[wbs_uid]
                cur, guard = by_uid.get(cur.parent_uid), guard + 1
        chosen = project_wide.most_common(1)[0][0] if project_wide else None
        prefix_cache[wbs_uid] = chosen
        return chosen

    alloc = _Allocator(project)
    changes, left_alone, skipped = [], [], []
    seen_skips = set()

    for a in acts:
        aid = (a.activity_id or "").strip()
        parsed = parse_id(aid)
        if not parsed:
            left_alone.append({"activity_id": aid, "name": a.name,
                               "why": "no number at the end of the code"})
            continue
        prefix, num, width = parsed
        if family(prefix) == fam:
            continue                              # already on the convention
        want = target_prefix(a.wbs_uid)
        if not want:
            note = f"'{_wbs_path(project, a.wbs_uid) or 'this folder'}' has no coded rows to follow"
            if note not in seen_skips:
                seen_skips.add(note)
                skipped.append(note)
            continue
        # Keep the number the row already carries when that slot is free — it
        # may well be meaningful, and it makes a re-run a no-op.
        target_width = (widths.get(want) or Counter({width: 1})).most_common(1)[0][0]
        candidate = f"{want}{num:0{target_width}d}"
        if not alloc.claim(candidate):
            pool = numbers.get(want) or []
            step = _stride(pool)
            start = (max(pool) + step) if pool else num
            candidate = alloc.next_free(want, start, step, target_width)
        numbers.setdefault(want, []).append(int(parse_id(candidate)[1]))
        changes.append({
            "uid": a.uid,
            "from": aid,
            "to": candidate,
            "name": a.name,
            "wbs_path": _wbs_path(project, a.wbs_uid),
        })

    return {"convention": fam, "share": share, "changes": changes,
            "left_alone": left_alone, "skipped": skipped, "scanned": len(acts)}


def validate(project: Project, changes: List[Dict[str, Any]]) -> List[str]:
    """
    Every reason this set of renames must not be applied.

    Checked as a whole rather than one at a time: a rename is only a
    collision if the id is still occupied AFTER the whole batch lands, and
    two renames that both want the same code are invisible row by row.
    """
    problems = []
    by_uid = {a.uid: a for a in project.activities}
    moving = set()
    for c in changes:
        uid = c.get("uid")
        if uid not in by_uid:
            problems.append(f"No activity with uid {uid} — reload and try again.")
        else:
            moving.add(uid)

    wanted = Counter()
    for c in changes:
        to = (c.get("to") or "").strip()
        if not to:
            problems.append("A rename has no new id.")
        wanted[to] += 1
    for code, n in wanted.items():
        if n > 1:
            problems.append(f"{n} activities would both become '{code}'.")

    staying = {(a.activity_id or "").strip()
               for a in project.activities if a.uid not in moving}
    for code in wanted:
        if code in staying:
            problems.append(f"'{code}' is already used by an activity that is "
                            f"not being renamed.")
    return problems


def apply_changes(project: Project, changes: List[Dict[str, Any]]) -> int:
    """Apply a validated set of renames. Returns how many ids moved."""
    by_uid = {a.uid: a for a in project.activities}
    n = 0
    for c in changes:
        a = by_uid.get(c.get("uid"))
        to = (c.get("to") or "").strip()
        if a is None or not to or (a.activity_id or "").strip() == to:
            continue
        a.activity_id = to
        n += 1
    project.build_lookups()
    return n
