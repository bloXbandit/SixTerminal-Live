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
