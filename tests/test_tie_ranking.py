"""
test_tie_ranking.py — a candidate tie is judged on several signals, not just
on whose date is nearest.

Ranking by date proximity alone produced ties like "Complete Construction <-
Final Floor Finishes, implied lag 55d": nothing better was in scope, so the
least-bad date won and was offered as a driver. And on a milestone whose own
date is stale it was worse than useless — "Finish Precast", dated 2025-11-03
while every precast activity finishes in 2026, was handed "MEP Underground
Excavations" purely because that happened to abut the date.

Now: date fit is a multiplier over the other evidence (shared subject, same
area, WBS distance, trade order, terminal work, procurement coupling), so a
tie needs BOTH a plausible relationship and a plausible gap. And a milestone
whose subject-matching work all finishes AFTER it is reported as an
unsupportable date rather than anchored to a coincidence.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import logic_advisor as la
from engine.schedule_model import Project, Activity, WBSNode, Calendar


def _proj():
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="JOB", parent_uid=None)]
    p.activities = []
    return p


def _wbs(p, uid, name, parent="root"):
    p.wbs_nodes.append(WBSNode(uid=uid, name=name, code=name[:18], parent_uid=parent))
    p.build_lookups()


def _act(p, uid, name, start, finish, wbs="root", ms=False, dur=5.0):
    a = Activity(uid=uid, activity_id=uid.upper(), name=name, wbs_uid=wbs,
                 calendar_uid="1", status="Not Started",
                 activity_type="Finish Milestone" if ms else "Task Dependent",
                 planned_duration=0.0 if ms else dur * 8,
                 remaining_duration=0.0 if ms else dur * 8,
                 planned_start=start, planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _score(p, pred, succ, lag):
    return la.score_tie(la._Ctx(p), pred, succ, lag)[0]


# ── date fit gates, it does not merely contribute ─────────────────────────────

def test_a_distant_date_cannot_be_rescued_by_other_signals():
    """The 78d tie that used to score 0.53 on the strength of everything else."""
    p = _proj()
    near = _act(p, "a1", "Energize Equipment and Start Up", "2026-03-02", "2026-03-06")
    ms = _act(p, "m1", "Energize Complete", "2026-06-15", "2026-06-15", ms=True)
    close = _score(p, near, ms, 2)
    far = _score(p, near, ms, 78)
    assert far < 0.30 <= close


def test_a_perfect_date_with_nothing_behind_it_is_not_a_driver():
    p = _proj()
    a = _act(p, "a1", "Paving and Striping", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "Switchgear Energized", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, a, ms, 0) < 0.30


def test_a_negative_gap_is_scored_near_zero():
    p = _proj()
    a = _act(p, "a1", "Precast Erection", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "Precast Complete", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, a, ms, -30) < 0.15


# ── the other signals ─────────────────────────────────────────────────────────

def test_shared_subject_beats_a_coincidental_date():
    p = _proj()
    same = _act(p, "a1", "Precast Area 7 Turnover", "2026-02-02", "2026-02-06")
    other = _act(p, "a2", "MEP Underground Excavations", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "Finish Precast", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, same, ms, 0) > _score(p, other, ms, 0)


def test_different_rooms_sink_a_tie_however_close_the_dates():
    p = _proj()
    right = _act(p, "a1", "MV 105 Terminations", "2026-02-02", "2026-02-06")
    wrong = _act(p, "a2", "MV 109 Terminations", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "MV 105 Ready to Energize", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, right, ms, 0) > _score(p, wrong, ms, 0)


def test_a_shared_phase_counts_for_less_than_a_shared_room():
    """Half the schedule is in PH1 — that is not evidence of a handoff."""
    p = _proj()
    room = _act(p, "a1", "CRAH 4 Start-Up", "2026-02-02", "2026-02-06")
    phase = _act(p, "a2", "Ceiling Grid Install (PH1)", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "CRAH 4 Commissioned (PH1)", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, room, ms, 0) > _score(p, phase, ms, 0)


def test_backwards_trade_order_is_penalised():
    """Foundations cannot be gated by painting — paint runs long after."""
    p = _proj()
    fwd = _act(p, "a1", "Foundation Excavation", "2026-02-02", "2026-02-06")
    back = _act(p, "a2", "Foundation Final Paint", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "Foundation Steel Erection", "2026-02-09", "2026-02-13")
    assert _score(p, fwd, ms, 0) > _score(p, back, ms, 0)


def test_a_turnover_anchors_a_milestone_better_than_mid_stream_work():
    p = _proj()
    end = _act(p, "a1", "Precast Area 7 Turnover", "2026-02-02", "2026-02-06")
    mid = _act(p, "a2", "Precast Area 7 Grouting", "2026-02-02", "2026-02-06")
    ms = _act(p, "m1", "Precast Complete", "2026-02-09", "2026-02-09", ms=True)
    assert _score(p, end, ms, 0) > _score(p, mid, ms, 0)


def test_same_folder_beats_a_distant_branch():
    p = _proj()
    _wbs(p, "near", "Structure")
    _wbs(p, "far", "Sitework")
    a = _act(p, "a1", "Steel Erection", "2026-02-02", "2026-02-06", wbs="near")
    b = _act(p, "a2", "Steel Erection", "2026-02-02", "2026-02-06", wbs="far")
    ms = _act(p, "m1", "Steel Complete", "2026-02-09", "2026-02-09", wbs="near", ms=True)
    assert _score(p, a, ms, 0) > _score(p, b, ms, 0)


def test_delivery_gates_its_own_installation():
    p = _proj()
    deliv = _act(p, "a1", "Deliver Switchgear", "2026-02-02", "2026-02-06")
    other = _act(p, "a2", "Deliver Ductwork", "2026-02-02", "2026-02-06")
    inst = _act(p, "m1", "Install Switchgear", "2026-02-09", "2026-02-13")
    assert _score(p, deliv, inst, 0) > _score(p, other, inst, 0)


# ── an unsupportable milestone date is reported, not anchored ────────────────

def _precast_case():
    """The real shape: milestone dated months before the work it is about."""
    p = _proj()
    _wbs(p, "str", "Structure")
    _wbs(p, "ms", "Milestones")
    _act(p, "a1", "Precast Erection Area 7", "2026-04-01", "2026-04-27", wbs="str")
    _act(p, "a2", "MEP Underground Excavations", "2025-10-01", "2025-10-31", wbs="str")
    ms = _act(p, "m1", "Finish Precast", "2025-11-03", "2025-11-03", wbs="ms", ms=True)
    return p, ms


def test_a_milestone_dated_before_its_own_work_is_flagged_not_anchored():
    p, ms = _precast_case()
    recs = la.milestone_drivers(p, ms)
    assert recs
    assert recs[0]["verdict"] == la.CONFLICT
    assert "Precast" in recs[0]["predecessor_name"]


def test_the_conflict_says_what_to_do_about_it():
    p, ms = _precast_case()
    rec = la.milestone_drivers(p, ms)[0]
    assert "unsupportable" in rec["rationale"].lower()
    assert "2026-04-27" in rec["date_check"]


def test_the_coincidental_neighbour_is_not_offered_instead():
    p, ms = _precast_case()
    recs = la.milestone_drivers(p, ms)
    assert not any("Excavations" in r["predecessor_name"] for r in recs)


# ── silence is a valid answer ─────────────────────────────────────────────────

def test_a_milestone_with_nothing_plausible_gets_no_driver():
    p = _proj()
    _act(p, "a1", "Paving and Striping", "2025-01-05", "2025-01-09")
    ms = _act(p, "m1", "Switchgear Energized", "2026-06-01", "2026-06-01", ms=True)
    assert la.milestone_drivers(p, ms) == []


def test_the_report_says_when_it_has_no_confident_driver():
    p = _proj()
    _act(p, "a1", "Paving and Striping", "2025-01-05", "2025-01-09")
    _act(p, "m1", "Switchgear Energized", "2026-06-01", "2026-06-01", ms=True)
    rep = la.milestone_report(p)
    assert rep["milestones"][0]["no_confident_driver"] is True


def test_a_clear_winner_is_offered_on_its_own():
    """No runners-up to choose between when one candidate is obviously right."""
    p = _proj()
    _wbs(p, "str", "Structure")
    _act(p, "a1", "Precast Area 7 Turnover", "2026-02-02", "2026-02-06", wbs="str")
    _act(p, "a2", "Precast Area 7 Grouting", "2026-01-05", "2026-01-09", wbs="str")
    ms = _act(p, "m1", "Precast Complete", "2026-02-09", "2026-02-09", wbs="str", ms=True)
    recs = la.milestone_drivers(p, ms, limit=3)
    assert len(recs) == 1 and "Turnover" in recs[0]["predecessor_name"]


def test_every_offered_tie_carries_its_confidence_and_reasons():
    p = _proj()
    _wbs(p, "str", "Structure")
    _act(p, "a1", "Precast Area 7 Turnover", "2026-02-02", "2026-02-06", wbs="str")
    ms = _act(p, "m1", "Precast Complete", "2026-02-09", "2026-02-09", wbs="str", ms=True)
    rec = la.milestone_drivers(p, ms)[0]
    assert 0.0 < rec["confidence"] <= 1.0
    assert rec["signals"] and isinstance(rec["signals"], list)


# ── the classifiers themselves ────────────────────────────────────────────────

def test_area_tags_pick_out_rooms_levels_and_units():
    assert "mv105" in la._area_tags("MV 105 Terminations")
    assert "lvl3" in la._area_tags("Level 3 Ceiling Grid")
    assert "area7" in la._area_tags("Precast Area 7 Turnover")
    assert "ph2" in la._area_tags("Ceiling Grid Install (PH2)")
    assert "cup1" in la._area_tags("CWP-CUP-01 - Energization")


def test_trade_rank_orders_the_usual_construction_flow():
    r = la._trade_rank
    assert r("Deliver Switchgear") < r("Excavate Footings") < r("Steel Erection")
    assert r("Steel Erection") < r("Drywall Close In") < r("Level 4 Cx: Functional")
    assert r("Punch Walk") > r("Final Paint")


def test_an_unrecognised_activity_name_scores_neutral_not_wrong():
    assert la._trade_rank("Widget Reticulation") is None
