"""
test_procurement_map.py — does every system arrive before the work that needs it?

Three things here are worth more than the rest, because each one is a place a
plausible implementation gets a confidently wrong answer:

  Location is not equipment. "OH Lighting (Gen 325)" is lighting in generator
  room 325. Reading the room tag as equipment made 603 activities on the
  reference schedule look like generator consumers when 159 are — and a map
  that flags a quarter of the job is a map nobody opens twice.

  Coverage is reachability. A delivery tied to the first install, which
  carries on to the rest, has covered all of them. Demanding a direct link to
  each would report a correctly-wired schedule as broken and invite a hundred
  redundant ties.

  Some work legitimately precedes delivery. Pads, layout and high steel are
  done before the equipment lands. Demanding the generator arrive before its
  own housekeeping pad flags the correct sequence as an error.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import procurement_map as pm
from engine.edit_engine import apply_command, is_advisory
from engine.logic_advisor import _equipment_of, location_tag, strip_location
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _p(folders):
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = []
    for uid, name, parent in folders:
        p.wbs_nodes.append(WBSNode(uid=uid, name=name, code=uid,
                                   parent_uid=parent))
    p.activities, p.relations = [], []
    return p


def _act(p, uid, name, folder, start, finish):
    a = Activity(uid=uid, activity_id=uid, name=name, wbs_uid=folder,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40,
                 remaining_duration=40, planned_start=start,
                 planned_finish=finish)
    p.activities.append(a)
    p.build_lookups()
    return a


def _rel(p, a, b):
    p.relations.append(Relation(uid=f"r{len(p.relations)}", predecessor_uid=a,
                                successor_uid=b, type="Finish to Start",
                                lag=0.0))
    p.build_lookups()


def _job(need_start="2026-06-01", arrive="2026-03-02", tie=False):
    """A chiller delivery in a phase LLE folder, and the work that needs it."""
    p = _p([("PH1", "Phase 1 (Build-Out)", None),
            ("LLE", "LLE", "PH1"),
            ("W", "Lineup 1", "PH1")])
    _act(p, "S1", "Chillers", "LLE", "2026-01-05", arrive)
    _act(p, "I1", "Chiller Lineup 1 - Set Equipment", "W", need_start,
         "2026-06-12")
    if tie:
        _rel(p, "S1", "I1")
    return p


def _row(p, system="chiller", phase=None):
    d = pm.analyse(p, phase=phase, system=system)
    assert d["systems"], f"no row for {system}"
    return d["systems"][0]


# ── the parse: a room is not a piece of equipment ────────────────────────────

def test_a_trailing_room_tag_is_not_read_as_equipment():
    """The single most important behaviour in this module."""
    assert _equipment_of("OH Lighting (Gen 325)") == []
    assert _equipment_of("Install Hangers (Gen 319)") == []
    assert _equipment_of("Set CRAHs (Data Hall 202)") == ["crah"]


def test_equipment_named_outside_the_room_tag_still_matches():
    assert _equipment_of("Set Generator (Gen 321)") == ["generator"]
    assert _equipment_of("Pull LBB to Generator (Gen 318)") == ["generator"]


def test_a_spec_parenthetical_is_kept_because_it_is_not_a_location():
    """'(4MW)' and '(2500KVA)' describe the equipment and belong to the name;
    stripping them would lose the delivery lines entirely."""
    assert strip_location("Generators (4MW)") == "Generators (4MW)"
    assert strip_location("Transformer B01 (2500KVA)") == "Transformer B01 (2500KVA)"
    assert _equipment_of("Generators (4MW)") == ["generator"]


def test_the_room_tag_is_recoverable_rather_than_thrown_away():
    assert location_tag("OH Lighting (Gen 325)") == "Gen 325"
    assert location_tag("Chillers") is None


def test_a_short_code_does_not_match_inside_a_longer_word():
    """'gis' fired on 'Holder Logistics/Planning' before word boundaries."""
    assert _equipment_of("Holder Logistics/Planning") == []
    assert _equipment_of("Set GIS") == ["gis"]


def test_plurals_still_match():
    assert _equipment_of("Chillers") == ["chiller"]
    assert _equipment_of("CDUs") == ["cdu"]


# ── the verdicts ─────────────────────────────────────────────────────────────

def test_work_dated_before_its_delivery_is_at_risk():
    r = _row(_job(need_start="2026-01-12", arrive="2026-03-02"))
    assert r["verdict"] == pm.AT_RISK
    assert r["buffer_days"] < 0


def test_dates_that_work_with_no_logic_holding_them_are_flagged():
    """The one people miss — fine today, silently wrong the moment either end
    moves, because nothing is keeping the gap open."""
    r = _row(_job(tie=False))
    assert r["verdict"] == pm.NO_LOGIC
    assert r["buffer_days"] > 0 and r["uncovered"] == 1


def test_dates_that_work_and_are_wired_are_ready():
    r = _row(_job(tie=True))
    assert r["verdict"] == pm.READY
    assert r["covered"] == 1 and r["uncovered"] == 0


def test_work_with_no_supply_line_anywhere_is_reported():
    p = _job()
    p.activities = [a for a in p.activities if a.activity_id != "S1"]
    p.build_lookups()
    assert _row(p)["verdict"] == pm.NO_DELIVERY


def test_a_delivery_nothing_consumes_is_reported():
    p = _job()
    p.activities = [a for a in p.activities if a.activity_id != "I1"]
    p.build_lookups()
    r = _row(p)
    assert r["verdict"] == pm.NO_CONSUMER
    assert r["standalone_deliveries"] == 1


# ── coverage is reachability, not a direct link ──────────────────────────────

def test_a_delivery_covers_work_it_reaches_through_other_activities():
    """Demanding a direct tie to every consumer would call a correctly-wired
    schedule broken and invite a hundred redundant relationships."""
    p = _job(tie=True)
    _act(p, "I2", "Chiller Lineup 1 - Tie-In Equipment", "W", "2026-06-15",
         "2026-06-20")
    _rel(p, "I1", "I2")                      # reached via I1, not directly
    r = _row(p)
    assert r["covered"] == 2 and r["verdict"] == pm.READY


def test_one_unreachable_consumer_is_enough_to_withhold_ready():
    p = _job(tie=True)
    _act(p, "I2", "Chiller Lineup 2 - Set Equipment", "W", "2026-06-15",
         "2026-06-20")
    r = _row(p)
    assert r["verdict"] == pm.NO_LOGIC
    assert r["uncovered"] == 1
    assert r["uncovered_sample"][0]["activity_id"] == "I2"


def test_a_cycle_does_not_hang_the_coverage_walk():
    p = _job(tie=True)
    _rel(p, "I1", "S1")
    assert _row(p)["covered"] == 1


# ── work that legitimately precedes delivery ─────────────────────────────────

def test_a_pad_dated_before_its_equipment_is_not_a_conflict():
    """The pad is poured before the chiller lands. That is the correct
    sequence, and flagging it would bury the real conflicts in noise."""
    p = _job(need_start="2026-06-01")
    _act(p, "P1", "Chiller Housekeeping Pad", "W", "2026-01-06", "2026-01-09")
    r = _row(p)
    assert r["verdict"] != pm.AT_RISK
    assert r["consumers"] == 2 and r["needs_delivery"] == 1


def test_the_need_date_comes_from_work_that_really_needs_it():
    p = _job(need_start="2026-06-01")
    _act(p, "P1", "Chiller Layout", "W", "2026-01-06", "2026-01-09")
    assert _row(p)["need"] == "2026-06-01"


# ── phase scoping ────────────────────────────────────────────────────────────

def _two_phase_job():
    p = _p([("PH1", "Phase 1 (Build-Out)", None), ("L1", "LLE", "PH1"),
            ("W1", "Lineup 1", "PH1"),
            ("PH2", "Phase 2 (Build-Out)", None), ("W2", "Lineup 2", "PH2")])
    _act(p, "S1", "Chillers", "L1", "2026-01-05", "2026-03-02")
    _act(p, "I1", "Set Chiller", "W1", "2026-06-01", "2026-06-05")
    _act(p, "I2", "Set Chiller", "W2", "2026-07-01", "2026-07-05")
    return p


def test_a_system_is_reported_per_phase():
    d = pm.analyse(_two_phase_job(), system="chiller")
    assert {r["phase"] for r in d["systems"]} == {"Phase 1", "Phase 2"}


def test_equipment_bought_once_for_the_site_feeds_every_phase():
    """The reference schedule has exactly one chiller delivery, in Phase 1,
    feeding chiller work in all three phases. Insisting on a same-phase line
    called two of those 'no delivery at all', which is both wrong and the kind
    of false alarm that makes people stop reading the report."""
    d = pm.analyse(_two_phase_job(), system="chiller", phase="Phase 2")
    r = d["systems"][0]
    assert r["verdict"] != pm.NO_DELIVERY
    assert r["cross_phase_from"] == ["Phase 1"]


def test_a_cross_phase_match_is_never_passed_off_as_a_phase_match():
    txt = pm.story(_two_phase_job(), "chiller", "Phase 2")
    assert "no delivery line of its own" in txt and "Phase 1" in txt


# ── a compound delivery is not four findings ─────────────────────────────────

def test_a_skids_contents_do_not_each_become_an_orphan_delivery():
    """'MV Skids 1 (XFMR, UPS, Battery Cabinet)' is one delivery read four
    ways, not four deliveries with nothing installing them."""
    p = _p([("PH1", "Phase 1 (Build-Out)", None), ("L", "LLE", "PH1"),
            ("W", "ER R101", "PH1")])
    _act(p, "S1", "MV Skids 1 (XFMR, UPS, Battery Cabinet)", "L",
         "2026-01-05", "2026-03-02")
    _act(p, "I1", "Set Electrical Skids", "W", "2026-06-01", "2026-06-05")
    r = _row(p, system="battery")
    assert r["verdict"] == pm.NO_CONSUMER
    assert "skid" in r["compound_with"]


def test_a_dedicated_line_with_nothing_installing_it_is_a_real_finding():
    """Nineteen 'Transformer B01' rows arriving with no install work is the
    finding; calling it a skid component would bury it."""
    p = _p([("PH1", "Phase 1 (Build-Out)", None), ("L", "LLE", "PH1"),
            ("W", "ER R101", "PH1")])
    _act(p, "S1", "MV Skids 1 (XFMR, UPS)", "L", "2026-01-05", "2026-03-02")
    _act(p, "S2", "Transformer B01 (2500KVA)", "L", "2026-01-05", "2026-02-02")
    _act(p, "I1", "Set Electrical Skids", "W", "2026-06-01", "2026-06-05")
    r = _row(p, system="transformer")
    assert r["compound_with"] == [], "a dedicated line was called a component"
    assert r["standalone_deliveries"] == 1
    assert "in its own right" in pm.story(p, "transformer")


def test_the_representative_delivery_prefers_a_dedicated_line():
    p = _p([("PH1", "Phase 1 (Build-Out)", None), ("L", "LLE", "PH1")])
    _act(p, "S1", "MV Skids 1 (XFMR, UPS)", "L", "2026-01-05", "2026-03-02")
    _act(p, "S2", "Transformer B01 (2500KVA)", "L", "2026-01-05", "2026-03-02")
    assert _row(p, system="transformer")["arrival_id"] == "S2"


# ── the upstream chain is not the delivery ───────────────────────────────────

def test_a_submittal_is_not_treated_as_the_delivery():
    """The reference schedule carries selection, pricing, OAA approval and
    submittal review for every system, and all of them name the equipment.
    Reading those as arrivals would put the delivery months early and report a
    job as comfortably covered when it is not."""
    p = _job(need_start="2026-06-01", arrive="2026-03-02")
    _act(p, "SUB", "Long Lead Equipment Submittal Review / Approval Chillers",
         "LLE", "2025-06-01", "2025-07-01")
    r = _row(p)
    assert r["arrival"] == "2026-03-02", "an approval was mistaken for arrival"
    assert r["deliveries"] == 1


# ── it never writes ──────────────────────────────────────────────────────────

def test_nothing_in_this_module_changes_the_schedule():
    p = _job()
    before = ([(a.activity_id, a.planned_start, a.planned_finish)
               for a in p.activities],
              [(r.predecessor_uid, r.successor_uid) for r in p.relations])
    pm.analyse(p)
    pm.report(p)
    pm.story(p, "chiller")
    pm.digest(p)
    after = ([(a.activity_id, a.planned_start, a.planned_finish)
              for a in p.activities],
             [(r.predecessor_uid, r.successor_uid) for r in p.relations])
    assert before == after


def test_both_actions_are_advisory():
    assert is_advisory("procurement_map") and is_advisory("procurement_story")


def test_the_map_action_returns_the_report():
    p = _job()
    ok, msg = apply_command(p, {"action": "procurement_map"})
    assert ok and "PROCUREMENT MAP" in msg


def test_the_story_action_names_the_activities():
    p = _job()
    ok, msg = apply_command(p, {"action": "procurement_story",
                                "system": "chiller"})
    assert ok and "S1" in msg and "I1" in msg


def test_the_story_action_needs_a_system():
    """And says what to pass, rather than reporting an empty map as success."""
    ok, msg = apply_command(_job(), {"action": "procurement_story"})
    assert not ok and "needs a system" in msg


def test_an_unknown_system_says_so_rather_than_returning_an_empty_map():
    assert "Nothing in the schedule" in pm.story(_job(), "flux capacitor")


# ── the prose says the numbers ───────────────────────────────────────────────

def test_the_story_gives_both_dates_and_the_gap():
    txt = pm.story(_job(need_start="2026-06-01", arrive="2026-03-02"), "chiller")
    assert "2026-03-02" in txt and "2026-06-01" in txt
    assert "working days of room" in txt


def test_a_conflict_says_it_will_not_be_tied_shut():
    txt = pm.story(_job(need_start="2026-01-12", arrive="2026-03-02"), "chiller")
    assert "BEFORE the equipment arrives" in txt
    assert "decision about the job" in txt


def test_the_digest_leads_with_what_is_wrong():
    p = _job(need_start="2026-01-12", arrive="2026-03-02")
    d = pm.digest(p)
    assert "at risk" in d and "chiller" in d


def test_the_digest_is_empty_when_there_is_no_procurement_to_talk_about():
    p = _p([("W", "Area", None)])
    _act(p, "A1", "Pull Wire", "W", "2026-01-05", "2026-01-09")
    assert pm.digest(p) == ""


# ── closing the "dates work, nothing holding them" rows ──────────────────────

def test_covering_gaps_ties_every_activity_that_needs_the_delivery():
    """procurement_wire ties the FIRST install per line; on the reference
    schedule that made 42 ties and moved exactly one system to green, because
    the second and fortieth consumer were never connected to anything."""
    p = _job(tie=False)
    _act(p, "I2", "Chiller Lineup 2 - Set Equipment", "W", "2026-06-15",
         "2026-06-20")
    r = pm.cover_gaps(p)
    assert {c["successor_id"] for c in r["commands"]} == {"I1", "I2"}


def test_work_already_reachable_from_the_delivery_is_not_tied_again():
    """Otherwise a properly-wired sequence collects a redundant tie per row
    and the schedule ends up with a hundred relationships saying what four
    already said."""
    p = _job(tie=True)
    _act(p, "I2", "Chiller Lineup 1 - Tie-In", "W", "2026-06-15", "2026-06-20")
    _rel(p, "I1", "I2")
    assert pm.cover_gaps(p)["commands"] == []


def test_a_tie_just_proposed_covers_what_is_downstream_of_it():
    """The reachability has to account for ties proposed a moment ago, or a
    chain of five gets five ties instead of one."""
    p = _job(tie=False)
    _act(p, "I2", "Chiller Lineup 1 - Tie-In", "W", "2026-06-15", "2026-06-20")
    _rel(p, "I1", "I2")
    r = pm.cover_gaps(p)
    assert [c["successor_id"] for c in r["commands"]] == ["I1"]


def test_work_dated_before_its_delivery_is_reported_never_tied():
    p = _job(need_start="2026-01-12", arrive="2026-03-02")
    r = pm.cover_gaps(p)
    assert r["commands"] == []


def test_a_pad_is_never_tied_behind_the_equipment_it_is_poured_for():
    """Even on a row that IS being wired — the pad is poured before the
    chiller lands, so a tie there would be wrong rather than redundant."""
    p = _job(tie=False)
    _act(p, "P1", "Chiller Housekeeping Pad", "W", "2026-01-06", "2026-01-09")
    r = pm.cover_gaps(p)
    tied = {c["successor_id"] for c in r["commands"]}
    assert "I1" in tied, "it should still wire the real consumer"
    assert "P1" not in tied, "it tied a pad behind its own equipment"


def test_an_unconnected_pad_does_not_hold_a_system_amber_forever():
    """It cannot be tied, so counting it as unconnected would leave the row
    permanently amber with nothing anyone could do — the worst kind of
    warning."""
    p = _job(tie=True)
    _act(p, "P1", "Chiller Housekeeping Pad", "W", "2026-01-06", "2026-01-09")
    assert _row(p)["verdict"] == pm.READY


def test_covering_the_gaps_turns_the_row_green():
    p = _job(tie=False)
    assert _row(p)["verdict"] == pm.NO_LOGIC
    ok, _ = apply_command(p, {"action": "procurement_cover", "apply": True})
    assert ok and _row(p)["verdict"] == pm.READY


def test_the_cover_action_reports_by_default():
    p = _job(tie=False)
    rels = len(p.relations)
    ok, msg = apply_command(p, {"action": "procurement_cover"})
    assert ok and len(p.relations) == rels
    assert "Nothing applied" in msg


def test_covering_twice_adds_nothing_the_second_time():
    p = _job(tie=False)
    apply_command(p, {"action": "procurement_cover", "apply": True})
    n = len(p.relations)
    apply_command(p, {"action": "procurement_cover", "apply": True})
    assert len(p.relations) == n


def test_covering_never_removes_logic_the_user_set():
    p = _job(tie=False)
    _act(p, "I2", "Chiller Lineup 2 - Set Equipment", "W", "2026-06-15",
         "2026-06-20")
    _rel(p, "I2", "I1")                      # an odd tie, deliberately theirs
    before = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    apply_command(p, {"action": "procurement_cover", "apply": True})
    after = {(r.predecessor_uid, r.successor_uid) for r in p.relations}
    assert before <= after


def test_covering_can_be_scoped_to_one_system():
    p = _job(tie=False)
    _act(p, "S2", "Generators (4MW)", "LLE", "2026-01-05", "2026-03-02")
    _act(p, "G1", "Set Generator", "W", "2026-06-01", "2026-06-05")
    r = pm.cover_gaps(p, system="generator")
    assert {c["successor_id"] for c in r["commands"]} == {"G1"}


def test_back_to_back_delivery_reads_as_no_room_rather_than_a_conflict():
    """The delivery's own finish day is worked, so work starting the next
    working day is zero buffer, not minus one."""
    p = _job(arrive="2026-03-02", need_start="2026-03-03")
    r = _row(p)
    assert r["buffer_days"] == 0 and r["verdict"] != pm.AT_RISK
