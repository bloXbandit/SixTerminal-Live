"""
test_llm_context.py — What the agent actually gets to see.

Folder names repeat constantly in real schedules (every building has a
"Level 1", every level an "ER 209"). These tests pin the two things that broke
because of that: the risk rollup merging unrelated folders that share a name,
and the tree rendering flat so nesting was invisible.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation, compute_dates)
from engine.edit_engine import apply_command


def _proj():
    """Two buildings, each with a 'Level 1'. Building A nests an ER room."""
    p = Project(uid="1", name="T", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="A",   name="Building A", code="BLDA"),
        WBSNode(uid="A1",  name="Level 1",    code="BLDA-L1",    parent_uid="A"),
        WBSNode(uid="A1E", name="ER 209",     code="BLDA-L1-ER", parent_uid="A1"),
        WBSNode(uid="B",   name="Building B", code="BLDB"),
        WBSNode(uid="B1",  name="Level 1",    code="BLDB-L1",    parent_uid="B"),
    ]
    p.activities = [
        Activity(uid="1", activity_id="A1000", name="deep act", wbs_uid="A1E",
                 calendar_uid="1", planned_duration=40.0),
        Activity(uid="2", activity_id="A1010", name="a-l1", wbs_uid="A1",
                 calendar_uid="1", planned_duration=40.0),
        Activity(uid="3", activity_id="A1020", name="b-l1 one", wbs_uid="B1",
                 calendar_uid="1", planned_duration=40.0),
        Activity(uid="4", activity_id="A1030", name="b-l1 two", wbs_uid="B1",
                 calendar_uid="1", planned_duration=40.0),
    ]
    p.build_lookups()
    compute_dates(p)
    return p


def _wbs_block(p):
    out, inside = [], False
    for line in p.llm_context().split("\n"):
        if line.startswith("WBS STRUCTURE"):
            inside = True
            continue
        if inside:
            if not line.strip():
                break
            out.append(line)
    return out


def _line_for(p, code):
    return next(l for l in _wbs_block(p) if l.strip().startswith(code + " "))


# ── Hierarchy ────────────────────────────────────────────────────────────────

def test_nesting_is_visible_through_indentation():
    """A child must be indented deeper than its parent — with two fixed indent
    levels a 3-deep tree rendered flat and the agent could not tell what
    contained what."""
    p = _proj()
    ind = lambda l: len(l) - len(l.lstrip())
    assert ind(_line_for(p, "BLDA")) < ind(_line_for(p, "BLDA-L1")) \
        < ind(_line_for(p, "BLDA-L1-ER"))


def test_every_folder_appears_exactly_once():
    p = _proj()
    block = _wbs_block(p)
    for code in ("BLDA", "BLDA-L1", "BLDA-L1-ER", "BLDB", "BLDB-L1"):
        assert sum(1 for l in block if l.strip().startswith(code + " ")) == 1


# ── Rollup ───────────────────────────────────────────────────────────────────

def test_same_named_folders_do_not_share_a_rollup():
    """Both buildings have a 'Level 1'. Keying the rollup by name merged them
    and reported a count belonging to neither."""
    p = _proj()
    a_l1 = _line_for(p, "BLDA-L1")     # 1 direct + 1 in its ER room
    b_l1 = _line_for(p, "BLDB-L1")     # 2 direct
    assert "2 acts (1 direct)" in a_l1
    assert "2 acts" in b_l1 and "direct" not in b_l1


def test_a_parent_rolls_up_work_held_in_its_children():
    p = _proj()
    assert "2 acts (0 direct)" in _line_for(p, "BLDA")


def test_ambiguous_folder_names_are_disambiguated_on_activity_lines():
    """'WBS: Level 1' is useless when two exist — an edit aimed at it could
    land in the wrong building."""
    p = _proj()
    ctx = p.llm_context()
    assert "WBS: Building A / Level 1" in ctx
    assert "WBS: Building B / Level 1" in ctx


def test_unique_folder_names_stay_short():
    p = _proj()
    p.wbs_nodes.append(WBSNode(uid="S", name="Sitework", code="SITE"))
    p.activities.append(Activity(uid="9", activity_id="A9000", name="site",
                                 wbs_uid="S", calendar_uid="1", planned_duration=8.0))
    p.build_lookups()
    assert "WBS: Sitework" in p.llm_context()


# ── Data date ────────────────────────────────────────────────────────────────

def test_set_data_date():
    p = _proj()
    ok, msg = apply_command(p, {"action": "set_data_date", "data_date": "2026-03-02"})
    assert ok, msg
    assert p.data_date == "2026-03-02"
    assert "2026-03-02" in msg


def test_set_data_date_can_move_the_project_start_too():
    p = _proj()
    apply_command(p, {"action": "set_data_date", "data_date": "2026-03-02",
                      "also_planned_start": True})
    assert p.planned_start == "2026-03-02"


def test_set_data_date_rejects_a_bad_date():
    p = _proj()
    before = p.data_date
    ok, msg = apply_command(p, {"action": "set_data_date", "data_date": "next tuesday"})
    assert ok is False
    assert p.data_date == before


def test_data_date_reaches_the_agent_context():
    p = _proj()
    apply_command(p, {"action": "set_data_date", "data_date": "2026-03-02"})
    assert "Data Date: 2026-03-02" in p.llm_context()


# ── Context size / format tiering ────────────────────────────────────────────

def _bulk(n):
    p = Project(uid="1", name="X", id="X")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W")]
    p.activities = [Activity(uid=str(i), activity_id=f"A{1000+i*10}",
                             name=f"Activity {i} rough-in work", wbs_uid="w",
                             calendar_uid="1", planned_duration=40.0)
                    for i in range(n)]
    p.build_lookups()
    return p


def test_compact_format_engages_just_above_the_threshold():
    """The format switch and the hard cap were the same number, so compact only
    engaged past 3000 and mid-size schedules paid roughly double the context."""
    assert "compact)" not in _bulk(400).llm_context()
    assert "compact)" in _bulk(401).llm_context()


def test_no_size_cliff_either_side_of_the_activity_cap():
    small = len(_bulk(2999).llm_context())
    big   = len(_bulk(3001).llm_context())
    assert abs(big - small) / small < 0.05     # was ~2x


def test_three_thousand_activities_stay_within_a_workable_context():
    ctx = _bulk(3000).llm_context()
    approx_tokens = len(ctx) / 4
    assert approx_tokens < 80_000, f"~{approx_tokens:,.0f} tokens is too large"
