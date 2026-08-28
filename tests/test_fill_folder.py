"""
test_fill_folder.py — build a thin folder up to match one that is already right.

Several areas are meant to run the same way. One gets fully built out and
wired; the rest are started and left short. Copying the template wholesale
duplicates the rows that already exist and orphans the logic hanging off
them — which is exactly how this schedule ended up with pairs of identical
activities. Adding the missing rows by hand is hours per area.

The contract defended here is mostly about what must NOT happen. Logic the
user has already set is the thing they are most afraid of losing, so: no
activity is ever deleted, no relationship is ever deleted or repointed, and
no row that already existed is edited. Only genuinely missing work is added.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.edit_engine import EditError, apply_command
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p():
    p = Project(uid="p", name="J", id="J", data_date="2026-01-01",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="A", name="MV 101", code="A"),
                   WBSNode(uid="B", name="MV 105", code="B")]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, name, folder, start, dur=5):
    a = Activity(uid=uid, activity_id=uid, name=name, wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=dur * 8,
                 remaining_duration=dur * 8, planned_start=start,
                 planned_finish=start)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start", lag=0.0))
    p.build_lookups()


def _template_and_thin_target():
    """MV 101 fully built and chained; MV 105 has 2 of the 5, already tied."""
    p = _p()
    _act(p, "T1", "Set CRAHs MV 101", "A", "2026-02-02")
    _act(p, "T2", "Overhead Rough In MV 101", "A", "2026-02-09")
    _act(p, "T3", "Wall Rough Ins MV 101", "A", "2026-02-16")
    _act(p, "T4", "Electrical Pulls MV 101", "A", "2026-02-23")
    _act(p, "T5", "Testing MV 101", "A", "2026-03-02")
    for x, y in [("T1", "T2"), ("T2", "T3"), ("T3", "T4"), ("T4", "T5")]:
        _rel(p, x, y)

    _act(p, "G1", "Set CRAHs MV 105", "B", "2026-06-01")
    _act(p, "G2", "Testing MV 105", "B", "2026-06-22")
    _rel(p, "G1", "G2")            # the user's own logic — must survive
    return p


def _fill(p, **kw):
    cmd = {"action": "fill_folder_from_template",
           "template_wbs": "MV 101", "target_wbs": "MV 105"}
    cmd.update(kw)
    return apply_command(p, cmd)


def _in(p, folder):
    return [a for a in p.activities if a.wbs_uid == folder]


# ── what must not happen ─────────────────────────────────────────────────────

def test_logic_the_user_already_set_survives():
    """The thing they are most afraid of losing."""
    p = _template_and_thin_target()
    ok, msg = _fill(p)
    assert ok, msg
    assert any(r.predecessor_uid == "G1" and r.successor_uid == "G2"
               for r in p.relations), "the user's own tie was removed"


def test_rows_that_were_already_there_are_not_touched():
    p = _template_and_thin_target()
    before = {a.uid: (a.name, a.planned_start, a.planned_duration)
              for a in _in(p, "B")}
    _fill(p)
    for uid, snap in before.items():
        a = next(x for x in p.activities if x.uid == uid)
        assert (a.name, a.planned_start, a.planned_duration) == snap


def test_nothing_is_ever_deleted():
    p = _template_and_thin_target()
    acts_before = {a.uid for a in p.activities}
    rels_before = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    _fill(p)
    assert acts_before <= {a.uid for a in p.activities}
    assert rels_before <= {(r.predecessor_uid, r.successor_uid) for r in p.relations}


def test_work_already_present_is_not_duplicated():
    """The failure that created the duplicate pairs in the first place."""
    p = _template_and_thin_target()
    _fill(p)
    names = [a.name for a in _in(p, "B")]
    assert len(names) == len(set(names)), f"duplicated rows: {names}"
    assert len(names) == 5, "should be the template's 5, not 5 + the 2 existing"


def test_running_it_twice_adds_nothing_the_second_time():
    p = _template_and_thin_target()
    _fill(p)
    after_first = (len(p.activities), len(p.relations))
    ok, msg = _fill(p)
    assert ok
    assert (len(p.activities), len(p.relations)) == after_first, (
        "a second run duplicated the folder — this must be safe to repeat")


# ── what must happen ─────────────────────────────────────────────────────────

def test_only_the_missing_activities_are_added():
    p = _template_and_thin_target()
    _fill(p)
    names = {a.name for a in _in(p, "B")}
    assert "Overhead Rough In MV 105" in names
    assert "Wall Rough Ins MV 105" in names
    assert "Electrical Pulls MV 105" in names


def test_the_area_carries_across_into_the_names():
    p = _template_and_thin_target()
    _fill(p)
    for a in _in(p, "B"):
        assert "MV 101" not in a.name, f"{a.name} still names the template area"


def test_the_template_logic_routes_through_the_existing_rows():
    """The new chain must connect to the activities that were already there,
    not run alongside them."""
    p = _template_and_thin_target()
    _fill(p)
    by_uid = {a.uid: a for a in p.activities}
    pairs = {(by_uid[r.predecessor_uid].name, by_uid[r.successor_uid].name)
             for r in p.relations
             if by_uid.get(r.predecessor_uid) and by_uid[r.predecessor_uid].wbs_uid == "B"}
    assert ("Set CRAHs MV 105", "Overhead Rough In MV 105") in pairs
    assert ("Electrical Pulls MV 105", "Testing MV 105") in pairs


def test_dates_land_in_the_targets_own_window_not_the_templates():
    """Every new row piling onto the template's February dates would be
    useless in an area that runs in June."""
    p = _template_and_thin_target()
    _fill(p)
    for a in _in(p, "B"):
        assert str(a.planned_start) >= "2026-06-01", (
            f"{a.name} was placed at the template's date, not the target's")


def test_with_logic_false_adds_rows_only():
    p = _template_and_thin_target()
    rels_before = len(p.relations)
    _fill(p, with_logic=False)
    assert len(_in(p, "B")) == 5
    assert len(p.relations) == rels_before


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_changes_absolutely_nothing():
    p = _template_and_thin_target()
    before = (len(p.activities), len(p.relations))
    ok, msg = _fill(p, preview=True)
    assert ok
    assert (len(p.activities), len(p.relations)) == before
    assert "PREVIEW" in msg and "nothing changed" in msg.lower()


def test_preview_says_how_many_and_which():
    p = _template_and_thin_target()
    _, msg = _fill(p, preview=True)
    assert "would add 3 of 5" in msg
    assert "Overhead Rough In MV 101" in msg


# ── refusals ─────────────────────────────────────────────────────────────────

def test_filling_a_folder_from_itself_is_refused():
    p = _template_and_thin_target()
    ok, msg = apply_command(p, {"action": "fill_folder_from_template",
                                "template_wbs": "MV 101", "target_wbs": "MV 101"})
    assert not ok and "template" in msg.lower()


def test_an_unknown_folder_is_refused_before_anything_is_added():
    p = _template_and_thin_target()
    before = len(p.activities)
    ok, _ = apply_command(p, {"action": "fill_folder_from_template",
                              "template_wbs": "MV 101", "target_wbs": "MV 999"})
    assert not ok
    assert len(p.activities) == before


def test_an_empty_template_is_refused():
    p = _p()
    _act(p, "G1", "Something", "B", "2026-06-01")
    ok, msg = apply_command(p, {"action": "fill_folder_from_template",
                                "template_wbs": "MV 101", "target_wbs": "MV 105"})
    assert not ok and "no activities" in msg.lower()


# ── several targets at once ──────────────────────────────────────────────────

def test_several_folders_can_be_filled_in_one_command():
    p = _template_and_thin_target()
    p.wbs_nodes.append(WBSNode(uid="C", name="MV 106", code="C"))
    _act(p, "H1", "Set CRAHs MV 106", "C", "2026-07-01")
    ok, msg = apply_command(p, {"action": "fill_folder_from_template",
                                "template_wbs": "MV 101",
                                "targets": ["MV 105", "MV 106"]})
    assert ok, msg
    assert len(_in(p, "B")) == 5
    assert len(_in(p, "C")) == 5
    assert "MV 105" in msg and "MV 106" in msg
