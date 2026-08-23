"""
test_objectives.py — knowing where we are in the thing we are doing.

The tool was excellent at "what should I do right now, given this schedule",
and had no idea "where are we in the campaign". 1,610 open ends looked
identical on day one and after a month of closing them, and the only way to
know you were making progress was to remember.

What is defended here: progress is MEASURED off the schedule every time, never
stored — a counter would drift the moment anything was edited outside the loop,
undone, or restored from a backup. The baseline is the only stored number,
because "how far through" is meaningless without a start. And the awkward cases
are reported honestly rather than clamped into a tidy percentage: work added
after the baseline can push the count UP, and saying so is the point.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import objectives as ob
from engine.project_brain import Brain
from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation)


def _project(n=6, milestones=0, constrained=0):
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="a", name="Area A", code="A", parent_uid="root"),
                   WBSNode(uid="b", name="Area B", code="B", parent_uid="root")]
    p.activities, p.relations = [], []
    for i in range(n):
        p.activities.append(Activity(
            uid=f"u{i}", activity_id=f"A{1000 + i * 10}", name=f"Work {i}",
            wbs_uid="a" if i < n // 2 else "b", calendar_uid="1",
            activity_type="Task Dependent", status="Not Started",
            planned_duration=40.0, remaining_duration=40.0,
            planned_start="2026-02-02", planned_finish="2026-02-06",
            constraint_type="Must Start On" if i < constrained else None))
    for m in range(milestones):
        p.activities.append(Activity(
            uid=f"m{m}", activity_id=f"M{100 + m}", name=f"Milestone {m}",
            wbs_uid="root", calendar_uid="1", activity_type="Finish Milestone",
            status="Not Started", planned_duration=0.0, remaining_duration=0.0,
            planned_finish="2026-06-01"))
    p.build_lookups()
    return p


def _link(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start", lag=0.0))
    p.build_lookups()


# ── counting off the schedule ────────────────────────────────────────────────

def test_open_ends_counts_rows_missing_either_end():
    p = _project(4)
    assert ob.remaining(p, ob.OPEN_ENDS) == 4
    _link(p, "u0", "u1")            # u0 gains a successor, u1 a predecessor
    assert ob.remaining(p, ob.OPEN_ENDS) == 4   # each still misses the other end
    _link(p, "u1", "u0")
    assert ob.remaining(p, ob.OPEN_ENDS) == 2   # u0 and u1 now have both


def test_open_starts_and_finishes_are_counted_separately():
    p = _project(4)
    _link(p, "u0", "u1")
    assert ob.remaining(p, ob.OPEN_STARTS) == 3
    assert ob.remaining(p, ob.OPEN_FINISHES) == 3


def test_unlinked_counts_only_the_completely_stranded():
    p = _project(4)
    _link(p, "u0", "u1")
    assert ob.remaining(p, ob.UNLINKED) == 2


def test_completed_work_is_never_outstanding():
    """A finished activity does not need a predecessor found for it — counting
    it would make an objective that can never reach zero."""
    p = _project(4)
    for a in p.activities:
        a.status = "Completed"
    assert ob.remaining(p, ob.OPEN_ENDS) == 0


def test_milestones_are_counted_by_their_own_objective_not_as_open_ends():
    p = _project(2, milestones=3)
    assert ob.remaining(p, ob.MILESTONES_ANCHORED) == 3
    assert ob.remaining(p, ob.OPEN_ENDS) == 2      # the milestones are not in here


def test_hard_constraints_are_counted():
    assert ob.remaining(_project(6, constrained=2), ob.HARD_CONSTRAINTS) == 2


def test_an_objective_can_be_scoped_to_one_branch():
    p = _project(6)
    assert ob.remaining(p, ob.OPEN_ENDS) == 6
    assert ob.remaining(p, ob.OPEN_ENDS, scope_uid="a") == 3


def test_an_unknown_kind_is_refused():
    try:
        ob.remaining(_project(2), "world_peace")
        assert False
    except ValueError:
        pass


# ── progress is measured, never stored ───────────────────────────────────────

def test_progress_moves_as_the_schedule_is_worked():
    p = _project(4)
    o = ob.make(p, ob.OPEN_ENDS)
    assert ob.progress(p, o)["percent"] == 0
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    rep = ob.progress(p, o)
    assert rep["done"] == 2 and rep["remaining"] == 2 and rep["percent"] == 50


def test_progress_reflects_an_edit_made_anywhere_not_just_through_the_loop():
    """The number is derived, so work done in the grid, by the agent, or by a
    re-upload all move it without anything being told."""
    p = _project(4)
    o = ob.make(p, ob.OPEN_ENDS)
    p.relations.append(Relation(uid="r", predecessor_uid="u0", successor_uid="u1",
                                type="Finish to Start", lag=0.0))
    p.relations.append(Relation(uid="r2", predecessor_uid="u1", successor_uid="u0",
                                type="Finish to Start", lag=0.0))
    p.build_lookups()
    assert ob.progress(p, o)["done"] == 2


def test_an_objective_that_reaches_zero_reads_as_complete():
    p = _project(2)
    o = ob.make(p, ob.OPEN_ENDS)
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    rep = ob.progress(p, o)
    assert rep["complete"] and rep["percent"] == 100 and rep["remaining"] == 0


def test_work_added_after_the_baseline_is_reported_as_growth_not_hidden():
    """Clamping this to 0% would say "no progress" when the truth is "the job
    got bigger" — a different thing, and the one worth knowing."""
    p = _project(2)
    o = ob.make(p, ob.OPEN_ENDS)
    p.activities.append(Activity(
        uid="new", activity_id="A9999", name="Late addition", wbs_uid="a",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=8.0, remaining_duration=8.0))
    p.build_lookups()
    rep = ob.progress(p, o)
    assert rep["grew"] and rep["done"] < 0
    assert "MORE than when this was set" in ob.line(p, o)


def test_a_baseline_of_zero_does_not_divide_by_zero():
    p = _project(2)
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    o = ob.make(p, ob.OPEN_ENDS)          # already clean
    assert o.baseline == 0
    assert ob.progress(p, o)["percent"] == 100


# ── the line the agent reads ─────────────────────────────────────────────────

def test_the_line_says_where_we_are_in_one_sentence():
    p = _project(4)
    o = ob.make(p, ob.OPEN_ENDS)
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    line = ob.line(p, o)
    assert "2 of 4 done (50%)" in line and "2" in line


def test_the_line_says_so_when_the_target_is_met():
    p = _project(2)
    o = ob.make(p, ob.OPEN_ENDS)
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    assert "OBJECTIVE MET" in ob.line(p, o)


def test_a_scoped_objective_names_where_it_applies():
    p = _project(6)
    o = ob.make(p, ob.OPEN_ENDS, scope_uid="a", scope_name="Area A")
    assert "Area A" in ob.line(p, o)


# ── what to set ──────────────────────────────────────────────────────────────

def test_suggestions_are_biggest_first_and_skip_what_is_already_clean():
    p = _project(6, milestones=2, constrained=1)
    picks = ob.suggest(p)
    counts = [x["outstanding"] for x in picks]
    assert counts == sorted(counts, reverse=True)
    assert all(x["outstanding"] > 0 for x in picks)


def test_a_clean_schedule_suggests_nothing():
    p = _project(2)
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    assert ob.suggest(p) == []


# ── it survives, and the agent is told ───────────────────────────────────────

def test_an_objective_survives_being_saved_and_reloaded():
    p = _project(4)
    b = Brain("J")
    b.set_objective(ob.make(p, ob.OPEN_ENDS))
    back = Brain.from_json(b.to_json())
    assert back.objective is not None
    assert back.objective.kind == ob.OPEN_ENDS and back.objective.baseline == 4


def test_a_target_whose_kind_no_longer_exists_does_not_break_a_turn():
    b = Brain.from_json({"key": "J", "objective": {"kind": "removed_kind"}})
    assert b.objective is None
    assert b.objective_line(_project(2)) == ""


def test_the_agent_is_told_the_objective_every_turn():
    p = _project(4)
    b = Brain("J")
    b.set_objective(ob.make(p, ob.OPEN_ENDS))
    block = b.context_block(p)
    assert "WHAT THIS PROJECT IS FOR" in block
    assert "0 of 4 done" in block


def test_no_objective_adds_nothing_to_the_prompt():
    p = _project(4)
    assert Brain("J").context_block(p) == ""


# ── through the routes ───────────────────────────────────────────────────────

def _client(project=None):
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = project or _project(4)
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def test_setting_and_reading_an_objective():
    c = _client()
    body = c.post("/api/objective", json={"kind": ob.OPEN_ENDS}).get_json()
    assert body["success"] and body["objective"]["baseline"] == 4
    got = c.get("/api/objective").get_json()
    assert got["objective"]["kind"] == ob.OPEN_ENDS


def test_the_route_reports_live_progress_not_the_stored_baseline():
    p = _project(4)
    c = _client(p)
    c.post("/api/objective", json={"kind": ob.OPEN_ENDS})
    _link(p, "u0", "u1")
    _link(p, "u1", "u0")
    assert c.get("/api/objective").get_json()["objective"]["done"] == 2


def test_the_route_offers_what_is_worth_setting():
    assert c_options(_client()) != []


def c_options(c):
    return c.get("/api/objective").get_json()["options"]


def test_an_unknown_kind_is_refused_by_the_route():
    c = _client()
    assert c.post("/api/objective", json={"kind": "nope"}).status_code == 400


def test_an_unknown_folder_scope_is_refused_by_the_route():
    c = _client()
    resp = c.post("/api/objective", json={"kind": ob.OPEN_ENDS, "wbs_uid": "ghost"})
    assert resp.status_code == 400


def test_an_objective_can_be_cleared():
    c = _client()
    c.post("/api/objective", json={"kind": ob.OPEN_ENDS})
    c.delete("/api/objective")
    assert c.get("/api/objective").get_json()["objective"] is None


def test_setting_one_survives_a_restart():
    """The whole point of hanging it on the brain — it is the job's, not the
    session's."""
    from engine import cloud_store, project_brain
    from tests.test_cloud_persistence import _install_fake, _wipe_all_memory
    _install_fake()
    p = _project(4)
    c = _client(p)
    c.post("/api/objective", json={"kind": ob.OPEN_ENDS})
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()
    key = project_brain.project_key(server._projects["t"]["project"])
    assert server._brains[key].objective.kind == ob.OPEN_ENDS
