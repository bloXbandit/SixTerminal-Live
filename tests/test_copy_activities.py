"""
test_copy_activities.py — In-schedule mass copy of selected rows.

The point of the action is that logic travels: relationships whose BOTH ends are
inside the selection are recreated between the copies, while a link that leaves
the selection is not (a copy must not silently inherit a predecessor it doesn't
own). Row data — duration, type, constraints — comes along too.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation
from engine.edit_engine import apply_command


def _proj():
    p = Project(uid="1", name="T", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="a", name="ER 209", code="ER209"),
                   WBSNode(uid="b", name="ER 210", code="ER210")]
    p.activities = [
        Activity(uid="1", activity_id="A1000", name="Rough-in", wbs_uid="a",
                 calendar_uid="1", planned_duration=40.0,
                 constraint_type="Start On", constraint_date="2026-03-02"),
        Activity(uid="2", activity_id="A1010", name="Pull wire", wbs_uid="a",
                 calendar_uid="1", planned_duration=24.0),
        Activity(uid="3", activity_id="A1020", name="Terminate", wbs_uid="a",
                 calendar_uid="1", planned_duration=16.0,
                 activity_type="Finish Milestone"),
        Activity(uid="9", activity_id="A9000", name="Outside", wbs_uid="b",
                 calendar_uid="1", planned_duration=8.0),
    ]
    p.relations = [
        Relation(uid="r1", predecessor_uid="1", successor_uid="2"),
        Relation(uid="r2", predecessor_uid="2", successor_uid="3",
                 type="Start to Start", lag=16.0),
        Relation(uid="rb", predecessor_uid="3", successor_uid="9"),   # BOUNDARY
    ]
    p.build_lookups()
    return p


def _copy(p, **kw):
    cmd = {"action": "copy_activities",
           "activity_ids": ["A1000", "A1010", "A1020"]}
    cmd.update(kw)
    return apply_command(p, cmd)


def _new_acts(p):
    return [a for a in p.activities if a.wbs_uid == "b" and a.activity_id != "A9000"]


def test_rows_land_in_the_target_folder():
    p = _proj()
    ok, msg = _copy(p, wbs_name="ER 210")
    assert ok, msg
    new = _new_acts(p)
    assert len(new) == 3
    assert {a.name for a in new} == {"Rough-in", "Pull wire", "Terminate"}


def test_internal_logic_travels_with_type_and_lag():
    p = _proj()
    _copy(p, wbs_name="ER 210")
    new = {a.name: a for a in _new_acts(p)}
    by_uid = {a.uid: a for a in p.activities}
    pairs = {(by_uid[r.predecessor_uid].name, by_uid[r.successor_uid].name):
             (r.type, r.lag)
             for r in p.relations
             if r.predecessor_uid in {a.uid for a in new.values()}}
    assert pairs[("Rough-in", "Pull wire")] == ("Finish to Start", 0.0)
    # the SS lag must survive, not silently collapse to a default FS
    assert pairs[("Pull wire", "Terminate")] == ("Start to Start", 16.0)


def test_a_link_leaving_the_selection_is_not_carried():
    p = _proj()
    before_outside = sum(1 for r in p.relations if r.successor_uid == "9")
    ok, msg = _copy(p, wbs_name="ER 210")
    after_outside = sum(1 for r in p.relations if r.successor_uid == "9")
    assert after_outside == before_outside      # the copy did NOT link to A9000
    assert "outside the selection" in msg


def test_row_data_is_preserved():
    p = _proj()
    _copy(p, wbs_name="ER 210")
    new = {a.name: a for a in _new_acts(p)}
    assert new["Rough-in"].planned_duration == 40.0
    assert new["Rough-in"].constraint_type == "Start On"
    assert new["Rough-in"].constraint_date == "2026-03-02"
    assert new["Terminate"].activity_type == "Finish Milestone"
    # a copy has not been worked yet
    assert all(a.status == "Not Started" for a in new.values())


def test_copies_get_fresh_unique_ids():
    p = _proj()
    _copy(p, wbs_name="ER 210")
    ids = [a.activity_id for a in p.activities]
    assert len(ids) == len(set(ids))


def test_count_stamps_out_repeats():
    p = _proj()
    _copy(p, wbs_name="ER 210", count=3)
    assert len(_new_acts(p)) == 9
    # each stamp keeps its own internal chain: 2 links x 3 copies
    new_uids = {a.uid for a in _new_acts(p)}
    carried = [r for r in p.relations
               if r.predecessor_uid in new_uids and r.successor_uid in new_uids]
    assert len(carried) == 6


def test_missing_activity_is_reported_and_nothing_is_half_copied():
    """Every id is resolved before anything is created, so a typo in one row
    can't leave the schedule holding a partial copy."""
    p = _proj()
    before = len(p.activities)
    ok, msg = apply_command(p, {"action": "copy_activities",
                                "activity_ids": ["A1000", "NOPE"],
                                "wbs_name": "ER 210"})
    assert ok is False
    assert "NOPE" in msg
    assert len(p.activities) == before


def test_no_target_folder_keeps_each_row_where_it_was():
    p = _proj()
    ok, _ = _copy(p)
    assert ok
    in_a = [a for a in p.activities if a.wbs_uid == "a"]
    assert len(in_a) == 6      # 3 originals + 3 copies, all still in ER 209
