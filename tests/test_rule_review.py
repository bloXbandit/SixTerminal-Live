"""
test_rule_review.py — rules that have stopped earning their place.

A rule overridden thirty times looked exactly like one never questioned. It
went on blocking with full confidence, and the only record that it kept losing
was in the user's memory. Meanwhile a rule taught when the schedule was small
went on claiming "binds 12 activities" long after those twelve were renamed or
deleted, because grounding ran once, at teaching time, and never again.

Two different failures wanting two different answers, and the tool was blind to
both. What is defended here: overrides and back-offs are both recorded,
grounding is re-run against the schedule as it IS, a demotion can be undone
when the work finally appears, and nothing is decided automatically — a rule
the user meant stays a rule however often it loses an argument. The job of
this layer is to stop the problem being invisible, not to resolve it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import project_brain as pb
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _project(names=("Terminations", "QA/QC Inspections")):
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E")]
    p.activities = [
        Activity(uid=f"a{i}", activity_id=f"A{1000 + i * 10}", name=n, wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-02",
                 planned_finish="2026-02-06")
        for i, n in enumerate(names)]
    p.build_lookups()
    return p


_RULE = "QA/QC inspections follow terminations"


def _brain_with_rule(project=None):
    b = pb.Brain("J")
    b.add(_RULE, project)
    return b, b.directives[0]


# ── how a rule fared when it actually bit ────────────────────────────────────

def test_an_override_is_recorded_against_the_rule():
    b, d = _brain_with_rule(_project())
    b.record_conflict(d.id, overridden=True)
    assert d.overridden == 1 and d.upheld == 0
    assert d.last_conflict_at


def test_backing_off_is_recorded_too():
    b, d = _brain_with_rule(_project())
    b.record_conflict(d.id, overridden=False)
    assert d.upheld == 1 and d.overridden == 0


def test_recording_against_a_rule_that_is_gone_is_harmless():
    b, _ = _brain_with_rule(_project())
    assert b.record_conflict("nope", overridden=True) is None


# ── a contested rule gets raised ─────────────────────────────────────────────

def test_one_override_is_an_exception_not_a_pattern():
    b, d = _brain_with_rule(_project())
    b.record_conflict(d.id, overridden=True)
    assert b.needs_review() == []


def test_three_overrides_raise_it():
    b, d = _brain_with_rule(_project())
    for _ in range(3):
        b.record_conflict(d.id, overridden=True)
    review = b.needs_review()
    assert len(review) == 1
    assert review[0]["reason"] == "overridden" and review[0]["overridden"] == 3


def test_a_rule_that_mostly_holds_is_not_raised():
    """Three overrides against five back-offs is a rule doing its job with
    exceptions, not a rule that is wrong."""
    b, d = _brain_with_rule(_project())
    for _ in range(3):
        b.record_conflict(d.id, overridden=True)
    for _ in range(5):
        b.record_conflict(d.id, overridden=False)
    assert b.needs_review() == []


def test_a_contested_rule_still_enforces():
    """It is raised, not disarmed. A rule the user meant stays a rule however
    often it loses an argument with the schedule."""
    p = _project()
    b, d = _brain_with_rule(p)
    for _ in range(9):
        b.record_conflict(d.id, overridden=True)
    assert d.kind == pb.ORDER and d in b.rules
    assert pb.directive_verdict(d, "QA/QC Inspections", "Terminations") == "violates"


def test_the_agent_is_told_a_rule_is_contested():
    p = _project()
    b, d = _brain_with_rule(p)
    for _ in range(4):
        b.record_conflict(d.id, overridden=True)
    block = b.context_block(p)
    assert "CONTESTED" in block and "overridden 4" in block


def test_a_rule_nobody_argues_with_is_not_called_contested():
    p = _project()
    b, _ = _brain_with_rule(p)
    assert "CONTESTED" not in b.context_block(p)


# ── acknowledging it ─────────────────────────────────────────────────────────

def test_keeping_it_silences_the_prompt_in_one_click():
    """Counting an acknowledgement as an uphold would need three clicks to
    clear a three-override flag."""
    b, d = _brain_with_rule(_project())
    for _ in range(4):
        b.record_conflict(d.id, overridden=True)
    b.acknowledge(d.id)
    assert b.needs_review() == []


def test_keeping_it_does_not_pretend_the_rule_won():
    b, d = _brain_with_rule(_project())
    for _ in range(4):
        b.record_conflict(d.id, overridden=True)
    b.acknowledge(d.id)
    assert d.upheld == 0 and d.overridden == 4


def test_it_is_raised_again_if_it_goes_on_losing_after_that():
    b, d = _brain_with_rule(_project())
    for _ in range(4):
        b.record_conflict(d.id, overridden=True)
    b.acknowledge(d.id)
    for _ in range(3):
        b.record_conflict(d.id, overridden=True)
    assert len(b.needs_review()) == 1


# ── a rule that quietly stopped matching anything ────────────────────────────

def test_a_rule_binding_nothing_is_raised_as_orphaned():
    b, d = _brain_with_rule(_project(names=("Excavate", "Backfill")))
    review = b.needs_review()
    assert len(review) == 1 and review[0]["reason"] == "orphaned"


def test_a_rule_that_binds_is_not_raised():
    b, _ = _brain_with_rule(_project())
    assert b.needs_review() == []


def test_regrounding_notices_the_work_was_renamed_away():
    """Taught when the work existed; the schedule changed underneath it."""
    p = _project()
    b, d = _brain_with_rule(p)
    assert d.kind == pb.ORDER
    p.get_activity(activity_id="A1010").name = "Quality Walk"
    p.build_lookups()
    b.reground(p)
    assert d.kind == pb.NOTE
    assert b.needs_review()[0]["reason"] == "orphaned"


def test_a_demoted_rule_comes_back_when_the_work_appears():
    """Grounding must not be a one-way door: a rule taught before the work was
    in the file has to start enforcing once it is."""
    p = _project(names=("Excavate", "Backfill"))
    b, d = _brain_with_rule(p)
    assert d.kind == pb.NOTE
    p.activities.append(Activity(
        uid="x1", activity_id="A2000", name="Terminations", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=8.0, remaining_duration=8.0))
    p.activities.append(Activity(
        uid="x2", activity_id="A2010", name="QA/QC Inspections", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=8.0, remaining_duration=8.0))
    p.build_lookups()
    b.reground(p)
    assert d.kind == pb.ORDER and not d.note_reason
    assert b.needs_review() == []


def test_regrounding_refreshes_the_match_counts():
    p = _project()
    b, d = _brain_with_rule(p)
    before = d.matched_after
    p.activities.append(Activity(
        uid="x", activity_id="A3000", name="Terminations", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=8.0, remaining_duration=8.0))
    p.build_lookups()
    b.reground(p)
    assert d.matched_after == before + 1


def test_regrounding_reports_only_what_changed():
    p = _project()
    b, _ = _brain_with_rule(p)
    assert b.reground(p) == []


def test_regrounding_is_idempotent():
    p = _project()
    b, d = _brain_with_rule(p)
    b.reground(p)
    counts = (d.kind, d.matched_after, d.matched_subject)
    b.reground(p)
    assert (d.kind, d.matched_after, d.matched_subject) == counts


def test_a_plain_note_is_never_raised_as_orphaned():
    """It was never a rule, so it is not failing to be one."""
    b = pb.Brain("J")
    b.add("Owner wants the CUP energised early", _project())
    assert b.needs_review() == []


# ── it all survives ──────────────────────────────────────────────────────────

def test_the_record_survives_being_saved_and_reloaded():
    b, d = _brain_with_rule(_project())
    for _ in range(3):
        b.record_conflict(d.id, overridden=True)
    b.record_conflict(d.id, overridden=False)
    back = pb.Brain.from_json(b.to_json())
    r = back.directives[0]
    assert r.overridden == 3 and r.upheld == 1
    assert len(back.needs_review()) == 1


def test_an_older_saved_rule_without_the_new_fields_still_loads():
    """Records written before any of this existed must not break."""
    back = pb.Brain.from_json({"key": "J", "directives": [
        {"id": "x", "text": _RULE, "kind": "order",
         "subject": "QA/QC inspections", "after": "terminations"}]})
    d = back.directives[0]
    assert d.kind == pb.ORDER and d.overridden == 0
    # and re-grounding an old record recovers the shape rather than freezing it
    back.reground(_project())
    assert d.parsed_kind == pb.ORDER and d.kind == pb.ORDER


# ── through the routes ───────────────────────────────────────────────────────

def _client(project=None):
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = project or _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _live_brain():
    return server._brains[pb.project_key(server._projects["t"]["project"])]


def _teach(c, text=_RULE):
    return c.post("/api/brain", json={"text": text}).get_json()["directive"]["id"]


def test_an_override_through_the_route_is_recorded():
    c = _client()
    did = _teach(c)
    body = c.post("/api/brain/conflict",
                  json={"directive_ids": [did], "overridden": True}).get_json()
    assert body["recorded"] == 1
    assert _live_brain().directives[0].overridden == 1


def test_the_review_route_raises_a_contested_rule():
    c = _client()
    did = _teach(c)
    for _ in range(3):
        c.post("/api/brain/conflict", json={"directive_ids": [did], "overridden": True})
    review = c.get("/api/brain/review").get_json()["review"]
    assert len(review) == 1 and review[0]["reason"] == "overridden"


def test_the_review_route_regrounds_against_the_current_schedule():
    p = _project()
    c = _client(p)
    _teach(c)
    p.get_activity(activity_id="A1010").name = "Quality Walk"
    p.build_lookups()
    body = c.get("/api/brain/review").get_json()
    assert body["regrounded"]
    assert body["review"][0]["reason"] == "orphaned"


def test_keeping_a_rule_through_the_route_clears_it():
    c = _client()
    did = _teach(c)
    for _ in range(3):
        c.post("/api/brain/conflict", json={"directive_ids": [did], "overridden": True})
    body = c.post(f"/api/brain/{did}/keep").get_json()
    assert body["review"] == []


def test_keeping_a_rule_that_does_not_exist_is_a_404():
    c = _client()
    assert c.post("/api/brain/ghost/keep").status_code == 404


def test_an_unusable_conflict_payload_is_a_no_op():
    c = _client()
    for payload in ({}, {"directive_ids": "x"}, {"directive_ids": []}):
        resp = c.post("/api/brain/conflict", json=payload)
        assert resp.status_code == 200 and resp.get_json()["recorded"] == 0


def test_a_restored_project_is_regrounded_against_what_it_now_holds():
    """The counts were true when taught, not necessarily now."""
    from tests.test_cloud_persistence import _install_fake, _wipe_all_memory
    _install_fake()
    p = _project()
    c = _client(p)
    _teach(c)
    p.get_activity(activity_id="A1010").name = "Quality Walk"
    p.build_lookups()
    server._persist("t")
    _wipe_all_memory()
    server._restore_from_cloud()
    d = _live_brain().directives[0]
    assert d.kind == pb.NOTE, "a rule matching nothing still claimed to be one"
