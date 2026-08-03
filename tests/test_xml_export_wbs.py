"""
test_xml_export_wbs.py — The exported XML must describe a WBS tree P6 can
actually attach to the project.

Project/WBSObjectId names the project's root WBS, and P6 exports that node as
a <WBS> element like any other. The writer referenced it but never defined it,
so every folder was a child of an ObjectId appearing nowhere in the file: the
P6 XML importer hit an unresolvable parent and refused the project outright,
while a more forgiving reader created the folders and dropped every activity
beneath them.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.xml_writer import _PROJECT_WBS_OID


def _client():
    return server.app.test_client()


def _load(c, text, name="Job"):
    r = c.post("/api/import/paste", json={"text": text, "project_name": name})
    c.post("/api/import/commit", json={"contract": r.get_json()["contract"],
                                       "mode": "replace", "project_name": name})


def _export(c):
    return server._project_to_xml_bytes(server._get_session()["project"]).decode()


def _field(block, tag):
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), block)
    return m.group(1) if m else None


def _wbs_blocks(xml):
    """ObjectId -> (name, parent, sequence)."""
    out = {}
    for m in re.finditer(r"<WBS>(.*?)</WBS>", xml, re.S):
        b = m.group(1)
        out[_field(b, "ObjectId")] = (_field(b, "Name"),
                                      _field(b, "ParentObjectId"),
                                      _field(b, "SequenceNumber"))
    return out


def _activity_targets(xml):
    return re.findall(r"<Activity>.*?<Id>([^<]*)</Id>.*?"
                      r"<WBSObjectId>([^<]*)</WBSObjectId>", xml, re.S)


def _nested_fixture(c):
    _load(c, "Sitework\nA1000\tMobilize\t10\t05-Jan-26\t16-Jan-26")
    c.post("/api/direct", json={"commands": [{"action": "add_wbs", "name": "Phase 2"}],
                                "label": "a"})
    c.post("/api/direct", json={"commands": [
        {"action": "add_wbs", "name": "Electrical", "parent_name": "Phase 2"}], "label": "a"})
    c.post("/api/direct", json={"commands": [
        {"action": "add_activity", "wbs_name": "Electrical",
         "name": "Pull wire", "duration_days": 3}], "label": "a"})


def test_the_project_root_wbs_is_defined_not_just_referenced():
    """Project/WBSObjectId must resolve to a real <WBS> element in the file.
    A dangling root is what made P6 reject the import outright."""
    c = _client()
    _nested_fixture(c)
    xml = _export(c)
    proj = re.search(r"  <Project>(.*?)\n  </Project>", xml, re.S).group(1)
    declared = re.search(r"<WBSObjectId>(\d+)</WBSObjectId>", proj).group(1)
    blocks = _wbs_blocks(xml)
    assert declared in blocks, (
        f"Project declares root WBS {declared} but no <WBS> element defines it")
    assert declared == _PROJECT_WBS_OID
    # the root itself is the only node without a parent
    root_block = re.search(r"<WBS>((?:(?!</WBS>).)*?<ObjectId>%s</ObjectId>.*?)</WBS>"
                           % declared, xml, re.S)
    assert root_block and "ParentObjectId xsi:nil" in root_block.group(1), \
        "the project root WBS must have a nil parent"


def test_top_level_folders_attach_to_the_project_root():
    c = _client()
    _nested_fixture(c)
    blocks = _wbs_blocks(_export(c))
    assert blocks, "export produced no WBS blocks"
    for oid, (name, parent, _seq) in blocks.items():
        if oid == _PROJECT_WBS_OID:
            assert parent is None, "the project root WBS is the only node with no parent"
            continue
        assert parent, f"{name} has no ParentObjectId"
        assert parent in blocks, \
            f"{name} points at {parent}, which is not a WBS element in this file"


def test_no_wbs_is_left_unattached():
    """Every folder must chain up to the project root — an unreachable branch
    is what P6 silently drops on import."""
    c = _client()
    _nested_fixture(c)
    blocks = _wbs_blocks(_export(c))
    for oid in blocks:
        seen, cur, hops = set(), oid, 0
        while cur is not None and cur != _PROJECT_WBS_OID and hops < 50:
            assert cur in blocks, f"{blocks[oid][0]} hangs off undefined WBS {cur}"
            assert cur not in seen, "cycle in the exported WBS tree"
            seen.add(cur)
            cur = blocks[cur][1]
            hops += 1
        assert cur == _PROJECT_WBS_OID, \
            f"{blocks[oid][0]} never reaches the project root (stopped at {cur})"


def test_every_activity_points_at_a_folder_that_exists():
    c = _client()
    _nested_fixture(c)
    xml = _export(c)
    blocks = _wbs_blocks(xml)
    targets = _activity_targets(xml)
    assert targets, "export produced no activities"
    for aid, wbs_oid in targets:
        assert wbs_oid in blocks or wbs_oid == _PROJECT_WBS_OID, \
            f"activity {aid} references WBS {wbs_oid}, which is not in the file"


def test_all_activities_survive_the_export():
    c = _client()
    _nested_fixture(c)
    proj = server._get_session()["project"]
    assert len(_activity_targets(_export(c))) == len(proj.activities)


def test_export_order_matches_the_order_shown_in_the_app():
    """Imported folders all carry sequence_num 0, so the export has to spell
    out the order the grid actually shows — including after the reorder
    arrows are used — or P6 picks its own."""
    c = _client()
    _load(c, "A1\tSeed\t1\t05-Jan-26\t05-Jan-26", name="J")
    for n in ("Alpha", "Bravo", "Charlie"):
        c.post("/api/direct", json={"commands": [{"action": "add_wbs", "name": n}],
                                    "label": "a"})

    def app_order():
        return [w["name"] for w in c.get("/api/schedule").get_json()["wbs_sections"]]

    def xml_root_order():
        blocks = _wbs_blocks(_export(c))
        roots = [(int(seq), name) for name, parent, seq in blocks.values()
                 if parent == _PROJECT_WBS_OID]
        return [n for _s, n in sorted(roots)]

    assert xml_root_order() == app_order()

    uid = next(w["uid"] for w in c.get("/api/schedule").get_json()["wbs_sections"]
               if w["name"] == "Charlie")
    c.post("/api/direct", json={"commands": [
        {"action": "reorder_wbs", "wbs_uid": uid, "direction": "up"}], "label": "m"})
    assert xml_root_order() == app_order()
    assert app_order().index("Charlie") < app_order().index("Bravo")


def test_siblings_get_distinct_sequence_numbers():
    """All-zero sequence numbers are exactly what let P6 reorder folders."""
    c = _client()
    _load(c, "A1\tSeed\t1\t05-Jan-26\t05-Jan-26", name="J")
    for n in ("Alpha", "Bravo", "Charlie"):
        c.post("/api/direct", json={"commands": [{"action": "add_wbs", "name": n}],
                                    "label": "a"})
    blocks = _wbs_blocks(_export(c))
    by_parent = {}
    for name, parent, seq in blocks.values():
        by_parent.setdefault(parent, []).append(int(seq))
    for parent, seqs in by_parent.items():
        assert len(seqs) == len(set(seqs)), f"duplicate SequenceNumbers under {parent}"
