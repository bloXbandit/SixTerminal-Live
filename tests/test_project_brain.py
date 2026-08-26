"""
test_project_brain.py — telling the agent how THIS job is being built.

The ranker guesses from names, dates and folders. A directive is not a guess:
somebody who has walked the job said it. So a stated rule has to actually
change which ties get proposed, has to be checkable against the schedule that
exists, has to stop a tie that runs backwards to it — and has to stay inside
its own project, because logic taught on the data centre must not follow the
user into the next job.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import project_brain as pb
from engine.logic_advisor import _Ctx, score_tie, tie_options
from engine.schedule_model import (Project, Activity, WBSNode, Relation,
                                   Calendar)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _proj(pid="25-1539-INT-1"):
    p = Project(uid="p", name="Data Centre", id=pid, data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities, p.relations = [], []
    return p


def _act(p, uid, name, start, finish, wbs="w", atype="Task Dependent"):
    a = Activity(uid=uid, activity_id=uid.upper(), name=name, wbs_uid=wbs,
                 calendar_uid="1", activity_type=atype, status="Not Started",
                 planned_duration=5.0, remaining_duration=5.0,
                 planned_start=start, planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rooms():
    """Terminations then QA/QC, in two rooms, plus an unrelated row between."""
    p = _proj()
    _act(p, "t5", "ER 105 Terminations", "2026-02-02", "2026-02-06")
    _act(p, "q5", "ER 105 QA/QC Inspections", "2026-02-09", "2026-02-13")
    _act(p, "t6", "ER 106 Terminations", "2026-02-16", "2026-02-20")
    _act(p, "q6", "ER 106 QA/QC Inspections", "2026-02-23", "2026-02-27")
    return p


# ── reading a sentence ────────────────────────────────────────────────────────

def test_an_after_sentence_becomes_an_enforceable_rule():
    d = pb.parse_directive("QA/QC inspections always follow terminations")
    assert d.kind == pb.ORDER
    assert "qa" in d.subject.lower() and "termination" in d.after.lower()


def test_a_before_sentence_reads_the_same_way_round():
    d = pb.parse_directive("Terminations must come before QA/QC inspections")
    assert d.kind == pb.ORDER
    assert "termination" in d.after.lower() and "qa" in d.subject.lower()


def test_a_same_room_qualifier_is_picked_up():
    d = pb.parse_directive("QA/QC follows terminations in the same room")
    assert d.kind == pb.ORDER and d.same_area is True


def test_a_sequence_sentence_becomes_a_sequence_rule():
    d = pb.parse_directive("ER rooms run sequential")
    assert d.kind == pb.SEQUENCE and d.family.upper() == "ER"


def test_anything_it_cannot_parse_is_kept_verbatim_as_guidance():
    text = "We are running two crews and the owner wants the CUP energised early"
    d = pb.parse_directive(text)
    assert d.kind == pb.NOTE and d.text == text


def test_the_original_wording_is_always_kept():
    text = "QA/QC inspections always follow terminations"
    assert pb.parse_directive(text).text == text


def test_what_was_understood_is_stated_back():
    d = pb.parse_directive("QA/QC follows terminations in the same room")
    said = pb.describe(d)
    assert "after" in said and "same area" in said


def test_a_sentence_naming_the_same_thing_twice_is_not_a_rule():
    assert pb.parse_directive("terminations after terminations").kind == pb.NOTE


# ── the digit is the whole difference ─────────────────────────────────────────

def test_the_digit_is_kept_because_it_is_the_whole_difference():
    """
    "Level 3 Commissioning" and "Level 4 Commissioning" differ only in the
    digit. Dropping short words would make the two phrases identical, and a
    rule about L4 would silently match L3 milestones.
    """
    assert pb.phrase_matches("Level 3 Commissioning", "Level 3 Commissioning Start (PH1)")
    assert not pb.phrase_matches("Level 4 Commissioning", "Level 3 Commissioning Start (PH1)")


def test_a_digit_matches_a_whole_number_not_part_of_one():
    assert not pb.phrase_matches("Level 4 Commissioning", "Level 40 Commissioning Suite")
    assert not pb.phrase_matches("Panel 4", "Panel 400A Installation")


# ── phase scoping ─────────────────────────────────────────────────────────────

def test_in_the_same_phase_is_understood():
    d = _d("Level 4 commissioning follows Level 3 commissioning in the same phase")
    assert d.kind == pb.ORDER and d.same_phase is True
    assert "same phase" in pb.describe(d)


def test_a_phase_rule_supports_within_and_ignores_across():
    d = _d("Level 4 commissioning follows Level 3 commissioning in the same phase")
    assert pb.directive_verdict(d, "Level 3 Commissioning Finish (PH1)",
                                "Level 4 Commissioning Start (PH1)") == "supports"
    assert pb.directive_verdict(d, "Level 3 Commissioning Finish (PH1)",
                                "Level 4 Commissioning Start (PH2)") is None


def test_a_phase_rule_still_catches_the_backwards_tie_in_its_phase():
    d = _d("Level 4 commissioning follows Level 3 commissioning in the same phase")
    assert pb.directive_verdict(d, "Level 4 Commissioning Start (PH2)",
                                "Level 3 Commissioning Finish (PH2)") == "violates"


def test_the_phase_can_come_from_the_folder_not_the_name():
    d = _d("QA/QC follows terminations in the same phase")
    assert pb.directive_verdict(d, "Terminations", "QA/QC Inspections",
                                "Phase 1 (Build-Out) / MV Rooms",
                                "Phase 1 (Build-Out) / MV Rooms") == "supports"
    assert pb.directive_verdict(d, "Terminations", "QA/QC Inspections",
                                "Phase 1 (Build-Out) / MV Rooms",
                                "Phase 2 (Build-Out) / MV Rooms") is None


# ── the room lives in the folder ──────────────────────────────────────────────

def test_a_same_room_rule_reads_the_room_off_the_folder():
    """On this job "Pull Wire" says nothing — "MV Rooms / MV 105" says it all."""
    d = _d("QA/QC follows terminations in the same room")
    assert pb.directive_verdict(d, "Terminations", "QA/QC Inspections",
                                "P1 / MV Rooms / MV 105",
                                "P1 / MV Rooms / MV 105") == "supports"
    assert pb.directive_verdict(d, "Terminations", "QA/QC Inspections",
                                "P1 / MV Rooms / MV 105",
                                "P1 / MV Rooms / MV 106") is None


def test_a_sequence_family_reads_its_number_off_the_folder():
    d = _d("MV rooms run sequential")
    assert pb.directive_verdict(d, "Pull Wire", "Pull Wire",
                                "P1 / MV Rooms / MV 105",
                                "P1 / MV Rooms / MV 106") == "supports"
    assert pb.directive_verdict(d, "Pull Wire", "Pull Wire",
                                "P1 / MV Rooms / MV 106",
                                "P1 / MV Rooms / MV 105") == "violates"


def test_grounding_a_sequence_counts_the_folders_too():
    p = _proj()
    p.wbs_nodes += [WBSNode(uid="m5", name="MV 105", code="M5", parent_uid="w"),
                    WBSNode(uid="m6", name="MV 106", code="M6", parent_uid="w")]
    _act(p, "a1", "Pull Wire", "2026-02-02", "2026-02-06", wbs="m5")
    _act(p, "a2", "Pull Wire", "2026-02-09", "2026-02-13", wbs="m6")
    d = pb.ground(p, pb.parse_directive("MV rooms run sequential"))
    assert d.kind == pb.SEQUENCE and d.matched_subject == 2


# ── a rule has to name work that actually exists ──────────────────────────────

def test_a_rule_shaped_sentence_about_nothing_in_the_schedule_stays_guidance():
    """
    The parser reads shape, not meaning: "the owner wants the CUP energised
    before the data halls" comes out looking like an ordering rule. Showing it
    as enforced would be a lie — neither side names an activity.
    """
    d = pb.ground(_rooms(),
                  pb.parse_directive("the owner wants the CUP energised before the data halls"))
    assert d.kind == pb.NOTE
    assert "nothing in this schedule is called" in d.note_reason


def test_a_rule_that_does_name_real_work_stays_a_rule_and_counts_it():
    d = pb.ground(_rooms(), pb.parse_directive("QA/QC inspections follow terminations"))
    assert d.kind == pb.ORDER
    assert d.matched_after == 2 and d.matched_subject == 2
    assert "2 ↔ 2 activities" in pb.describe(d)


def test_half_a_rule_is_no_rule():
    d = pb.ground(_rooms(), pb.parse_directive("Energization follows terminations"))
    assert d.kind == pb.NOTE and "Energization" in d.note_reason


def test_a_sequence_over_a_family_that_is_not_there_stays_guidance():
    d = pb.ground(_rooms(), pb.parse_directive("MV rooms run sequential"))
    assert d.kind == pb.NOTE and "MV" in d.note_reason


def test_the_reason_it_was_not_enforced_is_said_out_loud():
    d = pb.ground(_rooms(), pb.parse_directive("Widgets follow gizmos"))
    assert "Guidance only" in pb.describe(d)


# ── matching a directive to activities ────────────────────────────────────────

def test_every_word_of_the_phrase_has_to_appear():
    assert pb.phrase_matches("QA/QC inspections",
                             "CWP-CUP-01 - QA/QC Inspections and Checklists")
    assert not pb.phrase_matches("QA/QC inspections", "QA Manager Walkthrough")


def test_the_family_number_is_read_out_of_the_name():
    assert pb.family_index("ER", "ER 105 Terminations") == 105
    assert pb.family_index("ER", "ER-Room 12 Pull Wire") == 12
    assert pb.family_index("ER", "MV 105 Terminations") is None


# ── what a directive says about one candidate tie ─────────────────────────────

def _d(text):
    return pb.parse_directive(text)


def test_the_right_way_round_is_supported():
    d = _d("QA/QC inspections follow terminations")
    assert pb.directive_verdict(d, "ER 105 Terminations",
                                "ER 105 QA/QC Inspections") == "supports"


def test_the_wrong_way_round_is_a_violation():
    d = _d("QA/QC inspections follow terminations")
    assert pb.directive_verdict(d, "ER 105 QA/QC Inspections",
                                "ER 105 Terminations") == "violates"


def test_a_pair_the_rule_says_nothing_about_is_left_alone():
    d = _d("QA/QC inspections follow terminations")
    assert pb.directive_verdict(d, "Pour Slab Area 1", "Set Steel Area 1") is None


def test_a_same_room_rule_does_not_reach_across_rooms():
    d = _d("QA/QC follows terminations in the same room")
    assert pb.directive_verdict(d, "ER 105 Terminations",
                                "ER 106 QA/QC Inspections") is None


def test_a_sequence_rule_supports_the_next_room_and_rejects_going_back():
    d = _d("ER rooms run sequential")
    assert pb.directive_verdict(d, "ER 105 Pull Wire", "ER 106 Pull Wire") == "supports"
    assert pb.directive_verdict(d, "ER 106 Pull Wire", "ER 105 Pull Wire") == "violates"


def test_a_sequence_rule_is_about_the_same_work_in_the_next_room():
    """
    Read loosely, "ER rooms run sequential" endorses every pair of activities
    across two rooms — thousands of ties on a 30-room job. It means room N's
    pull is followed by room N+1's pull, and nothing more.
    """
    d = _d("ER rooms run sequential")
    assert pb.directive_verdict(d, "ER 105 Terminations", "ER 106 Pull Wire") is None


def test_a_sequence_rule_says_nothing_about_rooms_further_down():
    d = _d("ER rooms run sequential")
    assert pb.directive_verdict(d, "ER 105 Pull Wire", "ER 108 Pull Wire") is None


def test_a_directive_switched_off_says_nothing():
    d = _d("QA/QC inspections follow terminations")
    d.enabled = False
    assert pb.directive_verdict(d, "ER 105 Terminations",
                                "ER 105 QA/QC Inspections") is None


# ── it actually changes the ranking ───────────────────────────────────────────

def _score(p, pred, succ, directives=None):
    ctx = _Ctx(p, directives)
    a = p.get_activity(activity_id=pred)
    b = p.get_activity(activity_id=succ)
    from engine.logic_advisor import implied_lag
    return score_tie(ctx, a, b, implied_lag(p, a, b))


def test_a_stated_rule_is_named_in_the_reasons_for_the_tie_it_asks_for():
    p = _rooms()
    _, why = _score(p, "T5", "Q5", [_d("QA/QC inspections follow terminations")])
    assert any("you said" in w for w in why)


def test_a_stated_rule_lifts_the_tie_it_asks_for():
    """
    Measured across two rooms on purpose. Same-room ER 105 Terminations ->
    ER 105 QA/QC already scores a flat 1.0 on its own merits — dates, subject,
    area, folder and trade order all agree — so there is no headroom left for
    a rule to add anything, and "the rule lifted it" cannot be observed there.
    """
    p = _rooms()
    plain, _ = _score(p, "T5", "Q6")
    told, why = _score(p, "T5", "Q6", [_d("QA/QC inspections follow terminations")])
    assert told > plain
    assert any("you said" in w for w in why)


def test_a_stated_rule_survives_a_gap_that_would_otherwise_sink_it():
    """The dates being wrong is the thing to REPORT, not a reason to bury it."""
    p = _proj()
    _act(p, "t1", "ER 105 Terminations", "2026-02-02", "2026-02-06")
    _act(p, "q1", "ER 105 QA/QC Inspections", "2026-06-01", "2026-06-05")
    plain, _ = _score(p, "T1", "Q1")
    told, _ = _score(p, "T1", "Q1", [_d("QA/QC inspections follow terminations")])
    assert plain < 0.30 <= told


def test_a_tie_that_runs_backwards_to_a_rule_scores_zero():
    p = _rooms()
    c, why = _score(p, "Q5", "T6", [_d("QA/QC inspections follow terminations")])
    assert c == 0.0
    assert any("contradicts" in w for w in why)


def test_with_nothing_said_the_ranking_is_exactly_as_before():
    p = _rooms()
    assert _score(p, "T5", "Q5")[0] == _score(p, "T5", "Q5", [])[0]


def test_the_options_offered_put_the_stated_tie_on_top():
    p = _proj()
    _act(p, "t1", "ER 105 Terminations", "2026-02-02", "2026-02-06")
    _act(p, "x1", "Deliver Bollards Area 4", "2026-02-03", "2026-02-06")
    q = _act(p, "q1", "ER 105 QA/QC Inspections", "2026-02-09", "2026-02-13")
    opts = tie_options(p, q, directives=[_d("QA/QC inspections follow terminations")])
    assert opts["predecessors"][0]["predecessor_id"] == "T1"


# ── checking the schedule against what was said ───────────────────────────────

def test_a_relationship_running_the_wrong_way_is_found():
    p = _rooms()
    p.relations.append(Relation(uid="r1", predecessor_uid="q5",
                                successor_uid="t5", type="Finish to Start", lag=0.0))
    out = pb.check(p, [_d("QA/QC inspections follow terminations")])
    assert out["violations"]
    v = out["violations"][0]
    assert v["kind"] == "relationship"
    assert v["predecessor_id"] == "Q5" and v["successor_id"] == "T5"


def test_dates_in_the_wrong_order_are_found_even_with_no_tie():
    p = _proj()
    _act(p, "t1", "ER 105 Terminations", "2026-03-02", "2026-03-06")
    _act(p, "q1", "ER 105 QA/QC Inspections", "2026-02-02", "2026-02-06")
    out = pb.check(p, [_d("QA/QC inspections follow terminations")])
    assert any(v["kind"] == "dates" for v in out["violations"])


def test_a_schedule_that_obeys_the_rule_reports_nothing():
    p = _rooms()
    p.relations.append(Relation(uid="r1", predecessor_uid="t5",
                                successor_uid="q5", type="Finish to Start", lag=0.0))
    assert pb.check(p, [_d("QA/QC inspections follow terminations")])["violations"] == []


def test_each_room_is_judged_against_its_own_room():
    """
    ER 105's inspection legitimately runs before ER 106 is even wired. Judging
    every QA/QC against every Terminations anywhere would flag a schedule that
    is entirely correct.
    """
    p = _proj()
    _act(p, "t5", "ER 105 Terminations", "2026-02-02", "2026-02-06")
    _act(p, "q5", "ER 105 QA/QC Inspections", "2026-02-09", "2026-02-13")
    _act(p, "t6", "ER 106 Terminations", "2026-03-02", "2026-03-06")
    _act(p, "q6", "ER 106 QA/QC Inspections", "2026-02-09", "2026-02-13")
    out = pb.check(p, [_d("QA/QC inspections follow terminations")])
    ids = {v["successor_id"] for v in out["violations"]}
    assert ids == {"Q6"}            # ER 106 is genuinely out of order; ER 105 is not


def test_a_rule_naming_one_off_work_still_applies_across_the_job():
    """Nothing shares a room here, so the rule is read as the whole schedule."""
    p = _proj()
    _act(p, "a1", "Substation Energization", "2026-02-02", "2026-02-06")
    _act(p, "a2", "Utility Service Complete", "2026-03-02", "2026-03-06")
    out = pb.check(p, [_d("Substation energization follows utility service complete")])
    assert out["violations"] and out["violations"][0]["successor_id"] == "A1"


def test_notes_are_never_checked_against_anything():
    p = _rooms()
    out = pb.check(p, [_d("The owner wants the CUP energised early")])
    assert out["rules"] == 0 and out["violations"] == []


def test_the_report_is_capped_so_a_bad_rule_cannot_flood_it():
    p = _proj()
    for i in range(60):
        _act(p, f"t{i}", f"ER {i} Terminations", "2026-03-02", "2026-03-06")
        _act(p, f"q{i}", f"ER {i} QA/QC Inspections", "2026-02-02", "2026-02-06")
    out = pb.check(p, [_d("QA/QC inspections follow terminations")], limit=10)
    assert len(out["violations"]) == 10 and out.get("truncated")


# ── the store ─────────────────────────────────────────────────────────────────

def test_a_job_is_known_by_its_p6_id_not_its_filename():
    assert pb.project_key(_proj("25-1539-INT-1")) == "25-1539-INT-1"


def test_a_project_with_no_id_still_gets_a_stable_key():
    p = _proj(pid="")
    assert pb.project_key(p) == "Data Centre"


def test_rules_and_notes_are_kept_apart():
    b = pb.Brain("k")
    b.add("QA/QC inspections follow terminations")
    b.add("Two crews on nights through February")
    assert len(b.rules) == 1 and len(b.notes) == 1


def test_it_survives_a_round_trip_through_storage():
    b = pb.Brain("k")
    d = b.add("QA/QC inspections follow terminations")
    back = pb.Brain.from_json(b.to_json())
    assert back.key == "k"
    assert back.directives[0].id == d.id
    assert back.directives[0].kind == pb.ORDER


def test_what_the_agent_is_told_names_both_the_rule_and_what_it_means():
    """An enforced rule and a general remark carry different authority, so
    they must not arrive in one undifferentiated pile."""
    b = pb.Brain("k")
    b.add("QA/QC inspections follow terminations")
    b.add("Owner wants the CUP energised early")
    block = b.context_block()
    assert "ENFORCED" in block and "nothing enforced" in block
    assert "QA/QC inspections follow terminations" in block
    assert "Owner wants the CUP energised early" in block
    # and on the right sides of the divide
    rules_part, _, notes_part = block.partition("WHAT YOU KNOW ABOUT THIS JOB")
    assert "QA/QC inspections follow terminations" in rules_part
    assert "Owner wants the CUP energised early" in notes_part


def test_an_untaught_project_adds_nothing_to_the_prompt():
    assert pb.Brain("k").context_block() == ""


# ── through the API ───────────────────────────────────────────────────────────

def _client(project=None, pid="t"):
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session(pid, f"{pid}.xml")
    sess["project"] = project or _rooms()
    server._projects[pid] = sess
    server._active_id[0] = pid
    return server.app.test_client()


def test_teaching_it_reports_what_it_understood():
    c = _client()
    body = c.post("/api/brain",
                  json={"text": "QA/QC inspections follow terminations"}).get_json()
    assert body["success"]
    assert body["directive"]["kind"] == pb.ORDER
    assert "after" in body["directive"]["understood"]
    assert body["rule_count"] == 1


def test_a_sentence_it_could_not_parse_says_so_plainly():
    c = _client()
    body = c.post("/api/brain", json={"text": "Two crews on nights through February"}).get_json()
    assert body["directive"]["kind"] == pb.NOTE
    assert "nothing enforced" in body["directive"]["understood"].lower()


def test_a_rule_about_work_that_is_not_here_is_reported_as_guidance():
    c = _client()
    body = c.post("/api/brain",
                  json={"text": "Owner wants the CUP energised before the data halls"}).get_json()
    assert body["directive"]["kind"] == pb.NOTE
    assert body["rule_count"] == 0
    assert "nothing in this schedule is called" in body["directive"]["understood"]


def test_what_was_taught_reads_back():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    assert len(c.get("/api/brain").get_json()["directives"]) == 1


def test_it_can_be_switched_off_without_being_lost():
    c = _client()
    did = c.post("/api/brain", json={"text": "ER rooms run sequential"}
                 ).get_json()["directive"]["id"]
    body = c.post(f"/api/brain/{did}/toggle", json={"enabled": False}).get_json()
    assert body["enabled"] is False and body["rule_count"] == 0
    assert len(body["directives"]) == 1


def test_it_can_be_deleted():
    c = _client()
    did = c.post("/api/brain", json={"text": "ER rooms run sequential"}
                 ).get_json()["directive"]["id"]
    assert c.delete(f"/api/brain/{did}").get_json()["directives"] == []


def test_an_empty_sentence_is_refused():
    c = _client()
    assert c.post("/api/brain", json={"text": "   "}).status_code == 400


def test_the_check_endpoint_reports_where_the_schedule_breaks_it():
    p = _rooms()
    p.relations.append(Relation(uid="r1", predecessor_uid="q5",
                                successor_uid="t5", type="Finish to Start", lag=0.0))
    c = _client(p)
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    body = c.get("/api/brain/check").get_json()
    assert body["rules"] == 1 and body["violations"]


# ── isolation between jobs ────────────────────────────────────────────────────

def test_what_is_taught_on_one_job_does_not_follow_you_to_the_next():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})

    other = _rooms()
    other.id = "26-0002-OTHER"
    sess = server._make_session("other", "other.xml")
    sess["project"] = other
    server._projects["other"] = sess
    server._active_id[0] = "other"

    assert c.get("/api/brain").get_json()["directives"] == []


def test_the_same_job_re_uploaded_under_a_new_name_keeps_what_it_learnt():
    """The whole reason it is keyed on the P6 id and not the filename."""
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})

    again = _rooms()                       # same P6 id, different upload
    sess = server._make_session("test6_edited", "test6_edited.xml")
    sess["project"] = again
    server._projects["test6_edited"] = sess
    server._active_id[0] = "test6_edited"

    assert len(c.get("/api/brain").get_json()["directives"]) == 1


# ── stop, then override ───────────────────────────────────────────────────────

def _link(c, pred, succ, **extra):
    return c.post("/api/edit", json=dict(
        {"instruction": f"link {pred} to {succ}",
         "force_commands": [{"action": "add_relation", "predecessor_id": pred,
                             "successor_id": succ, "type": "Finish to Start"}]},
        **extra)).get_json()


def test_a_tie_against_a_stated_rule_is_stopped_and_explained():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    body = _link(c, "Q5", "T5")
    assert body["type"] == "brain_conflict"
    assert body["conflicts"][0]["successor_id"] == "T5"
    assert "opposite" in body["conflicts"][0]["why"]
    assert not server._projects["t"]["project"].relations


def test_the_stopped_tie_can_be_made_anyway():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    _link(c, "Q5", "T5")
    body = _link(c, "Q5", "T5", brain_override=True)
    assert body.get("success")
    assert len(server._projects["t"]["project"].relations) == 1


def test_a_tie_the_rule_agrees_with_goes_straight_through():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    assert _link(c, "T5", "Q5").get("success")


def test_a_rule_switched_off_stops_nothing():
    c = _client()
    did = c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"}
                 ).get_json()["directive"]["id"]
    c.post(f"/api/brain/{did}/toggle", json={"enabled": False})
    assert _link(c, "Q5", "T5").get("success")


def test_edits_that_are_not_ties_are_never_second_guessed():
    c = _client()
    c.post("/api/brain", json={"text": "QA/QC inspections follow terminations"})
    body = c.post("/api/edit", json={
        "instruction": "rename it",
        "force_commands": [{"action": "rename_activity", "activity_id": "Q5",
                            "new_name": "ER 105 QA/QC Inspections and Punch"}],
    }).get_json()
    assert body.get("success")


# ── a re-export of the same job keeps what it was taught ─────────────────────

def test_a_reexport_of_the_same_job_keeps_its_brain():
    """The whole point of keying on P6's project id: rev1.xml and
    rev2_updated.xml are one job, so the rules taught against the first are
    still there for the second. Keying on the filename would orphan them
    within a day."""
    from engine.project_brain import project_key

    class _P:
        def __init__(self, pid, name):
            self.id, self.name, self.uid = pid, name, "1"
            self.activities, self.wbs_nodes, self.relations = [], [], []

    rev1 = _P("25-1539-INT-1", "MDC1 Exhibit S")
    rev2 = _P("25-1539-INT-1", "MDC1 Exhibit S")      # re-exported, new file
    other = _P("99-OTHER-JOB", "Some Other Job")

    assert project_key(rev1) == project_key(rev2)
    assert project_key(other) != project_key(rev1)


def test_the_key_survives_a_project_rename():
    """P6's Id is what is keyed, so renaming the project in P6 does not
    orphan the brain — only changing the Id itself would."""
    from engine.project_brain import project_key

    class _P:
        def __init__(self, pid, name):
            self.id, self.name, self.uid = pid, name, "1"

    assert project_key(_P("25-1539-INT-1", "MDC1 Exhibit S")) == \
           project_key(_P("25-1539-INT-1", "MDC1 — Rev 4 FINAL"))


def test_regrounding_retests_rules_against_the_new_schedule():
    """A rule's match count was true when it was taught. A re-upload is
    precisely when the schedule has moved on, so the counts must be redone
    or a rule goes on claiming matches against activities that are gone."""
    p = _proj()
    _act(p, "g1", "Set Generator", "2026-02-02", "2026-02-06")
    _act(p, "w1", "Pull Wire", "2026-02-09", "2026-02-13")
    b = pb.Brain("k")
    d = b.add("Set Generator before Pull Wire", p)
    before = (d.kind, d.matched_after, d.matched_subject)
    # strip the schedule back to nothing and reground
    p.activities = []
    p.build_lookups()
    b.reground(p)
    after = (d.kind, d.matched_after, d.matched_subject)
    assert after != before, "the rule still claims matches that no longer exist"


# ── the agent can report what it actually knows ──────────────────────────────
#
# The block riding in the prompt is capped at 30 a section and ends with
# "…and N more (ask to see them all)" — but nothing could answer that, and
# the parts deliberately kept OUT of the prompt (how proposals landed, what
# documents exist) were invisible however the user asked. describe_brain is
# read-only and reports the lot, off the real objects.

def _brain_project():
    p = _proj()
    _act(p, "g1", "Set Generator 318", "2026-02-02", "2026-02-06")
    _act(p, "w1", "Pull Wire MV 318", "2026-02-09", "2026-02-13")
    return p


def _with_brain(brain):
    from engine import edit_engine as ee
    ee.set_brain_lookup(lambda proj: brain)
    return ee


def test_describe_brain_changes_nothing():
    from engine import edit_engine as ee
    assert ee.is_advisory("describe_brain"), (
        "it must be counted as a report, or the turn claims edits it never made")


def test_it_reports_the_real_rules_not_a_sample():
    p = _brain_project()
    b = pb.Brain("k")
    b.add("Set Generator 318 before Pull Wire MV 318", p)
    ee = _with_brain(b)
    ok, msg = ee.apply_command(p, {"action": "describe_brain"})
    assert ok
    assert "Set Generator 318 before Pull Wire MV 318" in msg


def test_it_reports_past_every_prompt_cap():
    """The exact thing the capped block says to ask for."""
    p = _brain_project()
    b = pb.Brain("k")
    for i in range(pb.Brain._CAP + 12):
        b.add(f"Note number {i} about this job", p)
    ee = _with_brain(b)
    _, msg = ee.apply_command(p, {"action": "describe_brain"})
    assert f"Note number {pb.Brain._CAP + 11} about this job" in msg, (
        "it stopped at the prompt cap — the one thing this action exists to do")
    assert "ask to see them all" not in msg


def test_it_surfaces_what_the_prompt_never_carries():
    p = _brain_project()
    b = pb.Brain("k")
    b.add("Set Generator 318 before Pull Wire MV 318", p)
    b.feedback = {"term->qaqc": {"accepted": 7, "declined": 1}}
    ee = _with_brain(b)
    _, msg = ee.apply_command(p, {"action": "describe_brain"})
    assert "7 accepted" in msg and "1 declined" in msg


def test_an_untaught_job_says_so_plainly():
    """Rather than an empty heading the agent would pad into a fake summary."""
    p = _brain_project()
    ee = _with_brain(pb.Brain("fresh"))
    ok, msg = ee.apply_command(p, {"action": "describe_brain"})
    assert ok and "Nothing has been taught" in msg


def test_enforced_and_unenforced_are_not_mixed_together():
    """A note quoted as a binding rule is the failure this separation exists
    to prevent."""
    p = _brain_project()
    b = pb.Brain("k")
    b.add("Set Generator 318 before Pull Wire MV 318", p)   # grounds -> rule
    b.add("The GC controls the loading dock on Fridays", p)  # nothing to match
    ee = _with_brain(b)
    _, msg = ee.apply_command(p, {"action": "describe_brain"})
    rules_at = msg.index("RULES — ENFORCED")
    ctx_at = msg.index("CONTEXT — not enforced")
    assert msg.index("Set Generator 318 before") > rules_at
    assert msg.index("loading dock") > ctx_at
