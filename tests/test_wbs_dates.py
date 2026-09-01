"""
test_wbs_dates.py — the date range on a folder row, the way P6 spans a WBS.

Collapsed, a folder said how MUCH work was inside and nothing about WHEN,
which is the question you are asking when you collapse it.

Two things carry the feature and both are easy to get subtly wrong:

  It rolls up. A phase usually holds no activities directly — they live in its
  rooms — so a range read only from a folder's own rows leaves every parent
  blank on a branch containing hundreds of activities.

  It repaints in place. A date edit patches the ACTIVITY rows; the folder
  header is not among them, so its range would keep showing the state before
  the edit until something forced a reload. That is the same trap the flow
  tint fell into, and the fix is the same shape: diff the ranges across the
  edit and send only what moved.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job():
    p = Project(uid="p", name="M", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="PH2", name="Phase 2", code="P"),
                   WBSNode(uid="ER", name="ER R202", code="E", parent_uid="PH2"),
                   WBSNode(uid="MV", name="MV 108", code="M", parent_uid="PH2"),
                   WBSNode(uid="EMPTY", name="Nothing here", code="N",
                           parent_uid="PH2")]
    p.activities, p.relations = [], []
    for uid, f, s, fin in [("a1", "ER", "2026-03-02", "2026-03-06"),
                           ("a2", "ER", "2026-04-01", "2026-04-10"),
                           ("a3", "MV", "2026-02-02", "2026-02-06")]:
        p.activities.append(Activity(
            uid=uid, activity_id=uid, name="W", wbs_uid=f, calendar_uid="1",
            activity_type="Task Dependent", status="Not Started",
            planned_duration=40, remaining_duration=40,
            planned_start=s, planned_finish=fin))
    p.build_lookups()
    return p


@pytest.fixture
def client():
    p = _job()
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = p
    server._active_id[0] = "J"
    return server.app.test_client(), p


def _folders(c):
    return {w["name"]: w for w in c.get("/api/schedule").get_json()["wbs_sections"]}


# ── the range itself ─────────────────────────────────────────────────────────

def test_a_folder_spans_its_own_work(client):
    c, _ = client
    er = _folders(c)["ER R202"]
    assert er["start"] == "2026-03-02" and er["finish"] == "2026-04-10"


def test_a_parent_rolls_up_from_its_children(client):
    """A phase holds no activities directly; without the roll-up every parent
    reads blank on a branch containing hundreds."""
    c, _ = client
    ph = _folders(c)["Phase 2"]
    assert ph["start"] == "2026-02-02"      # earliest, from MV
    assert ph["finish"] == "2026-04-10"     # latest, from ER


def test_an_empty_folder_has_no_range_rather_than_a_wrong_one(client):
    c, _ = client
    e = _folders(c)["Nothing here"]
    assert e.get("start") is None and e.get("finish") is None


def test_an_actual_date_wins_over_the_planned_one(client):
    """Matching the Start/Finish columns on the rows themselves: a folder
    whose work has started reads from when it started, not when it was once
    planned to."""
    c, p = client
    a = p.get_activity(activity_id="a3")
    a.actual_start, a.status = "2026-01-20", "In Progress"
    p.build_lookups()
    f = _folders(c)
    assert f["MV 108"]["start"] == "2026-01-20"
    assert f["Phase 2"]["start"] == "2026-01-20", "the roll-up ignored the actual"


def test_a_folder_with_one_activity_spans_just_it(client):
    c, _ = client
    mv = _folders(c)["MV 108"]
    assert mv["start"] == "2026-02-02" and mv["finish"] == "2026-02-06"


# ── it keeps up with edits, without a reload ─────────────────────────────────

def test_a_date_edit_repaints_the_folder_and_its_parent(client):
    c, _ = client
    r = c.post("/api/direct", json={"commands": [
        {"action": "update_planned_date", "activity_id": "a1",
         "field": "finish", "date": "2026-05-20"}], "label": "stretch"}).get_json()
    moved = {x["uid"]: x for x in r["redated_folders"]}
    assert set(moved) == {"ER", "PH2"}, "the parent phase was not repainted"
    assert moved["PH2"]["finish"] == "2026-05-20"


def test_the_repaint_does_not_force_a_reload(client):
    """A date edit is a value edit — patching the rows is enough, and the
    folder range travels beside them."""
    c, _ = client
    r = c.post("/api/direct", json={"commands": [
        {"action": "update_planned_date", "activity_id": "a1",
         "field": "finish", "date": "2026-05-20"}], "label": "x"}).get_json()
    assert r.get("structural") is False


def test_an_untouched_folder_is_not_reported_as_changed(client):
    c, _ = client
    r = c.post("/api/direct", json={"commands": [
        {"action": "update_planned_date", "activity_id": "a1",
         "field": "finish", "date": "2026-05-20"}], "label": "x"}).get_json()
    assert "MV" not in {x["uid"] for x in r["redated_folders"]}


def test_an_edit_that_moves_no_date_reports_nothing(client):
    c, _ = client
    r = c.post("/api/direct", json={"commands": [
        {"action": "rename_activity", "activity_id": "a1",
         "new_name": "Renamed"}], "label": "x"}).get_json()
    assert r["redated_folders"] == []


def test_deleting_the_last_dated_activity_clears_the_range(client):
    c, _ = client
    r = c.post("/api/direct", json={"commands": [
        {"action": "delete_activity", "activity_id": "a3"}],
        "label": "del"}).get_json()
    moved = {x["uid"]: x for x in r["redated_folders"]}
    assert moved["MV"]["start"] is None and moved["MV"]["finish"] is None


# ── the roll-up helper on its own ────────────────────────────────────────────

def test_the_range_helper_covers_every_folder(client):
    c, p = client
    rng = server._wbs_date_range(p)
    assert set(rng) == {"PH2", "ER", "MV", "EMPTY"}
    assert rng["PH2"] == ("2026-02-02", "2026-04-10")
    assert rng["EMPTY"] == (None, None)


def test_a_folder_with_no_parent_chain_is_still_measured():
    """A node whose parent is missing must not be skipped by the walk."""
    p = _job()
    p.wbs_nodes.append(WBSNode(uid="LOOSE", name="Loose", code="L",
                               parent_uid="does-not-exist"))
    p.activities.append(Activity(
        uid="z", activity_id="z", name="W", wbs_uid="LOOSE", calendar_uid="1",
        activity_type="Task Dependent", status="Not Started",
        planned_duration=40, remaining_duration=40,
        planned_start="2027-01-04", planned_finish="2027-01-08"))
    p.build_lookups()
    assert server._wbs_date_range(p)["LOOSE"] == ("2027-01-04", "2027-01-08")
