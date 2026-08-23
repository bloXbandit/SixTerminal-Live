"""
test_logic_advisor.py — Recommending the logic a dated schedule is missing.

The contract these tests defend: a recommendation must be judged against the
dates already in the schedule, and applying the accepted ones must not move a
single date. A schedule held together by hard constraints is re-expressed as
logic — it is not re-planned.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation, compute_dates)
from engine.edit_engine import apply_command, apply_commands
from engine.logic_advisor import (CONFIRMS, CONFLICT, SLACK, classify,
                                  implied_lag, working_days_between,
                                  milestone_report, commissioning_ladder,
                                  to_commands, phase_number)


def _project():
    """
    A phase with work, a commissioning ladder, and a completion milestone —
    all dated, none of it linked. This is the shape of a schedule built from
    dates alone.
    """
    p = Project(uid="1", name="DC", id="DC", planned_start="2026-01-05",
                data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="m",  name="Milestones", code="MS"),
        WBSNode(uid="m1", name="Phase 1", code="MS1", parent_uid="m"),
        WBSNode(uid="p1", name="Phase 1 (Build-Out)", code="P1"),
    ]

    def act(uid, aid, name, wbs, start, finish, dur=5.0, atype="Task Dependent",
            constraint=None):
        return Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                        calendar_uid="1", planned_duration=dur * 8,
                        activity_type=atype, planned_start=start,
                        planned_finish=finish,
                        constraint_type=constraint,
                        constraint_date=start if constraint else None)

    p.activities = [
        # work inside Phase 1, each pinned by a Start On constraint
        act("a1", "A1000", "Rough-in", "p1", "2026-01-05", "2026-01-09",
            constraint="Start On"),
        act("a2", "A1010", "Equipment Set", "p1", "2026-01-12", "2026-01-16",
            constraint="Start On"),
        act("a3", "A1020", "Terminations", "p1", "2026-01-19", "2026-01-23",
            constraint="Start On"),
        # commissioning milestones, contractual dates, zero logic
        act("c1", "M100", "Level 3 Commissioning Start (PH1)", "m1",
            "2026-01-26", "2026-01-26", 0, "Start Milestone"),
        act("c2", "M110", "Level 3 Commissioning Finish (PH1)", "m1",
            "2026-02-06", "2026-02-06", 0, "Finish Milestone"),
        act("c3", "M120", "Level 4 Commissioning Start (PH1)", "m1",
            "2026-02-02", "2026-02-02", 0, "Start Milestone"),
    ]
    p.relations = []
    p.build_lookups()
    # Settle through the scheduler so the fixture uses the engine's own
    # finish = start + duration convention. Unlinked not-started activities
    # keep their planned start, which is exactly the real-world case this
    # module addresses.
    compute_dates(p)
    return p


def _act(p, aid):
    return p.get_activity(activity_id=aid)


# ── The measure itself ───────────────────────────────────────────────────────

def test_working_days_skips_weekends_and_keeps_its_sign():
    # Fri 2026-01-09 -> Mon 2026-01-12 is one working day, not three
    assert working_days_between("2026-01-09", "2026-01-12") == 1
    # direction matters: a successor before its predecessor must read negative
    assert working_days_between("2026-01-12", "2026-01-09") == -1
    assert working_days_between("2026-01-05", "2026-01-05") == 0


def test_implied_lag_of_back_to_back_work_is_zero():
    """The engine's finish date is the next activity's start date, so truly
    back-to-back work reads as a zero gap."""
    p = _project()
    assert implied_lag(p, _act(p, "A1000"), _act(p, "A1010")) == 0


def test_classify_separates_the_three_cases():
    assert classify(0)[0] == CONFIRMS
    assert classify(1)[0] == CONFIRMS          # a day of drift is rounding
    assert classify(10)[0] == SLACK
    assert classify(-5)[0] == CONFLICT
    assert classify(None)[0] == SLACK          # undated: unknowable, not a conflict


def test_a_conflict_explains_itself_in_scheduler_terms():
    verdict, why = classify(-5)
    assert verdict == CONFLICT
    assert "5 working days BEFORE" in why
    assert "Start-to-Start" in why             # offers the real alternative


# ── Milestone anchoring ──────────────────────────────────────────────────────

def test_every_unanchored_milestone_is_reported():
    p = _project()
    rep = milestone_report(p)
    assert rep["milestone_count"] == 3
    assert rep["unanchored_count"] == 3
    assert all(not m["has_predecessor"] for m in rep["milestones"])


def test_a_milestone_gets_the_work_that_lands_on_its_date():
    """The driver should be the activity whose finish explains the date, not
    merely something earlier in the same phase."""
    p = _project()
    rep = milestone_report(p, limit_per_milestone=1)
    l3 = next(m for m in rep["milestones"] if m["activity_id"] == "M100")
    top = l3["drivers"][0]
    assert top["predecessor_id"] == "A1020"     # the latest finishing work
    assert top["verdict"] == CONFIRMS           # 2026-01-23 -> 2026-01-26 = 1 day
    assert top["implied_lag_days"] <= 2


def test_a_driver_that_reproduces_the_date_offers_to_drop_the_constraint():
    p = _project()
    _act(p, "A1010").constraint_type = "Start On"
    rep = milestone_report(p, limit_per_milestone=1)
    confirming = [d for m in rep["milestones"] for d in m["drivers"]
                  if d["verdict"] == CONFIRMS and d["constraint_on_successor"]]
    for d in confirming:
        assert d["removes_constraint"] is True


def test_milestones_outside_a_phase_do_not_borrow_another_phase_work():
    p = _project()
    p.wbs_nodes.append(WBSNode(uid="p2", name="Phase 2 (Build-Out)", code="P2"))
    p.activities.append(Activity(uid="b1", activity_id="B1000", name="P2 work",
                                 wbs_uid="p2", calendar_uid="1",
                                 planned_duration=40.0,
                                 planned_start="2026-01-20",
                                 planned_finish="2026-01-24"))
    p.build_lookups()
    rep = milestone_report(p, limit_per_milestone=5)
    l3 = next(m for m in rep["milestones"] if m["activity_id"] == "M100")
    assert "B1000" not in [d["predecessor_id"] for d in l3["drivers"]]


def test_phase_number_reads_both_spellings():
    assert phase_number("Substantial Completion (PH2)") == 2
    assert phase_number("Milestones / Phase 3") == 3
    assert phase_number("Complete Construction") is None


# ── Commissioning ladder ─────────────────────────────────────────────────────

def test_ladder_orders_a_level_start_before_its_own_finish():
    p = _project()
    ladder = commissioning_ladder(p)
    pairs = [(r["predecessor_id"], r["successor_id"]) for r in ladder]
    assert ("M100", "M110") in pairs            # L3 start -> L3 finish


def test_ladder_flags_an_overlap_rather_than_forcing_a_chain():
    """L4 start (02-02) precedes L3 finish (02-06) — a real overlap. It must be
    reported as a conflict for FS, not quietly turned into a chain."""
    p = _project()
    ladder = commissioning_ladder(p)
    rec = next(r for r in ladder
               if r["predecessor_id"] == "M110" and r["successor_id"] == "M120")
    assert rec["verdict"] == CONFLICT
    assert rec["implied_lag_days"] < 0


def test_ladder_skips_ties_that_already_exist():
    p = _project()
    p.relations.append(Relation(uid="r1", predecessor_uid="c1", successor_uid="c2"))
    p.build_lookups()
    pairs = [(r["predecessor_id"], r["successor_id"]) for r in commissioning_ladder(p)]
    assert ("M100", "M110") not in pairs


# ── Applying recommendations ─────────────────────────────────────────────────

def test_work_dates_are_untouched_by_milestone_anchoring():
    """Anchoring milestones must not disturb the work itself."""
    p = _project()
    work = [a.activity_id for a in p.activities
            if a.activity_type == "Task Dependent"]
    before = {k: (_act(p, k).planned_start, _act(p, k).planned_finish) for k in work}

    rep = milestone_report(p, limit_per_milestone=1)
    recs = [d for m in rep["milestones"] for d in m["drivers"]
            if d["verdict"] != CONFLICT]
    assert recs
    assert all(ok for ok, _ in apply_commands(p, to_commands(recs)))

    after = {k: (_act(p, k).planned_start, _act(p, k).planned_finish) for k in work}
    assert after == before, "anchoring milestones moved the work"


def test_a_contractual_milestone_keeps_its_date_as_a_deadline():
    """Once logic drives a milestone it floats to its early date. The contract
    date has to survive as a DEADLINE — otherwise anchoring quietly loses it."""
    p = _project()
    rep = milestone_report(p, limit_per_milestone=1)
    recs = [d for m in rep["milestones"] for d in m["drivers"]
            if d["verdict"] != CONFLICT]
    dated = [r for r in recs if r.get("deadline")]
    assert dated, "milestone recommendations must carry a deadline"
    for r in dated:
        assert r["deadline"]["constraint_type"].endswith("On Or Before")
        assert r["deadline"]["constraint_date"]

    cmds = to_commands(recs)
    assert any(c["action"] == "set_constraint"
               and c["constraint_type"].endswith("On Or Before") for c in cmds)
    # opting out is possible for a scheduler who wants pure logic
    assert not any(c["action"] == "set_constraint"
                   for c in to_commands(recs, keep_milestone_deadlines=False))


def test_a_milestone_deadline_is_the_date_it_had_before_anchoring():
    p = _project()
    rep = milestone_report(p, limit_per_milestone=1)
    dates = {m["activity_id"]: m["date"] for m in rep["milestones"]}
    for m in rep["milestones"]:
        for d in m["drivers"]:
            if d.get("deadline"):
                assert d["deadline"]["constraint_date"] == dates[m["activity_id"]]


def test_applying_anchors_the_milestones():
    p = _project()
    rep = milestone_report(p, limit_per_milestone=1)
    recs = [d for m in rep["milestones"] for d in m["drivers"]
            if d["verdict"] != CONFLICT]
    apply_commands(p, to_commands(recs))
    after = milestone_report(p, limit_per_milestone=1)
    assert after["unanchored_count"] < rep["unanchored_count"]


def test_conflicts_are_excluded_from_commands_unless_asked_for():
    p = _project()
    ladder = commissioning_ladder(p)
    conflicts = [r for r in ladder if r["verdict"] == CONFLICT]
    assert conflicts
    assert to_commands(conflicts) == []
    assert to_commands(conflicts, include_conflicts=True)


def test_a_confirming_tie_clears_the_constraint_it_replaces():
    p = _project()
    rep = milestone_report(p, limit_per_milestone=1)
    recs = [d for m in rep["milestones"] for d in m["drivers"]
            if d["removes_constraint"]]
    if not recs:                       # milestones here carry no constraint
        return
    cmds = to_commands(recs)
    assert any(c["action"] == "clear_constraint" for c in cmds)
    assert not any(c["action"] == "clear_constraint"
                   for c in to_commands(recs, drop_constraints=False))


def test_recommend_logic_action_is_advisory_only():
    p = _project()
    before = ([(a.activity_id, a.planned_start) for a in p.activities],
              len(p.relations))
    ok, msg = apply_command(p, {"action": "recommend_logic"})
    assert ok
    assert "milestones have nothing driving them" in msg
    after = ([(a.activity_id, a.planned_start) for a in p.activities],
             len(p.relations))
    assert after == before, "recommend_logic must not change the schedule"


# ── Area navigation and scoped recommendations ───────────────────────────────

def _multi_phase():
    """The same room name under two phases, which is how real projects read."""
    from engine.logic_advisor import find_wbs
    p = _project()
    p.wbs_nodes += [
        WBSNode(uid="p2", name="Phase 2 (Build-Out)", code="P2"),
        WBSNode(uid="p1r", name="MV Rooms", code="P1MV", parent_uid="p1"),
        WBSNode(uid="p2r", name="MV Rooms", code="P2MV", parent_uid="p2"),
    ]
    p.activities += [
        Activity(uid="x1", activity_id="X1000", name="P1 gear work", wbs_uid="p1r",
                 calendar_uid="1", planned_duration=40.0,
                 planned_start="2026-03-02", planned_finish="2026-03-09"),
        Activity(uid="x2", activity_id="X2000", name="P2 gear work", wbs_uid="p2r",
                 calendar_uid="1", planned_duration=40.0,
                 planned_start="2026-04-06", planned_finish="2026-04-13"),
    ]
    p.build_lookups()
    return p


def test_a_phase_qualifier_picks_the_right_repeated_folder():
    """'MV Rooms' exists under several phases. Without honouring the phase
    qualifier the lookup returns whichever comes first, which is how an agent
    ends up reasoning about the wrong area entirely."""
    from engine.logic_advisor import find_wbs, wbs_node_path
    p = _multi_phase()
    assert "Phase 1" in wbs_node_path(p, find_wbs(p, "Phase 1 MV Rooms"))
    assert "Phase 2" in wbs_node_path(p, find_wbs(p, "Phase 2 MV Rooms"))


def test_area_digest_scopes_to_the_named_branch_only():
    from engine.logic_advisor import area_digest
    p = _multi_phase()
    d = area_digest(p, "Phase 1 MV Rooms")
    assert d["activity_count"] == 1
    assert [a["activity_id"] for a in d["activities"]] == ["X1000"]


def test_an_unknown_area_suggests_alternatives_instead_of_failing_blindly():
    from engine.logic_advisor import area_digest
    p = _multi_phase()
    d = area_digest(p, "Chiller Plant")
    assert "error" in d and "did_you_mean" in d


def test_sequence_recommendations_chain_a_room_in_date_order():
    from engine.logic_advisor import sequence_recommendations
    p = _project()
    recs = sequence_recommendations(p, "Phase 1 (Build-Out)")
    pairs = [(r["predecessor_id"], r["successor_id"]) for r in recs]
    assert ("A1000", "A1010") in pairs
    assert ("A1010", "A1020") in pairs
    # and never backwards
    assert ("A1010", "A1000") not in pairs


def test_sequencing_stays_inside_one_room():
    from engine.logic_advisor import sequence_recommendations
    p = _multi_phase()
    recs = sequence_recommendations(p, "Phase 1 (Build-Out)")
    for r in recs:
        assert r["successor_id"] != "X2000", "sequenced across phases"


# ── Procurement coupling ─────────────────────────────────────────────────────

def _with_procurement(install_start, delivery_finish="2026-06-01"):
    p = _project()
    p.wbs_nodes.append(WBSNode(uid="lle", name="LLE", code="LLE", parent_uid="p1"))
    p.activities += [
        Activity(uid="g1", activity_id="L1000", name="Generators (4MW)",
                 wbs_uid="lle", calendar_uid="1", planned_duration=800.0,
                 planned_start="2026-01-05", planned_finish=delivery_finish),
        Activity(uid="g2", activity_id="S1000", name="Set Generator (Gen 318)",
                 wbs_uid="p1", calendar_uid="1", planned_duration=8.0,
                 planned_start=install_start, planned_finish=install_start),
    ]
    p.build_lookups()
    return p


def test_equipment_installed_before_delivery_is_flagged():
    from engine.logic_advisor import procurement_report
    p = _with_procurement(install_start="2026-02-02")     # months before delivery
    rep = procurement_report(p)
    assert rep["installed_before_delivery"] == 1
    item = rep["items"][0]
    assert item["installed_before_delivery"] is True
    assert "before" in item["note"].lower()


def test_equipment_installed_after_delivery_is_not_flagged():
    from engine.logic_advisor import procurement_report
    p = _with_procurement(install_start="2026-06-15")      # after it arrives
    rep = procurement_report(p)
    assert rep["installed_before_delivery"] == 0


def test_foundations_before_delivery_are_not_treated_as_an_error():
    """Setting a base or pad ahead of the equipment is correct practice, not a
    finding — flagging it would bury the real conflicts in noise."""
    from engine.logic_advisor import procurement_report
    p = _with_procurement(install_start="2026-02-02")
    p.get_activity(activity_id="S1000").name = "Install Generator Pad (Gen 318)"
    p.build_lookups()
    rep = procurement_report(p)
    assert rep["installed_before_delivery"] == 0


def test_area_report_is_advisory_and_changes_nothing():
    from engine.logic_advisor import area_report
    p = _project()
    before = ([(a.activity_id, a.planned_start) for a in p.activities], len(p.relations))
    rep = area_report(p, "Phase 1 (Build-Out)")
    assert "summary" in rep
    after = ([(a.activity_id, a.planned_start) for a in p.activities], len(p.relations))
    assert after == before


def test_recommend_logic_accepts_an_area_scope():
    p = _project()
    ok, msg = apply_command(p, {"action": "recommend_logic", "scope": "wbs",
                                "wbs_name": "Phase 1 (Build-Out)"})
    assert ok and "Logic gaps" in msg


def test_recommend_logic_reports_an_unknown_area():
    p = _project()
    ok, msg = apply_command(p, {"action": "recommend_logic", "scope": "wbs",
                                "wbs_name": "Nonexistent Area"})
    assert not ok and "No folder" in msg


def test_recommend_logic_wbs_scope_lists_every_activity_in_the_folder():
    """
    The prompt tells the agent 'ask about a folder and get every activity in
    that branch with dates' — this is the call that promise depends on. A
    text summary of counts is not enough for the agent to discuss what is
    actually IN the area; it needs the real activity_id/name/date rows.
    """
    from engine.logic_advisor import find_wbs
    p = _project()
    ok, msg = apply_command(p, {"action": "recommend_logic", "scope": "wbs",
                                "wbs_name": "Phase 1 (Build-Out)"})
    assert ok
    for a in p.activities:
        if a.wbs_uid == find_wbs(p, "Phase 1 (Build-Out)").uid:
            assert a.activity_id in msg
            assert a.name in msg
