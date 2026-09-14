"""
test_grid_stays_put.py — an edit must not cost you your place on screen.

Rebuilding the grid means refetching a megabyte and recreating ~55,000 DOM
nodes, and it throws away scroll position and every collapsed folder. On a
2,400-activity job that is the difference between an edit and an interruption.

A reload is only honest when the SHAPE changed — rows added, removed or
re-ordered, folders re-parented. These hold the line for the three edits that
were forcing one without changing the shape at all: renumbering activity ids,
typing a date, and adding a logic tie.

The id case is the one that bit. The diff used to key on activity_id — a value
the user can CHANGE — so normalizing ids across a job read as every row being
deleted and a different row added.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode, compute_dates)


@pytest.fixture
def app():
    p = Project(uid="1", name="J", id="J", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
    p.activities = [
        Activity(uid=f"u{i}", activity_id=f"A{1000 + i * 10}", name=f"Task {i}",
                 wbs_uid="w", calendar_uid="1", planned_duration=40,
                 remaining_duration=40, planned_start="2026-02-02",
                 planned_finish="2026-02-06")
        for i in range(6)
    ]
    p.relations = [Relation(uid="r1", predecessor_uid="u0", successor_uid="u1")]
    p.build_lookups()
    compute_dates(p, hold_unlinked_dates=True, apply_dates=False)
    server._projects.clear()
    server._brains.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = p
    server._active_id[0] = "J"
    return server.app.test_client(), p


def _direct(client, commands, label="t"):
    return client.post("/api/direct",
                       json={"commands": commands, "label": label}).get_json()


def test_renumbering_ids_patches_instead_of_rebuilding(app):
    """The reported case: normalize the ids and the whole screen recalibrates."""
    c, p = app
    d = _direct(c, [{"action": "update_activity_id", "activity_id": a.activity_id,
                     "new_activity_id": f"MDC1.AREA.{1000 + i * 10}"}
                    for i, a in enumerate(p.activities)])
    assert not d["structural"], "a renumber still forces a full reload"
    assert not d["removed_ids"], "rows read as deleted"
    assert not d["added_rows"], "rows read as newly added"
    assert d["changed_rows"], "nothing was patched either"


def test_a_renamed_row_is_identified_by_something_that_did_not_change(app):
    """The patch has to carry the uid, or the client cannot find the row it
    is meant to repaint — its id on screen is still the old one."""
    c, p = app
    old = p.activities[0].activity_id
    d = _direct(c, [{"action": "update_activity_id", "activity_id": old,
                     "new_activity_id": "NEW.1"}])
    row = next((r for r in d["changed_rows"] if r.get("activity_id") == "NEW.1"), None)
    assert row is not None
    assert row.get("uid") == "u0", "the patch cannot be matched to a row"


def test_typing_a_date_patches(app):
    c, p = app
    d = _direct(c, [{"action": "update_planned_date",
                     "activity_id": p.activities[3].activity_id,
                     "field": "start", "date": "2026-03-02"}])
    assert not d["structural"]


def test_adding_a_logic_tie_patches(app):
    c, p = app
    d = _direct(c, [{"action": "add_relation",
                     "predecessor_id": p.activities[2].activity_id,
                     "successor_id": p.activities[3].activity_id, "type": "fs"}])
    assert not d["structural"]
    assert d["changed_rows"]


def test_a_real_shape_change_still_reloads(app):
    """The guard is not simply switched off — adding a folder must still
    rebuild, because row placement and indentation depend on the tree."""
    c, _ = app
    d = _direct(c, [{"action": "add_wbs", "name": "New Area", "code": "NA"}])
    assert d["structural"], "a tree change quietly became a patch"


def test_the_grid_row_carries_the_uid():
    """The client half. Without it the row cannot be found after a renumber."""
    html = open(os.path.join(os.path.dirname(__file__), "..", "ui",
                             "templates", "index.html"), encoding="utf-8").read()
    assert 'data-uid="${escAttr(a.uid' in html, "grid rows have no stable handle"
    assert "function rowByUid(" in html
    assert "rowByUid(row.uid)" in html, "patchRows still looks up by the id"
