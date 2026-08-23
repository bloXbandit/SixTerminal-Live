"""
test_area_families.py — the ranker must read any job's naming, not one job's.

Area matching is what stops work in MV 105 being tied to work in MV 109: two
rows that name different places are almost never a handoff. That only works if
the tool can SEE the place, and it used to see it through a fixed list of
prefixes this trade happens to use — MV, UPS, CRAH, PDU. On a hospital of OR
and ICU rooms, a warehouse of DOCK and BAY, a hotel of numbered units, that
list matches nothing and the discrimination silently disappears.

So the place-words are learned from the project's own names. What is defended
here: a job whose rooms are called anything at all reads as well as the one
this was built on, and the things that merely LOOK numbered — wire sizes,
revisions, quantities — never become places, because two rows pulling
different wire are not in different rooms.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.logic_advisor import (learn_area_families, _area_tags, _Ctx,
                                  score_tie, implied_lag)
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _proj(names, wbs_names=()):
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W")]
    for i, n in enumerate(wbs_names):
        p.wbs_nodes.append(WBSNode(uid=f"w{i}", name=n, code=f"W{i}",
                                   parent_uid="w"))
    p.activities = [
        Activity(uid=f"a{i}", activity_id=f"A{1000 + i * 10}", name=n, wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-02",
                 planned_finish="2026-02-06")
        for i, n in enumerate(names)]
    p.build_lookups()
    return p


# ── it learns whatever the job calls its places ──────────────────────────────

def test_a_hospital_of_operating_rooms_is_read():
    fams = learn_area_families(_proj([
        "OR 3 Rough-In", "OR 4 Rough-In", "OR 7 Terminations"]))
    assert "or" in fams


def test_a_warehouse_of_docks_and_bays_is_read():
    fams = learn_area_families(_proj([
        "Dock 12 Levelers", "Dock 14 Levelers", "Bay 7 Racking", "Bay 9 Racking"]))
    assert "dock" in fams and "bay" in fams


def test_the_place_can_be_learned_from_the_folder_names():
    """Plenty of jobs put the room in the WBS, not the activity name."""
    fams = learn_area_families(_proj(["Pull Wire", "Terminations"],
                                     wbs_names=["POD 1", "POD 2", "POD 3"]))
    assert "pod" in fams


def test_the_original_data_centre_naming_still_works():
    assert "mv" in learn_area_families(_proj([
        "MV 105 Pull Wire", "MV 106 Pull Wire"]))


# ── it does not invent places ────────────────────────────────────────────────

def test_a_word_numbered_only_once_is_not_a_family():
    """One occurrence is a coincidence; a family is something numbered
    repeatedly."""
    assert "riser" not in learn_area_families(_proj([
        "Riser 1 Install", "Pull Wire", "Terminations"]))


def test_wire_sizes_never_become_places():
    """Two rows pulling different wire are not in different rooms — reading
    MCM as a place would penalise a perfectly good handoff."""
    fams = learn_area_families(_proj([
        "Pull 500 MCM Feeder", "Pull 750 MCM Feeder", "Terminate 500 MCM"]))
    assert "mcm" not in fams


def test_revisions_and_quantities_never_become_places():
    fams = learn_area_families(_proj([
        "Submittal Rev 2", "Submittal Rev 3", "Order Qty 40", "Order Qty 60"]))
    assert "rev" not in fams and "qty" not in fams


def test_electrical_ratings_never_become_places():
    fams = learn_area_families(_proj([
        "Set 480 V Panel", "Set 208 V Panel", "Feed 400 A Breaker",
        "Feed 800 A Breaker"]))
    assert "v" not in fams and "a" not in fams


def test_phases_and_levels_are_left_to_their_own_reading():
    """_AREA_RE already reads these; learning them again would double-tag."""
    fams = learn_area_families(_proj([
        "Phase 1 Start", "Phase 2 Start", "Level 3 Slab", "Level 4 Slab"]))
    assert "phase" not in fams and "level" not in fams


def test_a_conjunction_before_a_number_is_not_a_place():
    """"Pull 2 or 3 runs" is a range, not an operating room. Capitalisation is
    what separates it from OR 3, so a lowercase one must not qualify."""
    fams = learn_area_families(_proj([
        "Pull 2 or 3 runs", "Install 4 or 5 hangers", "Set 6 or 7 fixtures"]))
    assert "or" not in fams


def test_the_ordinary_word_room_is_allowed_to_be_a_place():
    """The general stopword list holds "room", "rm" and "unit" for judging
    shared subject. A job is perfectly entitled to number them."""
    assert "room" in learn_area_families(_proj(["Room 412 Paint", "Room 414 Paint"]))
    assert "rm" in learn_area_families(_proj(["RM 412 Paint", "RM 414 Paint"]))
    assert "unit" in learn_area_families(_proj(["Unit 3 Fit-Out", "Unit 5 Fit-Out"]))


def test_a_project_with_no_numbered_places_learns_nothing():
    assert learn_area_families(_proj(["Mobilize", "Excavate", "Backfill"])) == frozenset()


def test_an_empty_project_is_handled():
    assert learn_area_families(_proj([])) == frozenset()


# ── the learned families actually reach the tagging ──────────────────────────

def test_a_learned_family_is_tagged_as_a_place():
    assert "or3" in _area_tags("OR 3 Rough-In", frozenset({"or"}))


def test_an_unlearned_word_is_not_tagged():
    assert _area_tags("Widget 3 Install", frozenset({"or"})) == frozenset()


def test_tagging_without_families_still_reads_the_built_in_shapes():
    """Called with no families — as project_brain does — the old behaviour
    is unchanged."""
    assert "lvl3" in _area_tags("Level 3 Slab")
    assert "mv105" in _area_tags("MV 105 Pull Wire")


# ── and change the ranking, which is the point ───────────────────────────────

def _score(p, pred_id, succ_id):
    ctx = _Ctx(p)
    a = p.get_activity(activity_id=pred_id)
    b = p.get_activity(activity_id=succ_id)
    return score_tie(ctx, a, b, implied_lag(p, a, b))


def test_two_rows_in_the_same_learned_room_outrank_two_in_different_ones():
    """The whole reason this matters: on a hospital job, OR 3's rough-in
    should hand off to OR 3's terminations, not OR 9's."""
    p = _proj(["OR 3 Rough-In", "OR 3 Terminations", "OR 9 Terminations"])
    ids = {a.name: a.activity_id for a in p.activities}
    same, why = _score(p, ids["OR 3 Rough-In"], ids["OR 3 Terminations"])
    other, _ = _score(p, ids["OR 3 Rough-In"], ids["OR 9 Terminations"])
    assert same > other
    assert any("same area" in w for w in why)


def test_the_ranker_learns_the_families_without_being_told():
    p = _proj(["POD 1 Pull Wire", "POD 2 Pull Wire"])
    assert "pod" in _Ctx(p).families
