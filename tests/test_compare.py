"""
test_compare.py — Cross-schedule compare (A) and copy-branch (B).

The behaviour that matters: copying a WBS branch carries the logic *inside* the
selection and drops (and reports) the links that leave it, so an unrelated
section of the target is never disturbed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation
from engine.compare import compare_projects, copy_wbs_branch


def _src():
    """A branch (ER 209 > Gear) with one internal chain and one link that
    leaves the branch to an outside activity."""
    p = Project(uid="S2", name="S2", id="S2")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="w0", name="Electrical", code="E"),
        WBSNode(uid="w1", name="ER 209", code="ER209", parent_uid="w0"),
        WBSNode(uid="g",  name="Gear", code="G", parent_uid="w1"),
        WBSNode(uid="wo", name="Other", code="OTH"),
    ]
    p.activities = [
        Activity(uid="a1", activity_id="A1000", name="Rough-in", wbs_uid="w1", calendar_uid="1", planned_duration=40.0),
        Activity(uid="a2", activity_id="A1010", name="Terminate", wbs_uid="w1", calendar_uid="1", planned_duration=24.0),
        Activity(uid="a3", activity_id="A1020", name="Set Gear", wbs_uid="g", calendar_uid="1", planned_duration=16.0),
        Activity(uid="ext", activity_id="B2000", name="External", wbs_uid="wo", calendar_uid="1", planned_duration=8.0),
    ]
    p.relations = [
        Relation(uid="r1", predecessor_uid="a1", successor_uid="a2"),   # internal
        Relation(uid="r2", predecessor_uid="a2", successor_uid="a3"),   # internal (into Gear)
        Relation(uid="rb", predecessor_uid="a2", successor_uid="ext"),  # BOUNDARY
    ]
    p.build_lookups()
    return p


def _empty_target():
    p = Project(uid="S1", name="S1", id="S1")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="root", name="Building", code="BLD")]
    p.build_lookups()
    return p


# ── Compare ──────────────────────────────────────────────────────────────────

def test_compare_classifies_added_removed_changed_unchanged():
    a = _src()
    b = _src()
    # add one, remove one, change one, leave one unchanged
    b.activities = [x for x in b.activities if x.activity_id != "B2000"]      # removed vs a
    b.activities.append(Activity(uid="new", activity_id="A1030", name="New",
                                 wbs_uid="w1", calendar_uid="1", planned_duration=8.0))
    b.get_activity(activity_id="A1010").planned_duration = 99.0              # changed
    b.build_lookups()
    diff = compare_projects(a, b)
    s = diff["summary"]
    assert s["added"] == 1 and s["removed"] == 1 and s["changed"] == 1


def test_compare_matches_by_id_then_name_wbs():
    a = _src()
    b = _src()
    # same activity, different id -> should still match by name + WBS
    b.get_activity(activity_id="A1000").activity_id = "Z9999"
    b.build_lookups()
    diff = compare_projects(a, b)
    # not counted as both an add and a remove
    assert diff["summary"]["added"] == 0 or diff["summary"]["removed"] == 0


# ── Copy branch ──────────────────────────────────────────────────────────────

def test_copy_branch_carries_internal_logic():
    src, tgt = _src(), _empty_target()
    ok, _, detail = copy_wbs_branch(src, "ER209", tgt, tgt_parent_code="BLD")
    assert ok
    assert detail["activities_copied"] == 3
    assert detail["wbs_copied"] == 2                     # ER 209 + Gear
    assert detail["relations_copied"] == 2               # the two internal links
    # the copied activities are chained
    ids = {a.activity_id for a in tgt.activities}
    assert len(ids) == 3


def test_copy_branch_drops_and_reports_boundary_links():
    src, tgt = _src(), _empty_target()
    _, _, detail = copy_wbs_branch(src, "ER209", tgt, tgt_parent_code="BLD")
    dropped = detail["boundary_links_dropped"]
    assert len(dropped) == 1
    d = dropped[0]
    assert d["inside_id"] == "A1010" and d["outside_id"] == "B2000"
    assert d["direction"] == "successor"
    # nothing links out of the copied branch in the target
    for r in tgt.relations:
        assert tgt.get_activity(uid=r.predecessor_uid) is not None
        assert tgt.get_activity(uid=r.successor_uid) is not None


def test_copy_branch_leaves_unrelated_target_content_untouched():
    src, tgt = _src(), _empty_target()
    tgt.wbs_nodes.append(WBSNode(uid="keep", name="Do Not Touch", code="KEEP", parent_uid="root"))
    tgt.activities.append(Activity(uid="ka", activity_id="K1", name="Keep me",
                                   wbs_uid="keep", calendar_uid="1", planned_duration=5.0))
    tgt.build_lookups()
    copy_wbs_branch(src, "ER209", tgt, tgt_parent_code="BLD")
    keep = tgt.get_activity(activity_id="K1")
    assert keep is not None and keep.wbs_uid == "keep"


def test_copy_branch_nests_under_target_parent():
    src, tgt = _src(), _empty_target()
    copy_wbs_branch(src, "ER209", tgt, tgt_parent_code="BLD")
    by = {w.uid: w for w in tgt.wbs_nodes}
    er = next(w for w in tgt.wbs_nodes if w.name == "ER 209")
    assert by[er.parent_uid].name == "Building"


# ── Feature C: seam-preserving replace ───────────────────────────────────────

def _target_with_section_and_seams():
    """Target has section ER209 (A1000->A1010) plus outside activities linked
    across the boundary: OUT_PRE -> A1000 (feeds in) and A1010 -> OUT_SUC
    (feeds out). Replacing ER209 should reconnect BOTH seams to the new
    same-ID activities."""
    p = Project(uid="T", name="T", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="root", name="Building", code="BLD"),
        WBSNode(uid="sec", name="ER 209", code="ER209", parent_uid="root"),
        WBSNode(uid="oth", name="Sitework", code="SITE", parent_uid="root"),
    ]
    p.activities = [
        Activity(uid="pre", activity_id="OUT_PRE", name="Permit", wbs_uid="oth", calendar_uid="1", planned_duration=8.0),
        Activity(uid="t1", activity_id="A1000", name="Old rough-in", wbs_uid="sec", calendar_uid="1", planned_duration=80.0),
        Activity(uid="t2", activity_id="A1010", name="Old terminate", wbs_uid="sec", calendar_uid="1", planned_duration=80.0),
        Activity(uid="suc", activity_id="OUT_SUC", name="Inspection", wbs_uid="oth", calendar_uid="1", planned_duration=8.0),
    ]
    p.relations = [
        Relation(uid="ri", predecessor_uid="t1", successor_uid="t2"),   # internal (will be replaced)
        Relation(uid="s_in", predecessor_uid="pre", successor_uid="t1"),  # seam IN
        Relation(uid="s_out", predecessor_uid="t2", successor_uid="suc"), # seam OUT
    ]
    p.build_lookups()
    return p


def _source_updated_section():
    """A newer ER209 keeping the same activity IDs but shorter durations, so a
    keep+id replace reconnects the seams by ID."""
    p = Project(uid="S", name="S", id="S")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="e", name="ER 209", code="ER209")]
    p.activities = [
        Activity(uid="n1", activity_id="A1000", name="New rough-in", wbs_uid="e", calendar_uid="1", planned_duration=16.0),
        Activity(uid="n2", activity_id="A1010", name="New terminate", wbs_uid="e", calendar_uid="1", planned_duration=16.0),
    ]
    p.relations = [Relation(uid="nr", predecessor_uid="n1", successor_uid="n2")]
    p.build_lookups()
    return p


def test_replace_swaps_activities_and_reconnects_both_seams():
    from engine.compare import replace_wbs_branch
    tgt = _target_with_section_and_seams()
    src = _source_updated_section()
    ok, msg, d = replace_wbs_branch(src, "ER209", tgt, "ER209", id_mode="keep", match="id")
    assert ok, msg
    # old section content is gone, new content is in
    assert tgt.get_activity(activity_id="A1000").name == "New rough-in"
    assert d["activities_removed"] == 2 and d["activities_copied"] == 2
    # BOTH seams reconnected (OUT_PRE->A1000, A1010->OUT_SUC)
    assert d["seams_reconnected"] == 2 and d["seams_dropped"] == []
    new_a1000 = tgt.get_activity(activity_id="A1000").uid
    new_a1010 = tgt.get_activity(activity_id="A1010").uid
    pre = tgt.get_activity(activity_id="OUT_PRE").uid
    suc = tgt.get_activity(activity_id="OUT_SUC").uid
    pairs = {(r.predecessor_uid, r.successor_uid) for r in tgt.relations}
    assert (pre, new_a1000) in pairs   # feeds-in seam restored
    assert (new_a1010, suc) in pairs   # feeds-out seam restored


def test_replace_drops_unmatchable_seam_as_open_end():
    from engine.compare import replace_wbs_branch
    tgt = _target_with_section_and_seams()
    src = _source_updated_section()
    # source no longer has A1010 -> the feeds-out seam has nothing to land on
    src.activities = [a for a in src.activities if a.activity_id != "A1010"]
    src.relations = []
    src.build_lookups()
    ok, msg, d = replace_wbs_branch(src, "ER209", tgt, "ER209", id_mode="keep", match="id")
    assert ok, msg
    assert d["seams_reconnected"] == 1       # only the feeds-in seam
    assert len(d["seams_dropped"]) == 1
    assert d["seams_dropped"][0]["inside_id"] == "A1010"


def test_replace_validates_before_deleting_target():
    from engine.compare import replace_wbs_branch
    tgt = _target_with_section_and_seams()
    src = _source_updated_section()
    before = len(tgt.activities)
    ok, msg, d = replace_wbs_branch(src, "NOPE", tgt, "ER209")
    assert not ok
    assert len(tgt.activities) == before      # target untouched on failure


def test_apply_activity_changes_pulls_named_fields():
    from engine.compare import apply_activity_changes
    a = _src()
    b = _src()
    b.get_activity(activity_id="A1000").name = "Renamed in B"
    b.get_activity(activity_id="A1000").planned_duration = 999.0
    # pull only the name from b into a
    ok, msg, d = apply_activity_changes(b, a, [{"activity_id": "A1000", "attrs": ["name"]}])
    assert ok
    assert a.get_activity(activity_id="A1000").name == "Renamed in B"
    assert a.get_activity(activity_id="A1000").planned_duration == 40.0   # untouched
    assert d["applied"] == 1


# ── Relationship payload carries the linked activity's NAME ──────────────────

def test_rel_maps_include_linked_activity_names():
    """The grid shows 'A1010 — Terminate' and offers a goto jump, so the
    predecessor/successor payload must carry the name, not just the id."""
    import server
    p = _src()
    preds, succs = server._build_rel_maps(p)
    # a1 -> a2 : the successor entry on a1 must name a2
    s = succs["a1"][0]
    assert s["activity_id"] == "A1010"
    assert s["name"] == "Terminate"
    # and the mirrored predecessor entry on a2 must name a1
    pr = preds["a2"][0]
    assert pr["activity_id"] == "A1000"
    assert pr["name"] == "Rough-in"
