"""
test_outcome_feedback.py — the tool learning from what you actually accept.

Confirming a rule was the only way knowledge entered the brain, and it costs
the user a sentence. But every Apply and every dismiss is already a judgement
about a proposed tie, and all of them were thrown away: propose the same wrong
tie forty times, decline it forty times, and nothing changed.

What is defended here: what is learned is the SHAPE of the tie with the room
number removed, so a lesson from MV 105 carries into MV 106 — anything narrower
never generalises past the row it came from. And the nudge is deliberately
weak and bounded: it breaks ties between candidates the evidence already likes,
and must never overrule a stated rule or manufacture a handoff out of a pair
with nothing else going for it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import project_brain as pb
from engine.logic_advisor import _Ctx, score_tie, implied_lag
from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation)


def _project():
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Electrical", code="E")]
    p.activities = []
    n = 0
    for room in (105, 106):
        uid = f"w{room}"
        p.wbs_nodes.append(WBSNode(uid=uid, name=f"MV {room}", code=f"M{room}",
                                   parent_uid="root"))
        for work, start, fin in (("Pull Wire", "2026-02-02", "2026-02-06"),
                                 ("Terminations", "2026-02-09", "2026-02-13")):
            n += 1
            p.activities.append(Activity(
                uid=f"a{n}", activity_id=f"A{1000 + n * 10}", name=work,
                wbs_uid=uid, calendar_uid="1", activity_type="Task Dependent",
                status="Not Started", planned_duration=40.0,
                remaining_duration=40.0, planned_start=start, planned_finish=fin))
    p.build_lookups()
    return p


def _acts(p, room, work):
    node = next(w for w in p.wbs_nodes if w.name == f"MV {room}")
    return next(a for a in p.activities if a.wbs_uid == node.uid and a.name == work)


def _score(p, pred, succ, feedback=None, directives=None):
    ctx = _Ctx(p, directives, feedback)
    return score_tie(ctx, pred, succ, implied_lag(p, pred, succ))


# ── the shape, not the pair ──────────────────────────────────────────────────

def test_the_room_number_is_not_part_of_what_is_learned():
    """The whole point: a lesson from MV 105 has to apply in MV 106."""
    assert (pb.tie_signature("Pull Wire MV 105", "Terminations MV 105")
            == pb.tie_signature("Pull Wire MV 106", "Terminations MV 106"))


def test_different_work_is_a_different_shape():
    assert (pb.tie_signature("Pull Wire", "Terminations")
            != pb.tie_signature("Pull Wire", "QA/QC Inspections"))


def test_the_direction_is_part_of_the_shape():
    assert (pb.tie_signature("Pull Wire", "Terminations")
            != pb.tie_signature("Terminations", "Pull Wire"))


def test_word_order_and_case_do_not_change_the_shape():
    assert (pb.tie_signature("Pull Wire", "Terminations")
            == pb.tie_signature("WIRE PULL", "terminations"))


# ── one observation is an accident ───────────────────────────────────────────

def test_a_single_click_changes_nothing():
    """A misclick must not re-rank the schedule."""
    b = pb.Brain("k")
    b.record("Pull Wire", "Terminations", accepted=True)
    assert pb.feedback_score(b.feedback, "Pull Wire", "Terminations") == 0.0


def test_two_of_the_same_start_to_count():
    b = pb.Brain("k")
    b.record("Pull Wire", "Terminations", accepted=True)
    b.record("Pull Wire", "Terminations", accepted=True)
    assert pb.feedback_score(b.feedback, "Pull Wire", "Terminations") > 0


def test_declines_push_the_other_way():
    b = pb.Brain("k")
    for _ in range(3):
        b.record("Final Floor Finishes", "Complete Construction", accepted=False)
    assert pb.feedback_score(b.feedback,
                             "Final Floor Finishes", "Complete Construction") < 0


def test_accepting_one_direction_counts_against_the_reverse():
    """Accepting Pull Wire -> Terminations is also evidence that
    Terminations -> Pull Wire is wrong."""
    b = pb.Brain("k")
    for _ in range(3):
        b.record("Pull Wire", "Terminations", accepted=True)
    assert pb.feedback_score(b.feedback, "Terminations", "Pull Wire") < 0


def test_the_nudge_is_capped_however_many_times_it_is_clicked():
    b = pb.Brain("k")
    for _ in range(500):
        b.record("Pull Wire", "Terminations", accepted=True)
    assert pb.feedback_score(b.feedback, "Pull Wire", "Terminations") <= 0.25


def test_a_shape_never_seen_says_nothing():
    b = pb.Brain("k")
    b.record("Pull Wire", "Terminations", accepted=True)
    b.record("Pull Wire", "Terminations", accepted=True)
    assert pb.feedback_score(b.feedback, "Excavate", "Backfill") == 0.0


# ── it reaches the ranking ───────────────────────────────────────────────────

def test_accepted_shapes_rank_higher_next_time():
    p = _project()
    pull105, term105 = _acts(p, 105, "Pull Wire"), _acts(p, 105, "Terminations")
    plain, _ = _score(p, pull105, term105)
    b = pb.Brain("k")
    for _ in range(3):
        b.record("Pull Wire", "Terminations", accepted=True)
    told, why = _score(p, pull105, term105, feedback=b.feedback)
    assert told >= plain
    assert any("usually accept" in w for w in why)


def test_a_lesson_from_one_room_carries_into_another():
    """Learned in MV 105, applied in MV 106 — which is the difference between
    this being useful and it being a per-row memo."""
    p = _project()
    b = pb.Brain("k")
    for _ in range(3):
        b.record("Pull Wire MV 105", "Terminations MV 105", accepted=True)
    pull106, term106 = _acts(p, 106, "Pull Wire"), _acts(p, 106, "Terminations")
    _, why = _score(p, pull106, term106, feedback=b.feedback)
    assert any("usually accept" in w for w in why)


def test_repeated_declines_sink_a_tie():
    """
    Measured on a pair with headroom. A cross-room pair already sits on the
    scoring floor — support is 0 and the floor is the floor — so a negative
    nudge has nothing left to take away there, and "declines sink it" cannot
    be observed on a tie that is already as low as ties go.
    """
    p = _project()
    pull105, term105 = _acts(p, 105, "Pull Wire"), _acts(p, 105, "Terminations")
    plain, _ = _score(p, pull105, term105)
    b = pb.Brain("k")
    for _ in range(4):
        b.record("Pull Wire", "Terminations", accepted=False)
    told, why = _score(p, pull105, term105, feedback=b.feedback)
    assert told < plain
    assert any("usually reject" in w for w in why)


def test_feedback_never_overrules_a_stated_rule():
    """A rule was stated by somebody who walked the job. Clicks are weaker
    evidence than that, and must stay weaker."""
    p = _project()
    pull105, term105 = _acts(p, 105, "Pull Wire"), _acts(p, 105, "Terminations")
    rule = pb.parse_directive("Pull Wire follows Terminations")   # deliberately backwards
    b = pb.Brain("k")
    for _ in range(50):
        b.record("Pull Wire", "Terminations", accepted=True)
    told, why = _score(p, pull105, term105, feedback=b.feedback, directives=[rule])
    assert told == 0.0
    assert any("contradicts what you said" in w for w in why)


def test_feedback_alone_cannot_invent_a_handoff():
    """Two rows with nothing else going for them must not be dragged over the
    bar by clicks."""
    p = _project()
    a = _acts(p, 105, "Pull Wire")
    b_act = _acts(p, 106, "Pull Wire")      # same work, different room, no gap
    b = pb.Brain("k")
    for _ in range(50):
        b.record("Pull Wire", "Pull Wire", accepted=True)
    told, _ = _score(p, a, b_act, feedback=b.feedback)
    assert told < 0.45


def test_a_project_nobody_has_clicked_ranks_exactly_as_before():
    p = _project()
    pull105, term105 = _acts(p, 105, "Pull Wire"), _acts(p, 105, "Terminations")
    assert _score(p, pull105, term105, feedback={}) == _score(p, pull105, term105)


# ── it persists ──────────────────────────────────────────────────────────────

def test_what_was_learned_survives_being_saved_and_reloaded():
    b = pb.Brain("k")
    for _ in range(3):
        b.record("Pull Wire", "Terminations", accepted=True)
    back = pb.Brain.from_json(b.to_json())
    assert pb.feedback_score(back.feedback, "Pull Wire", "Terminations") > 0


def test_feedback_alone_is_worth_saving():
    """A project with clicks but no taught rules still has knowledge in it."""
    b = pb.Brain("k")
    b.record("Pull Wire", "Terminations", accepted=True)
    assert not b.is_empty()


def test_it_is_never_sent_to_the_model():
    """A scoring input, not context — it must cost nothing per turn."""
    p = _project()
    b = pb.Brain("k")
    for _ in range(5):
        b.record("Pull Wire", "Terminations", accepted=True)
    assert b.context_block(p) == ""


# ── through the route ────────────────────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _brain():
    return server._brains[pb.project_key(server._projects["t"]["project"])]


def test_an_apply_is_recorded():
    c = _client()
    body = c.post("/api/feedback", json={
        "accepted": True,
        "ties": [{"predecessor_name": "Pull Wire", "successor_name": "Terminations"}],
    }).get_json()
    assert body["recorded"] == 1
    assert _brain().feedback[pb.tie_signature("Pull Wire", "Terminations")]["accepted"] == 1


def test_a_dismiss_is_recorded_as_a_decline():
    c = _client()
    c.post("/api/feedback", json={
        "accepted": False,
        "ties": [{"predecessor_name": "Pull Wire", "successor_name": "Terminations"}]})
    assert _brain().feedback[pb.tie_signature("Pull Wire", "Terminations")]["declined"] == 1


def test_ids_are_resolved_to_names_when_a_card_only_has_ids():
    c = _client()
    p = server._projects["t"]["project"]
    pull = _acts(p, 105, "Pull Wire")
    term = _acts(p, 105, "Terminations")
    body = c.post("/api/feedback", json={
        "accepted": True,
        "ties": [{"predecessor_id": pull.activity_id,
                  "successor_id": term.activity_id}]}).get_json()
    assert body["recorded"] == 1


def test_a_whole_batch_can_be_recorded_in_one_call():
    c = _client()
    body = c.post("/api/feedback", json={
        "accepted": True,
        "ties": [{"predecessor_name": "A", "successor_name": "B"},
                 {"predecessor_name": "C", "successor_name": "D"}]}).get_json()
    assert body["recorded"] == 2


def test_an_unusable_payload_is_a_no_op_not_an_error():
    """A click must never fail because of bookkeeping."""
    c = _client()
    for payload in ({}, {"ties": "nonsense"}, {"ties": [{"predecessor_name": "only"}]}):
        resp = c.post("/api/feedback", json=payload)
        assert resp.status_code == 200
        assert resp.get_json()["recorded"] == 0


def test_recording_with_nothing_loaded_does_not_blow_up():
    server._projects.clear()
    server._active_id[0] = None
    resp = server.app.test_client().post("/api/feedback", json={"accepted": True})
    assert resp.status_code == 200
