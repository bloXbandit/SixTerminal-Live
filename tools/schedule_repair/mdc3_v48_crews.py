"""MDC3 v48: set the 'Number of Electricians' UDF from the MDC MV Commissioning
duration/labor workbook. Counts only. Hours, durations, logic and dates are
not touched.

The workbook carries one sheet per room type, so the same activity name can
hold a different count depending on the room it is in: branch pulls run 3 men
in an MV room and 8 in an ER room. Matching therefore tries the activity's own
room type first, and only then falls back to the other sheets for a name that
room type does not list, which is what covers the same scope sitting in
another WBS.

Nothing is guessed. An activity whose name the workbook does not carry keeps
the value it already has and is listed in the report for the user to set.
"""
import collections, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC3_logic_repaired_v47.xml"
OUT = f"{SCR}/MDC3_logic_repaired_v48.xml"
REF = (f"{SCR}/mvcx_src/"
       "8357af98-MDC_MV-Comissioning_-_Duration-Labor.xlsx")
UDF = "Number of Electricians"

# sheet name -> the room types in MDC3 it governs
SHEET_FOR = {"Generator Rooms": "Generator Rooms", "MV Rooms": "MV Rooms",
             "HV Rooms": "HV Rooms", "ER Rooms": "ER Rooms",
             "CUP": "CUP", "Data hall": "Data hall"}
# order used when a room type's own sheet does not list the name
FALLBACK = ["ER Rooms", "MV Rooms", "HV Rooms", "Generator Rooms",
            "Data hall", "CUP"]

STOP = {"the", "of", "to", "and", "a", "in", "s"}
FIX = {"penatration": "penetration", "penatrations": "penetration",
       "penetrations": "penetration", "roughin": "rough",
       "roughins": "rough", "ins": "in", "crahs": "crah",
       "crah's": "crah", "drywall": "dry wall"}


def norm(name):
    """Normalised token set for one activity name."""
    s = str(name)
    s = re.sub(r'\*+\s*WBO', ' ', s, flags=re.I)          # scope marker
    s = re.sub(r'\s*\(\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}\s*\)', ' ', s)
    s = re.sub(r'\s*-\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}\b.*$', ' ', s)
    s = re.sub(r'\s*-\s*Data\s*Cent\w*\s*[A-C]\b', ' ', s, flags=re.I)
    s = re.sub(r'\b(east|west)\b', ' ', s, flags=re.I)     # side of the room
    s = re.sub(r'\b\d+\b', ' ', s)                         # lineup / unit numbers
    s = s.lower().replace("/", " ").replace("-", " ")
    s = re.sub(r"[^a-z\s']", " ", s)
    out = []
    for t in s.split():
        t = t.strip("'")
        t = FIX.get(t, t)
        for part in t.split():
            if part and part not in STOP:
                out.append(part)
    return frozenset(out)


def load_ref():
    import openpyxl
    wb = openpyxl.load_workbook(REF, data_only=True)
    tables = {}
    for sh in wb.sheetnames:
        rows = {}
        for r in wb[sh].iter_rows(values_only=True):
            if not r or len(r) < 4:
                continue
            nm, dur, men = r[1], r[2], r[3]
            if nm is None or men is None:
                continue
            try:
                men = int(float(str(men).strip()))
            except (TypeError, ValueError):
                continue
            if men <= 0:
                continue
            k = norm(nm)
            if k:
                rows[k] = (men, str(nm).strip())
        tables[sh] = rows
        print(f"   {sh:<18}{len(rows)} activities with a count")
    return tables


def rtype(a, pathfn):
    pa = pathfn(a.wbs_uid)
    aid = str(a.activity_id)
    if ".LLE." in aid:
        return "LLE"
    if re.search(r'Generator Rooms', pa) or re.search(r'\.GEN\.', aid):
        return "Generator Rooms"
    if re.search(r'\bMV Rooms', pa) or re.search(r'\.MVR?\.', aid):
        return "MV Rooms"
    if re.search(r'\bHV Rooms', pa) or re.search(r'\.HVR?\.', aid):
        return "HV Rooms"
    if re.search(r'Electrical Rooms', pa) or re.search(r'\.ERR?\.', aid):
        return "ER Rooms"
    if re.search(r'\bCUP\b|Lineup', pa):
        return "CUP"
    if re.search(r'Data Hall|Gallery', pa):
        return "Data hall"
    return "other"


def best(key, table):
    """Exact token-set hit, else the closest containment match."""
    if key in table:
        return table[key], "exact"
    bestk = None
    bestscore = 0.0
    for k, v in table.items():
        if not k or not key:
            continue
        inter = len(k & key)
        if not inter:
            continue
        score = inter / len(k | key)
        # one name wholly inside the other counts as a strong match
        if k <= key or key <= k:
            score = max(score, 0.9 * inter / max(len(k), len(key)))
        if score > bestscore:
            bestscore, bestk = score, k
    if bestk is not None and bestscore >= 0.70:
        return table[bestk], f"close {bestscore:.2f}"
    return None, None


def main():
    print("reading the reference workbook:")
    tables = load_ref()
    p = load_xml(SRC)
    w = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in w:
            o.append(str(w[u].name))
            u = w[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    before = collections.Counter(
        str(a.udfs.get(UDF)) for a in p.activities if UDF in a.udfs)
    changed = []
    unmapped = collections.Counter()
    unmapped_ex = {}
    byhow = collections.Counter()

    for a in p.activities:
        if UDF not in a.udfs:
            continue
        rt = rtype(a, path)
        if rt in ("LLE", "other"):
            continue
        key = norm(a.name)
        if not key:
            continue
        hit = how = None
        src = None
        own = tables.get(SHEET_FOR.get(rt, ""), {})
        if own:
            hit, how = best(key, own)
            src = rt
        if hit is None:
            for sh in FALLBACK:
                if sh == SHEET_FOR.get(rt):
                    continue
                hit, how = best(key, tables.get(sh, {}))
                if hit is not None:
                    src = sh + " (borrowed)"
                    break
        if hit is None:
            nm = re.sub(r'\s*[\(-]\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}.*$',
                        '', str(a.name)).strip()
            unmapped[f"{rt} | {nm}"] += 1
            unmapped_ex.setdefault(f"{rt} | {nm}", str(a.activity_id))
            continue
        men, refname = hit
        old = str(a.udfs.get(UDF))
        if old != str(men):
            changed.append((str(a.activity_id), str(a.name), rt, old,
                            str(men), refname, src, how))
        a.udfs[UDF] = str(men)
        byhow[f"{src} / {how.split()[0]}"] += 1

    after = collections.Counter(
        str(a.udfs.get(UDF)) for a in p.activities if UDF in a.udfs)
    print(f"\nmatched and set: {sum(byhow.values())} activities "
          f"({len(changed)} of them changed value)")
    for k, v in byhow.most_common():
        print(f"      {v:>5}  {k}")
    print(f"\n'{UDF}' spread, before -> after:")
    keys = sorted(set(before) | set(after), key=lambda x: (len(x), x))
    for k in keys:
        print(f"      {k:<8}{before.get(k,0):>6} -> {after.get(k,0):>6}")
    print(f"\nnot in the workbook, left as they are: "
          f"{sum(unmapped.values())} activities, {len(unmapped)} distinct names")
    for k, v in unmapped.most_common(40):
        print(f"      {v:>4}  {k[:70]}   e.g. {unmapped_ex[k]}")

    # nothing but the UDF may change
    import hashlib
    sig = hashlib.md5(repr([
        (str(a.activity_id), a.planned_labor_units, a.planned_duration,
         str(a.planned_start), str(a.planned_finish))
        for a in p.activities]).encode()).hexdigest()
    q = load_xml(SRC)
    sig0 = hashlib.md5(repr([
        (str(a.activity_id), a.planned_labor_units, a.planned_duration,
         str(a.planned_start), str(a.planned_finish))
        for a in q.activities]).encode()).hexdigest()
    assert sig == sig0, "hours, durations or dates changed, they must not"
    assert len(p.relations) == len(q.relations), "relation count changed"
    print("\n   checks passed: hours, durations, dates and logic untouched")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")
    return changed, unmapped, unmapped_ex


if __name__ == "__main__":
    main()
