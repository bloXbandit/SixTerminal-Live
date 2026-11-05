"""MDC-3 v29: fit every UDF value inside P6's 255-character limit.

P6's XML importer rejects a UDF Text value over 255 characters:

  SEVERE: com.primavera.integration.client.xml.xmlimporter.XMLImporterException:
  Field "Text" in UDFValue may not contain more than 255 characters.

787 of the COMMENTS values written into v28 were longer than that, the worst
427, because activities that picked up more than one change had their notes
appended one after another. Nothing else in the file is affected: the other
UDF, Number of Electricians, tops out at 2 characters.

Trimming mid-word would leave notes ending in half a sentence, so each
over-long value keeps as many whole sentences as fit in 250 characters and
gets a trailing "..." to show something was dropped. A single sentence that is
itself too long is cut at the last space instead. The cap is 248 rather than
255 to leave room for that marker, and the marker is three ASCII dots rather
than a single ellipsis character so nothing in the file depends on P6 reading
the encoding the way this writes it.

Nothing but the comment text changes: same activities, same logic, same dates.
"""
import collections
import re
import sys

sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
CAP = 248   # 248 + the 3-character "..." stays clear of P6's 255
p = load_xml(f"{SCR}/MDC3_logic_repaired_v28.xml")


# Notes that must survive the trim. Taking sentences from the front alone
# dropped every one of these, because an activity that picked up several
# changes had the important one appended last: 194 Red Tags lost the marker
# saying the sign-off is JER's own, and 3 of the 5 activities where our field
# update contradicts Holder lost the warning that they disagree.
KEEP = ("DISAGREEMENT", "Confirm with Holder", "THIS IS JER",
        "DATES INFERRED", "no roof work", "NOT duplicates")


def fit(s, cap=CAP):
    """keep whole sentences, the ones that carry a flag first"""
    s = re.sub(r"\s+", " ", str(s)).strip()
    if len(s) <= cap:
        return s
    parts = [x for x in re.split(r"(?<=[.!?]) +", s) if x]
    pri = [i for i, x in enumerate(parts) if any(m in x for m in KEEP)]
    take, used = set(), 0
    for i in pri + [i for i in range(len(parts)) if i not in pri]:
        if used + len(parts[i]) + 1 > cap - 1:
            continue
        take.add(i)
        used += len(parts[i]) + 1
    out = " ".join(parts[i] for i in sorted(take))
    if not out:
        out = parts[pri[0]] if pri else s
        out = out[:cap - 1]
        if " " in out:
            out = out[:out.rfind(" ")]
    return out.rstrip() + ("..." if len(take) < len(parts) else "")


before = collections.Counter()
after = collections.Counter()
fixed = 0
longest_before = longest_after = 0
sample = []
for a in p.activities:
    for k, v in list((a.udfs or {}).items()):
        s = str(v)
        longest_before = max(longest_before, len(s))
        before[k] = max(before[k], len(s))
        if len(s) > CAP:
            n = fit(s)
            if len(sample) < 3:
                sample.append((a.activity_id, k, len(s), s, n))
            a.udfs[k] = n
            fixed += 1
            s = n
        longest_after = max(longest_after, len(s))
        after[k] = max(after[k], len(s))

print(f"trimmed {fixed} UDF values")
print(f"{'UDF':<24}{'longest before':>16}{'longest after':>15}")
for k in before:
    print(f"{k:<24}{before[k]:>16}{after[k]:>15}")
print(f"\nany value still over 255: "
      f"{sum(1 for a in p.activities for v in (a.udfs or {}).values() if len(str(v)) > 255)}")
print(f"any value still over {CAP}: "
      f"{sum(1 for a in p.activities for v in (a.udfs or {}).values() if len(str(v)) > CAP)}")
for aid, k, n, old, new in sample:
    print(f"\n--- {aid} [{k}] {n} -> {len(new)} chars")
    print(f"  was: {old[:150]}...")
    print(f"  now: {new}")

# nothing else should have moved
print(f"\nactivities {len(p.activities)}   relationships {len(p.relations)}   "
      f"WBS {len(p.wbs_nodes)}")
print("status: " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
    a.status for a in p.activities).most_common()))
out = f"{SCR}/MDC3_logic_repaired_v29.xml"
write_p6_xml(p, out, include_resources=False, include_udfs=True)
print(f"\nwrote {out}")
