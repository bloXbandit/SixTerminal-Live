"""114 activities name one room in their ID and a different one in their name.

In Data Center C the ER rooms are mirrored exactly, 16 activities each way:

    id 102 <-> name 107      id 106 <-> name 103
    id 103 <-> name 106      id 107 <-> name 102
    id 104 <-> name 105      id 108 <-> name 101
    id 105 <-> name 104

Every pair sums to 209, so one of the two numberings was reversed end for
end. Anything that classifies by activity id therefore reports the wrong
room for those, which is how a crew-flow table came to be labelled ER 106,
107 and 108 when the activities name ER 103, 102 and 101.
"""
import collections, re, sys
sys.path.insert(0, "/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml                      # noqa: E402

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")


def from_id(k):
    m = re.search(r'\.(ERR?|MVR?|HVR?)\.?(\d{3})\.', str(k).upper())
    return m.group(2) if m else None


def from_name(n):
    m = re.search(r'\b(ERR?|MVR?|HVR?)[\s.]?(\d{3})', str(n).upper())
    return m.group(2) if m else None


p = load_xml(f"{SCR}/MDC3_logic_repaired_v43.xml")
bad = [(str(a.activity_id), str(a.name), from_id(a.activity_id),
        from_name(a.name)) for a in p.activities
       if from_id(a.activity_id) and from_name(a.name)
       and from_id(a.activity_id) != from_name(a.name)]
print(f"activities whose ID room differs from their NAME room: {len(bad)}")
c = collections.Counter(f"{i} -> {n}" for _, _, i, n in bad)
for k, v in c.most_common():
    print(f"   {v:>4}  id {k}")
print("\nwhich is right is a field question. The NAME is more likely correct,")
print("since it is what a foreman reads, but nothing in the file settles it.")
