"""
test_logic_proposals.py — a logic suggestion should carry something to click.

recommend_logic already ranks real candidates (implied lag, verdict,
rationale) before flattening them into the text the agent reads back as a
report. Until now that ranked data never left the text: a user reading
"Terminations should follow Pull Wire" in the chat had no way to make that
tie except retyping it as a new instruction. /api/edit now hands the same
rows back as `logic_proposals` so the frontend can put a real Apply button
on each one.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities, p.relations = [], []
    for uid, name, s, f in [("a1", "Pull Wire MV 105", "2026-02-02", "2026-02-06"),
                            ("a2", "Terminations MV 105", "2026-02-09", "2026-02-13")]:
        p.activities.append(Activity(
            uid=uid, activity_id=uid.upper(), name=name, wbs_uid="w",
            calendar_uid="1", activity_type="Task Dependent", status="Not Started",
            planned_duration=5.0, remaining_duration=5.0,
            planned_start=s, planned_finish=f))
    p.build_lookups()
    return p


def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _proj()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _run(c, commands, instruction="what's missing in Electrical?"):
    return c.post("/api/edit", json={"instruction": instruction,
                                     "force_commands": commands}).get_json()


def test_a_wbs_scope_report_with_real_candidates_carries_clickable_rows():
    c = _client()
    body = _run(c, [{"action": "recommend_logic", "scope": "wbs",
                     "wbs_name": "Electrical"}])
    prop = body["logic_proposals"]
    assert prop is not None
    assert prop["scope"] == "wbs"
    items = prop["items"]
    assert items and items[0]["predecessor_id"] == "A1"
    assert items[0]["successor_id"] == "A2"
    assert "verdict" in items[0] and "implied_lag_days" in items[0]


def test_a_report_with_nothing_found_carries_no_dead_card():
    """No milestones exist in this project at all — a card with nothing in
    it would be worse than no card."""
    c = _client()
    body = _run(c, [{"action": "recommend_logic", "scope": "milestones"}])
    assert body["logic_proposals"] is None


def test_a_turn_with_no_recommend_logic_at_all_carries_none():
    c = _client()
    body = _run(c, [{"action": "add_relation", "predecessor_id": "A1",
                     "successor_id": "A2", "type": "fs"}])
    assert body["logic_proposals"] is None


def test_applying_a_proposed_row_actually_ties_the_schedule():
    """The row's own ids must be real, live ids — apply them straight
    through /api/direct, the same route the card's button hits."""
    c = _client()
    body = _run(c, [{"action": "recommend_logic", "scope": "wbs",
                     "wbs_name": "Electrical"}])
    row = body["logic_proposals"]["items"][0]
    applied = c.post("/api/direct", json={"commands": [
        {"action": "add_relation", "predecessor_id": row["predecessor_id"],
         "successor_id": row["successor_id"], "type": "fs", "lag_days": 0}],
        "label": "test"}).get_json()
    assert applied["success"]
    assert len(server._projects["t"]["project"].relations) == 1
