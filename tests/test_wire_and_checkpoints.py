"""
test_wire_and_checkpoints.py — closing open ends in bulk, and being able to
undo having done so.

The reference schedule has 1,610 activities with no predecessor and 1,608 with
no successor. One at a time is not a plan, so wire_folder proposes the single
best predecessor for every open row in an area — a reviewable batch rather
than another decision per activity. Its bar is higher than the single-activity
view because a wrong tie applied in bulk is wrong many times over.

Checkpoints exist because of that: undo is a stack fifty deep, and one bulk
apply can burn through it. A checkpoint is a named snapshot to come back to,
and restoring one is itself undoable.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.logic_advisor import wire_folder
from engine.schedule_model import Project, Activity, WBSNode, Calendar, Relation


def _proj():
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J", parent_uid=None),
                   WBSNode(uid="str", name="Structure", code="S", parent_uid="root"),
                   WBSNode(uid="site", name="Sitework", code="W", parent_uid="root")]
    p.activities = []
    p.relations = []
    return p


def _act(p, uid, name, start, finish, wbs="str", status="Not Started"):
    a = Activity(uid=uid, activity_id=uid.upper(), name=name, wbs_uid=wbs,
                 calendar_uid="1", activity_type="Task Dependent", status=status,
                 planned_duration=40.0, remaining_duration=40.0,
                 planned_start=start, planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _chain():
    """A precast area in sequence, entirely unlinked."""
    p = _proj()
    _act(p, "a1", "Precast Erection Area 1", "2026-01-05", "2026-01-30")
    _act(p, "a2", "Precast Area 1 Turnover", "2026-02-02", "2026-02-06")
    _act(p, "a3", "Precast Erection Area 2", "2026-02-09", "2026-03-06")
    _act(p, "a4", "Precast Area 2 Turnover", "2026-03-09", "2026-03-13")
    return p


# ── proposing ties ────────────────────────────────────────────────────────────

def test_open_rows_in_the_area_get_a_proposal():
    d = wire_folder(_chain(), "str")
    assert d["open_starts"] == 4
    assert d["proposals"]


def test_each_proposal_names_a_real_pair():
    p = _chain()
    real = {a.activity_id for a in p.activities}
    for r in wire_folder(p, "str")["proposals"]:
        assert r["predecessor_id"] in real and r["successor_id"] in real
        assert r["predecessor_id"] != r["successor_id"]


def test_the_predecessor_always_finishes_before_the_successor_starts():
    p = _chain()
    by = {a.activity_id: a for a in p.activities}
    for r in wire_folder(p, "str")["proposals"]:
        assert by[r["predecessor_id"]].planned_finish <= by[r["successor_id"]].planned_start


def test_the_obvious_sequence_is_found():
    d = wire_folder(_chain(), "str")
    pairs = {(r["predecessor_id"], r["successor_id"]) for r in d["proposals"]}
    assert ("A1", "A2") in pairs


def test_only_one_proposal_per_activity():
    """A reviewable batch, not another decision per row."""
    d = wire_folder(_chain(), "str")
    succs = [r["successor_id"] for r in d["proposals"]]
    assert len(succs) == len(set(succs))


def test_an_activity_that_already_has_a_predecessor_is_left_alone():
    p = _chain()
    p.relations = [Relation(uid="r", predecessor_uid="a1", successor_uid="a2",
                            type="Finish to Start", lag=0.0)]
    p.build_lookups()
    d = wire_folder(p, "str")
    assert not any(r["successor_id"] == "A2" for r in d["proposals"])


def test_work_outside_the_folder_is_not_touched():
    p = _chain()
    _act(p, "s1", "Paving", "2026-01-05", "2026-01-09", wbs="site")
    d = wire_folder(p, "str")
    ids = {r["successor_id"] for r in d["proposals"]} | {r["predecessor_id"] for r in d["proposals"]}
    assert "S1" not in ids


def test_completed_work_needs_no_predecessor():
    p = _chain()
    for a in p.activities:
        a.status = "Completed"
    assert wire_folder(p, "str")["open_starts"] == 0


def test_rows_with_no_candidate_are_counted_not_hidden():
    """The number that could not be answered has to be visible."""
    p = _proj()
    _act(p, "a1", "Widget Reticulation", "2026-06-01", "2026-06-05")
    d = wire_folder(p, "str")
    assert d["proposals"] == [] and d["unresolved"] == 1


def test_a_higher_bar_proposes_fewer_ties():
    p = _chain()
    lo = len(wire_folder(p, "str", min_confidence=0.3)["proposals"])
    hi = len(wire_folder(p, "str", min_confidence=0.8)["proposals"])
    assert hi <= lo


def test_proposals_come_back_most_confident_first():
    d = wire_folder(_chain(), "str", min_confidence=0.3)
    confs = [r["confidence"] for r in d["proposals"]]
    assert confs == sorted(confs, reverse=True)


def test_an_empty_folder_is_handled():
    assert wire_folder(_proj(), "site")["proposals"] == []


# ── through the API ───────────────────────────────────────────────────────────

def _client(p):
    c = server.app.test_client()
    server._projects["t"] = server._make_session("t", "t.xml")
    server._projects["t"]["project"] = p
    server._active_id[0] = "t"
    return c


def test_the_wire_endpoint_names_the_folder():
    body = _client(_chain()).get("/api/advise/wire?wbs_uid=str").get_json()
    assert body["wbs_name"] == "Structure" and body["proposals"]


def test_the_wire_endpoint_needs_a_folder():
    assert "error" in _client(_chain()).get("/api/advise/wire").get_json()


def test_an_unknown_folder_is_reported():
    r = _client(_chain()).get("/api/advise/wire?wbs_uid=nope")
    assert r.status_code == 404


# ── checkpoints ───────────────────────────────────────────────────────────────

def test_a_checkpoint_can_be_saved_and_listed():
    c = _client(_chain())
    c.post("/api/checkpoint", json={"label": "before wiring"})
    cps = c.get("/api/checkpoint").get_json()["checkpoints"]
    assert len(cps) == 1 and cps[0]["label"] == "before wiring"
    assert cps[0]["activity_count"] == 4


def test_an_unnamed_checkpoint_gets_a_timestamp():
    c = _client(_chain())
    body = c.post("/api/checkpoint", json={}).get_json()
    assert body["saved"] and "Checkpoint" in body["saved"]


def test_restoring_puts_the_schedule_back():
    p = _chain()
    c = _client(p)
    c.post("/api/checkpoint", json={"label": "clean"})
    cid = c.get("/api/checkpoint").get_json()["checkpoints"][0]["id"]

    c.post("/api/direct", json={"commands": [
        {"action": "delete_activity", "activity_id": "A1"},
        {"action": "delete_activity", "activity_id": "A2"}], "label": "damage"})
    assert len(server._get_session()["project"].activities) == 2

    body = c.post("/api/checkpoint", json={"action": "restore", "id": cid}).get_json()
    assert body["activity_count"] == 4
    assert len(server._get_session()["project"].activities) == 4


def test_restoring_is_itself_undoable():
    p = _chain()
    c = _client(p)
    c.post("/api/checkpoint", json={"label": "clean"})
    cid = c.get("/api/checkpoint").get_json()["checkpoints"][0]["id"]
    c.post("/api/direct", json={"commands": [
        {"action": "delete_activity", "activity_id": "A1"}], "label": "damage"})
    c.post("/api/checkpoint", json={"action": "restore", "id": cid})
    assert len(server._get_session()["project"].activities) == 4
    c.post("/api/undo")
    assert len(server._get_session()["project"].activities) == 3


def test_a_checkpoint_is_a_copy_not_a_reference():
    """Editing after saving must not change what the checkpoint holds."""
    c = _client(_chain())
    c.post("/api/checkpoint", json={"label": "clean"})
    c.post("/api/direct", json={"commands": [
        {"action": "rename_activity", "activity_id": "A1",
         "new_name": "Renamed"}], "label": "x"})
    cid = c.get("/api/checkpoint").get_json()["checkpoints"][0]["id"]
    c.post("/api/checkpoint", json={"action": "restore", "id": cid})
    a = server._get_session()["project"].get_activity(activity_id="A1")
    assert a.name == "Precast Erection Area 1"


def test_a_checkpoint_can_be_deleted():
    c = _client(_chain())
    c.post("/api/checkpoint", json={"label": "one"})
    cid = c.get("/api/checkpoint").get_json()["checkpoints"][0]["id"]
    body = c.delete("/api/checkpoint", json={"id": cid}).get_json()
    assert body["checkpoints"] == []


def test_restoring_something_deleted_is_reported():
    c = _client(_chain())
    r = c.post("/api/checkpoint", json={"action": "restore", "id": "nope"})
    assert r.status_code == 404


def test_old_checkpoints_are_dropped_rather_than_growing_forever():
    c = _client(_chain())
    for i in range(server._MAX_CHECKPOINTS + 5):
        c.post("/api/checkpoint", json={"label": f"cp{i}"})
    cps = c.get("/api/checkpoint").get_json()["checkpoints"]
    assert len(cps) == server._MAX_CHECKPOINTS
    assert cps[-1]["label"] == f"cp{server._MAX_CHECKPOINTS + 4}"
