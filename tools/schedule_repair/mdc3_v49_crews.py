"""MDC3 v49: finish the electrician counts. Counts only, no hours.

Three passes, in descending order of how much is inferred:

  1. The reference workbook, matched per room type as in v48, now with the
     location suffixes stripped ("- L1 CUP", "- Mech Line Up 7",
     "- Data Center A"), which recovers about 150 CUP activities that were
     being blocked by the suffix rather than by any real difference.
  2. Named aliases: an MDC3 activity that is plainly the reference activity
     under another name takes the reference's count. "Install In-Wall MEP"
     is the reference's "MEP In Wall".
  3. Reasoned rules for what the workbook does not cover at all. Every rule
     carries the basis it was set from, which the report prints.

The field is JER electricians, so work carried by another trade is set to 0
rather than left at the inherited 4. A drywall framing activity does not have
four JER electricians on it. That is the single biggest correction here.

Crew size is scaled to the room: a gallery or data hall is a large open area
and the workbook puts 9 to 15 men on its overhead, while an ER, MV, HV or
generator room is a small room and the workbook puts 2 to 5 on the same kind
of work. Rules below follow that split.
"""
import collections, re, sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
SRC = f"{SCR}/MDC3_logic_repaired_v47.xml"
OUT = f"{SCR}/MDC3_logic_repaired_v49.xml"
REF = (f"{SCR}/mvcx_src/"
       "8357af98-MDC_MV-Comissioning_-_Duration-Labor.xlsx")
UDF = "Number of Electricians"

SHEET_FOR = {"Generator Rooms": "Generator Rooms", "MV Rooms": "MV Rooms",
             "HV Rooms": "HV Rooms", "ER Rooms": "ER Rooms",
             "CUP": "CUP", "Data hall": "Data hall"}
FALLBACK = ["ER Rooms", "MV Rooms", "HV Rooms", "Generator Rooms",
            "Data hall", "CUP"]

STOP = {"the", "of", "to", "and", "a", "in", "s"}
FIX = {"penatration": "penetration", "penatrations": "penetration",
       "penetrations": "penetration", "roughin": "rough", "roughins": "rough",
       "ins": "in", "crahs": "crah", "throu": "thru"}

# --- pass 2: MDC3 name -> the reference activity it actually is ----------
ALIAS = {
    "install in-wall mep":            ("Data hall", "MEP In Wall"),
    "install electrical overhead":    ("Data hall", "Remaining Overhead Rough In / Equipment Tie In"),
    "install lighting":               ("Data hall", "Lighting / MEP Trim Out"),
    "install fire alarm devices":     ("Data hall", "Ceiling Mounted Device Install"),
    "install upper vesda":            ("Data hall", "Ceiling Mounted Device Install"),
    "upper vesda install":            ("Data hall", "Ceiling Mounted Device Install"),
    "install lower vesda":            ("Data hall", "Ceiling Mounted Device Install"),
    "install ceiling grid devices":   ("Data hall", "Ceiling Mounted Device Install"),
    "set cdus":                       ("Data hall", "CDUs Set / Install*"),
    "electrical rough-in":            ("Data hall", "Remaining Overhead Rough In / Equipment Tie In"),
}

# --- pass 3: reasoned rules. (pattern, room scope, count, basis) ---------
# room scope: "big" = data hall / gallery / CUP, "small" = ER/MV/HV/Gen, "" = any
RULES = [
    # ---- not JER electrical work: another trade carries it -------------
    (r'drywall|dry wall|framing|frame walls|hang.?tape|tape finish|hang / tape',
     "", 0, "drywall trade, no JER electricians"),
    (r'\bpaint|prime|caulking and paint|caulking & paint|waterproof',
     "", 0, "painters, no JER electricians"),
    (r'floor finish|final floor|floor epoxy|epoxy floor|ceiling tile|install ceiling grid$|ceiling grid install',
     "", 0, "finishes trade, no JER electricians"),
    (r'mechanical piping|pipe mains|branch piping|insulat|hydrostatic|'
     r'steel pipe support|hanging rods|compressed air|mechanical final connection|'
     r'overhead mechanical|rough in mechanical|mechanical pipe',
     "", 0, "mechanical trade, no JER electricians"),
    (r'sprinkler|fire protection|fire caulking',
     "", 0, "fire protection trade, no JER electricians"),
    (r'ductwork|hvac trim',
     "", 0, "sheet metal trade, no JER electricians"),
    (r'concrete curb|equipment pads|f/r/p|imp wall|hang scr',
     "", 0, "concrete and wall panel trades, no JER electricians"),
    (r'overhead caulking',
     "", 0, "caulking trade, no JER electricians"),
    (r'set fan walls|tie-in fan walls',
     "", 0, "fan walls are set and piped by others, workbook leaves the count blank"),
    (r'chiller lineup .*set equipment|chiller lineup .*tie-in equipment',
     "", 0, "mechanical sets and ties the chiller plant, JER pulls and terminates it"),

    # ---- inspections and hold points ----------------------------------
    (r'rooms ready for inspection|ready to energize',
     "", 1, "hold point, one foreman walks it"),
    (r'inspection|pre-rock|close-in',
     "", 2, "two men walk the room, against the workbook's 3 for a full test and inspection"),

    # ---- feeder pulls and terminations --------------------------------
    (r'^pull from edp|^pull from skid|pull telecom',
     "big", 8, "gallery feeder pull, matches the workbook's 8 on ER Electrical Pulls and Final Connections"),
    (r'^term from edp|^term from skid',
     "big", 4, "matches the workbook's 4 on Electrical Terminations and Controls"),
    (r'^pull msb|^pull ',
     "small", 5, "single feeder pull in a small room, matches the workbook's 5 on MSG to MSG pull"),
    (r'^term msb|^term crah|^term ats|^term rmu|^term |controls - skid|'
     r'install/pull/term',
     "small", 4, "matches the workbook's 4 on Electrical Terminations and Controls"),
    (r'tie-in adc.*busway|busway',
     "", 5, "matches the workbook's 5 on ER Bus Duct Install"),

    # ---- equipment sets ------------------------------------------------
    (r'^set edps|^set control panel',
     "", 3, "matches the workbook's 3 on Set Electrical Skids"),
    (r'install station batteries',
     "", 4, "battery string is JER electrical, matches the workbook's 4 on Set Misc. Electrical Equip"),
    (r'set crah|set gen \(|^set gen\b',
     "", 2, "rigging set, JER makes the connection only, matches the workbook's 2 on Set Generator"),
    (r'install vesda rack|fa device install',
     "small", 3, "device work in a small room, matches the workbook's 3 on Device Trim Out"),
    (r'install dfh',
     "", 4, "equipment install, matches the workbook's 4 on Set Misc. Electrical Equip"),

    # ---- overhead rough in --------------------------------------------
    (r'overhead electrical|electrical/fa|overhead rough in electrical',
     "small", 3, "matches the workbook's 3 on Overhead Rough In / FA"),
    (r'install hangers and double strut|mep overhead hangers|^install hangers',
     "", 3, "matches the workbook's 3 on Install Hangers"),
    (r'drop ceiling grid rods',
     "", 3, "overhead rod hanging, matches the workbook's 3 on Install Hangers"),
    (r'electrical pull and connection to engine',
     "", 5, "matches the workbook's 5 on Pull LBB to Generator"),
    (r'testing and terminate',
     "", 3, "matches the workbook's 3 on Terminate Generator/LBB"),
]


def norm(name):
    s = str(name)
    s = re.sub(r'\*+\s*WBO', ' ', s, flags=re.I)
    s = re.sub(r'\s*-\s*Mech(anical)?\s*Line\s*Up\s*\d+.*$', ' ', s, flags=re.I)
    s = re.sub(r'\s*-\s*L\d+\s*CUP\b.*$', ' ', s, flags=re.I)
    s = re.sub(r'\s*-\s*Data\s*Cent\w*\s*[A-C]\b.*$', ' ', s, flags=re.I)
    s = re.sub(r'\s*\(\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}\s*\)', ' ', s)
    s = re.sub(r'\s*-\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}\b.*$', ' ', s)
    s = re.sub(r'\b(east|west)\b', ' ', s, flags=re.I)
    s = re.sub(r'\b\d+\b', ' ', s)
    s = s.lower().replace("/", " ").replace("-", " ")
    s = re.sub(r"[^a-z\s']", " ", s)
    out = []
    for t in s.split():
        t = FIX.get(t.strip("'"), t.strip("'"))
        if t and t not in STOP:
            out.append(t)
    return frozenset(out)


def clean(name):
    s = re.sub(r'\s*[\(-]\s*(Gen|ER|ERR|MV|HV|HVR|DH|GL)\s*\d{3}.*$', '', str(name))
    s = re.sub(r'\s*-\s*Mech(anical)?\s*Line\s*Up\s*\d+.*$', '', s, flags=re.I)
    s = re.sub(r'\s*-\s*L\d+\s*CUP\b.*$', '', s, flags=re.I)
    s = re.sub(r'\s*-\s*Data\s*Cent\w*\s*[A-C]\b.*$', '', s, flags=re.I)
    return s.strip()


def load_ref():
    import openpyxl
    wb = openpyxl.load_workbook(REF, data_only=True)
    tables, flat = {}, {}
    for sh in wb.sheetnames:
        rows = {}
        for r in wb[sh].iter_rows(values_only=True):
            if not r or len(r) < 4:
                continue
            nm, men = r[1], r[3]
            if nm is None or men is None:
                continue
            t = str(nm).strip()
            if t.lower() in ("activity name", "activity id") or t.startswith(("NOTE:", "*=", "PH2 ")):
                continue
            try:
                men = int(float(str(men).strip()))
            except (TypeError, ValueError):
                continue
            if men <= 0:
                continue
            k = norm(t)
            if k:
                rows[k] = (men, t)
                flat[(sh, t)] = men
        tables[sh] = rows
    return tables, flat


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


SCOPE = {"Data hall": "big", "CUP": "big", "ER Rooms": "small",
         "MV Rooms": "small", "HV Rooms": "small", "Generator Rooms": "small"}


def best(key, table):
    if key in table:
        return table[key], "exact"
    bk, bs = None, 0.0
    for k, v in table.items():
        if not k or not key:
            continue
        inter = len(k & key)
        if not inter:
            continue
        score = inter / len(k | key)
        if k <= key or key <= k:
            score = max(score, 0.9 * inter / max(len(k), len(key)))
        if score > bs:
            bs, bk = score, k
    return (table[bk], f"close {bs:.2f}") if bk is not None and bs >= 0.70 else (None, None)


def main():
    tables, flat = load_ref()
    p = load_xml(SRC)
    w = {n.uid: n for n in p.wbs_nodes}

    def path(u):
        o = []
        while u in w:
            o.append(str(w[u].name))
            u = w[u].parent_uid
        return " / ".join(list(reversed(o))[1:])

    before = collections.Counter(str(a.udfs.get(UDF)) for a in p.activities if UDF in a.udfs)
    decided, leftover = [], collections.Counter()
    leftover_ex = {}
    bysrc = collections.Counter()

    for a in p.activities:
        if UDF not in a.udfs:
            continue
        rt = rtype(a, path)
        if rt in ("LLE", "other"):
            continue
        nm = clean(a.name)
        key = norm(a.name)
        scope = SCOPE.get(rt, "")
        men = src = basis = None

        # pass 1: the workbook, own room type then the others
        own = tables.get(SHEET_FOR.get(rt, ""), {})
        hit, how = best(key, own) if own else (None, None)
        if hit:
            men, src, basis = hit[0], f"workbook {rt}", f"reference '{hit[1]}' ({how})"
        if men is None:
            for sh in FALLBACK:
                if sh == SHEET_FOR.get(rt):
                    continue
                hit, how = best(key, tables.get(sh, {}))
                if hit:
                    men, src = hit[0], f"workbook {sh} (borrowed)"
                    basis = f"reference '{hit[1]}' ({how})"
                    break
        # pass 2: named alias
        if men is None:
            low = nm.lower()
            for pat, (sh, refname) in ALIAS.items():
                if low.startswith(pat):
                    v = flat.get((sh, refname))
                    if v:
                        men, src = v, "alias"
                        basis = f"same work as reference '{refname}' on the {sh} sheet"
                    break
        # pass 3: reasoned rule
        if men is None:
            for pat, sc, val, why in RULES:
                if sc and sc != scope:
                    continue
                if re.search(pat, nm, re.I):
                    men, src, basis = val, "rule", why
                    break
        if men is None:
            leftover[f"{rt} | {nm}"] += 1
            leftover_ex.setdefault(f"{rt} | {nm}", str(a.activity_id))
            continue
        old = str(a.udfs.get(UDF))
        a.udfs[UDF] = str(men)
        bysrc[src] += 1
        if old != str(men):
            decided.append((str(a.activity_id), nm, rt, old, str(men), src, basis))

    after = collections.Counter(str(a.udfs.get(UDF)) for a in p.activities if UDF in a.udfs)
    print(f"set a count on {sum(bysrc.values())} activities "
          f"({len(decided)} changed value)")
    for k, v in bysrc.most_common():
        print(f"      {v:>5}  {k}")
    print(f"\n'{UDF}' spread, before -> after:")
    for k in sorted(set(before) | set(after), key=lambda x: (len(x), x)):
        print(f"      {k:<8}{before.get(k,0):>6} -> {after.get(k,0):>6}")
    print(f"\nstill undecided: {sum(leftover.values())} activities, "
          f"{len(leftover)} names")
    for k, v in leftover.most_common(30):
        print(f"      {v:>4}  {k[:70]}   e.g. {leftover_ex[k]}")

    import hashlib
    def sig(pr):
        return hashlib.md5(repr([
            (str(x.activity_id), x.planned_labor_units, x.planned_duration,
             str(x.planned_start), str(x.planned_finish)) for x in pr.activities]).encode()).hexdigest()
    q = load_xml(SRC)
    assert sig(p) == sig(q), "hours, durations or dates changed, they must not"
    assert len(p.relations) == len(q.relations), "relation count changed"
    print("\n   checks passed: hours, durations, dates and logic untouched")
    write_p6_xml(p, OUT)
    print(f"   written {OUT}")
    return decided, leftover, leftover_ex


if __name__ == "__main__":
    main()
