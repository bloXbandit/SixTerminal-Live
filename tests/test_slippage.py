"""
test_slippage.py — what moved between the contract schedule and each update.

The arithmetic here is easy and the measurement is not. Two things make a
rolled-up number lie, and both show up on these jobs:

A group's window taken from whatever each issue happened to contain reports
scope as slip. The June re-issue of MDC-3 carries 3,398 activities against the
contract's 1,957 -- the GC added 190 roof activities and took structural steel
from 27 to 439 -- so "the last finish in Generator Rooms" moved without a
single existing date changing.

And an update that has started work reports actuals. An actual start against a
planned start is progress, not movement.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import slippage as sl


# ── which group a row belongs to ─────────────────────────────────────────────

def test_the_group_comes_off_the_id_wherever_the_segment_sits():
    """
    GEN is the second segment on MDC-2 and MDC-3 and the third on MDC-1, so
    the id is searched a segment at a time rather than by position.
    """
    assert sl.categorize("MDC3.GEN.315.1000", "")[0] == "Generator Rooms"
    assert sl.categorize("MDC1.PH1.GEN.1000", "")[0] == "Generator Rooms"


def test_the_name_carries_a_row_whose_id_says_nothing():
    """A printed Gantt gives a name and an id and nothing else to go on."""
    assert sl.categorize("X.1000", "Install High Steel")[0] == "Structural Steel"
    assert sl.categorize("X.1000", "Pour 2 Level 1")[0] == "Slabs & Pours"


def test_contract_commissioning_beats_the_room_it_happens_in():
    """
    Level 3 Cx is a contract date that happens to sit on a room's id, and it
    belongs with the commissioning it is reported against. The order of the
    table is what settles that, so this pins the order.
    """
    g, _ = sl.categorize("MDC3.PH3.CO.CL.4350",
                         "Level 3 Cx: Pre-Functional Testing")
    assert g == "Commissioning & Startup"
    g, _ = sl.categorize("MDC3.PH1.ER.209.1500", "Level 4 Cx: Functional Testing")
    assert g == "Commissioning & Startup"


def test_a_rooms_own_testing_step_stays_with_the_room():
    """
    The other side of that line, and the one worth being deliberate about.
    "Testing and Inspections" as the last step of an electrical room is part of
    building the room, not the contractual Cx -- pulling it out left the room
    group missing its own completion and inflated commissioning with 300 rows
    of room work.
    """
    g, _ = sl.categorize("MDC3.PH3.ER.104.4160",
                         "Testing and Inspections - ER 104 - Data Center C")
    assert g == "Electrical Rooms"


def test_the_phase_is_read_off_the_id():
    assert sl.categorize("MDC3.PH2.LLE.3700", "")[1] == "Phase 2"
    assert sl.categorize("MDC3.ST.PC.A9.1020", "")[1] == "All phases"


def test_a_row_matching_nothing_is_kept_rather_than_dropped():
    """Work nobody has a bucket for is still work, and still slips."""
    assert sl.categorize("MDC3.ZZZ.1000", "Something unforeseen")[0] == "Other"


# ── reading a schedule in, whatever it arrived as ───────────────────────────

def _gantt(*rows):
    return {"rows": [{"kind": "activity", "activity_id": a, "name": n,
                      "start": s, "finish": f, "start_actual": sa,
                      "finish_actual": fa}
                     for a, n, s, f, sa, fa in rows]}


def test_a_printed_gantt_becomes_a_snapshot():
    snap = sl.Snapshot.from_gantt(
        _gantt(("A.10", "Install High Steel", "2026-02-02", "2026-02-06", 0, 0)),
        "Contract")
    assert len(snap) == 1
    assert snap.rows["A.10"]["finish"] == "2026-02-06"


def test_a_row_repeated_on_a_continuation_page_is_not_two_rows():
    snap = sl.Snapshot.from_gantt(
        _gantt(("A.10", "Pull wire", "2026-02-02", "2026-02-06", 0, 0),
               ("A.10", "Pull wire", "2026-02-02", "2026-02-09", 0, 0)),
        "Print")
    assert len(snap) == 1
    assert snap.rows["A.10"]["finish"] == "2026-02-09", "kept the widest"


def test_a_row_with_no_dates_at_all_is_not_carried():
    """A milestone the print left blank says nothing about movement."""
    snap = sl.Snapshot.from_gantt(
        _gantt(("A.10", "Substantial Completion", None, None, 0, 0)), "Print")
    assert len(snap) == 0


def test_an_actual_date_is_recorded_as_actual():
    snap = sl.Snapshot.from_gantt(
        _gantt(("A.10", "Mobilize", "2026-02-02", "2026-02-06", 1, 1)), "Update")
    assert snap.rows["A.10"]["start_actual"]
    assert snap.rows["A.10"]["finish_actual"]


# ── the comparison ──────────────────────────────────────────────────────────

def _snap(label, rows):
    return sl.Snapshot.from_rows(
        [{"activity_id": a, "name": n, "start": s, "finish": f}
         for a, n, s, f in rows], label)


def test_a_group_that_moved_later_reports_the_slip():
    base = _snap("Contract", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20")])
    upd = _snap("June", [("G.10", "Gen 315 rough in", "2026-03-02", "2026-03-20")])
    res = sl.compare(base, [upd])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == 28   # 2026 is not a leap year


def test_work_pulled_earlier_reports_a_negative_slip():
    base = _snap("Contract", [("G.10", "Gen 315 rough in", "2026-03-02", "2026-03-20")])
    upd = _snap("June", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20")])
    res = sl.compare(base, [upd])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == -28


def test_an_added_activity_is_scope_and_not_slippage():
    """
    The measurement this module exists for. June added 190 roof activities to
    MDC-3; a group window taken from whatever each issue contains calls that
    slip, and then the number cannot be put in front of the GC.
    """
    base = _snap("Contract", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20")])
    upd = _snap("June", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20"),
                         ("G.20", "Gen 316 rough in", "2027-01-04", "2027-06-30")])
    res = sl.compare(base, [upd])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == 0, "scope growth was reported as slip"
    assert res["scope"]["June"]["not_in_common"] == 1
    assert "G.20" in res["scope"]["June"]["sample"]


def test_a_dropped_activity_is_also_scope():
    base = _snap("Contract", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20"),
                              ("G.99", "Gen 999 deleted", "2027-01-01", "2027-12-31")])
    upd = _snap("June", [("G.10", "Gen 315 rough in", "2026-02-02", "2026-02-20")])
    res = sl.compare(base, [upd])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == 0
    assert res["scope"]["Contract"]["not_in_common"] == 1


def test_the_common_set_spans_every_issue_not_just_the_baseline():
    """
    With three issues compared at once, an activity two of them share is still
    not comparable. Intersecting pairwise against the baseline alone let one
    through and the group window then came off a different set per column.
    """
    base = _snap("C", [("A.10", "One", "2026-01-05", "2026-01-09"),
                       ("A.20", "Two", "2026-01-05", "2026-01-09")])
    u1 = _snap("U1", [("A.10", "One", "2026-01-05", "2026-01-09"),
                      ("A.20", "Two", "2026-01-05", "2026-01-09")])
    u2 = _snap("U2", [("A.10", "One", "2026-01-05", "2026-01-09")])
    res = sl.compare(base, [u1, u2])
    assert res["scope"]["common"] == 1


def test_several_updates_are_each_measured_against_the_contract():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    jun = _snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-03-06")])
    sep = _snap("Sept", [("G.10", "Gen 315", "2026-02-02", "2026-04-03")])
    res = sl.compare(base, [jun, sep])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == 14
    assert row["Sept"]["slip_days"] == 42, "measured against June, not the contract"


def test_the_baseline_itself_carries_no_slip():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    res = sl.compare(base, [_snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])])
    row = res["rows"][0]
    assert "slip_days" not in row["Contract"]


def test_progress_is_counted_so_a_group_can_be_read_as_underway():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    upd = sl.Snapshot.from_rows(
        [{"activity_id": "G.10", "name": "Gen 315", "start": "2026-02-02",
          "finish": "2026-02-20", "start_actual": True, "finish_actual": True}],
        "June")
    res = sl.compare(base, [upd])
    assert res["rows"][0]["June"]["complete"] == 1


def test_the_groups_come_out_in_the_order_the_table_declares():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                              ("M.10", "Substantial Completion", "2027-01-01", "2027-01-01")])
    res = sl.compare(base, [_snap("June", [
        ("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
        ("M.10", "Substantial Completion", "2027-01-01", "2027-01-01")])])
    groups = [r["group"] for r in res["rows"]]
    assert groups.index("Completion Milestones") < groups.index("Generator Rooms")


def test_nothing_in_common_is_reported_rather_than_divided_by_zero():
    base = _snap("Contract", [("A.10", "One", "2026-01-05", "2026-01-09")])
    res = sl.compare(base, [_snap("June", [("B.10", "Two", "2026-01-05", "2026-01-09")])])
    assert res["rows"] == []
    assert res["scope"]["common"] == 0
    assert "0 activities common" in sl.describe(res)


# ── the other half: what each issue actually says ───────────────────────────

def test_as_issued_counts_every_activity_the_issue_carries():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    upd = _snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                         ("G.20", "Gen 316", "2027-01-04", "2027-06-30")])
    res = sl.as_issued([base, upd])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["Contract"]["activities"] == 1
    assert row["June"]["activities"] == 2


def test_as_issued_shows_the_growth_that_like_for_like_holds_out():
    """
    The pair is the point: the same two issues read one way show no movement
    and read the other show five months, and the difference is scope.
    """
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    upd = _snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                         ("G.20", "Gen 316", "2027-01-04", "2027-06-30")])
    like = sl.compare(base, [upd])
    issued = sl.as_issued([base, upd])
    assert next(r for r in like["rows"]
                if r["group"] == "Generator Rooms")["June"]["slip_days"] == 0
    row = next(r for r in issued["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] > 400
    assert row["June"]["added"] == 1


def test_a_group_only_a_later_issue_has_is_still_listed():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    upd = _snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                         ("R.10", "Roof membrane", "2027-01-04", "2027-06-30")])
    res = sl.as_issued([base, upd])
    roof = next(r for r in res["rows"] if r["group"] == "Roof")
    assert roof["Contract"]["activities"] == 0
    assert roof["June"]["activities"] == 1
    assert roof["June"]["slip_days"] is None, "nothing to measure it against"


# ── a GC who renumbers every re-issue ───────────────────────────────────────

def test_each_update_is_measured_on_its_own_overlap_with_the_contract():
    """
    Held to one common set, an activity that only two of three issues carry
    drops out of all of them. On MDC-3 that took the basis from 1,142 and 974
    down to 968 and left most groups with a row or two. Pairwise keeps each
    column on everything it actually shares with the contract.
    """
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                              ("G.20", "Gen 316", "2026-02-02", "2026-02-20")])
    jun = _snap("June", [("G.10", "Gen 315", "2026-03-02", "2026-03-20")])
    sep = _snap("Sept", [("G.20", "Gen 316", "2026-04-01", "2026-04-20")])

    strict = sl.compare(base, [jun, sep])
    assert strict["rows"] == [], "nothing is common to all three"

    loose = sl.compare_pairwise(base, [jun, sep])
    row = next(r for r in loose["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["activities"] == 1
    assert row["Sept"]["activities"] == 1
    assert row["June"]["slip_days"] == 28
    assert row["Sept"]["slip_days"] == 59


def test_pairwise_still_refuses_to_count_added_work_as_slip():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    jun = _snap("June", [("G.10", "Gen 315", "2026-02-02", "2026-02-20"),
                         ("G.90", "Gen 390 added", "2027-01-04", "2027-06-30")])
    res = sl.compare_pairwise(base, [jun])
    row = next(r for r in res["rows"] if r["group"] == "Generator Rooms")
    assert row["June"]["slip_days"] == 0
    assert res["scope"]["June"]["not_in_baseline"] == 1


def test_pairwise_reports_what_each_column_rests_on():
    base = _snap("Contract", [("G.10", "Gen 315", "2026-02-02", "2026-02-20")])
    jun = _snap("June", [("G.10", "Gen 315", "2026-03-02", "2026-03-20")])
    res = sl.compare_pairwise(base, [jun])
    assert res["scope"]["June"]["shared_with_baseline"] == 1
    assert res["scope"]["Contract"]["total"] == 1


# ── the id tokens mean what this job means by them ──────────────────────────

def test_the_tokens_that_were_guessed_wrong_the_first_time():
    """
    Each of these was mislabelled by a table written from the token alone, and
    each was corrected against the five prints. FDG is funding, not
    foundations. SUB is a submittal, not a substation. U and UG are the slab
    pour sequence -- plumbing, electrical, form and tie rebar, pour -- and not
    underground utilities, which is what made a group read +285 days on three
    activities that had kept their numbers.
    """
    cases = [
        ("MDC1.FDG.1220", "Initial Funding Review and Apporval",
         "Funding & Pre-Construction"),
        ("MDC1.LLE.SUB.3230", "Long Lead Equipment Submittal Development",
         "Submittals"),
        ("MDC1.S.UG.L2.A1.3020", "Form & Tie Rebar", "Slabs & Pours"),
        ("MDC1.S.U.L2.A13.8020", "Pour Slab", "Slabs & Pours"),
        ("MDC1.SOD.L3.A11.4020", "Form & Tie Rebar", "Slabs & Pours"),
        ("MDC1.STR.UDG.2230", "Deep Foundations (Grid Line 18)",
         "Foundations & Underground"),
        ("MDC1.PH1.L.UP9.2140", "Chiller Lineup 1 - Set Equipment",
         "Equipment Lineups"),
        ("MDC3.GEN.PRO.1640", "Procurement / Fabrication: Precast",
         "Procurement"),
        ("MDC3.GEN.315.1000", "Overhead Fire Protection - GEN 315",
         "Generator Rooms"),
        ("MDC1.PH1.XFMR.1000", "Transformer C01 (2500KVA)",
         "Long Lead Equipment"),
        ("MDC3.EXT.SUT.1690", "Mobilize", "Site & Exterior Utilities"),
    ]
    for aid, name, want in cases:
        got, _ = sl.categorize(aid, name)
        assert got == want, f"{aid} {name!r} -> {got}, wanted {want}"


def test_a_lineup_and_a_floor_level_are_not_the_same_token():
    """
    Matched as a substring, "L" caught L1, L2 and L3 and swept every floor
    slab into the equipment lineups. The segment is matched whole.
    """
    assert sl.categorize("MDC1.PH1.L.UP9.2140", "Chiller Lineup 1")[0] \
        == "Equipment Lineups"
    assert sl.categorize("MDC3.ST.L1.A5.5000", "Pour Slab")[0] == "Slabs & Pours"


def test_generator_room_work_is_not_general_procurement():
    """
    GEN is both: GEN.PRO is general procurement and GEN.315 is a generator
    room. 13 of 472 were procurement, and they finish early, so they could not
    drive a group's last finish -- but they were still being counted in it.
    """
    assert sl.categorize("MDC1.GEN.PRO.1630", "Procurement: Deep Foundations")[0] \
        == "Procurement"
    assert sl.categorize("MDC3.GEN.327.1040", "Day Tank Piping")[0] \
        == "Generator Rooms"


# ── a milestone is a point in time, not a span ──────────────────────────────

def test_a_single_dated_row_is_a_point_in_time():
    """
    A zero-duration milestone is reported with one date, and the sources do not
    agree on which column it lands in: MDC-1's September workbook puts
    Substantial Completion (PH1) in Start on MDC1.PH1.C.OUT.4410 and in Finish
    on MDC1.MIL.PH1.1060, both 28-May-27. Compared on finish alone, the
    contract date read blank against a contract date that was right there.
    """
    snap = sl.Snapshot.from_rows(
        [{"activity_id": "A.10", "name": "Substantial Completion (PH1)",
          "start": "2027-05-28", "finish": None},
         {"activity_id": "B.10", "name": "Temporary Certificate of Occupancy",
          "start": None, "finish": "2027-05-28"}], "GC")
    assert snap.rows["A.10"]["finish"] == "2027-05-28"
    assert snap.rows["B.10"]["start"] == "2027-05-28"


def test_a_milestone_slip_is_measured_even_from_the_other_column():
    base = sl.Snapshot.from_rows(
        [{"activity_id": "M.10", "name": "Substantial Completion (PH1)",
          "start": "2027-03-15", "finish": "2027-03-15"}], "Contract")
    upd = sl.Snapshot.from_rows(
        [{"activity_id": "M.10", "name": "Substantial Completion (PH1)",
          "start": "2027-05-28", "finish": None}], "GC September")
    res = sl.compare(base, [upd])
    row = next(r for r in res["rows"] if r["group"] == "Completion Milestones")
    assert row["GC September"]["slip_days"] == 74


def test_a_span_is_not_turned_into_a_point():
    snap = sl.Snapshot.from_rows(
        [{"activity_id": "A.10", "name": "Pull wire", "start": "2026-02-02",
          "finish": "2026-02-20"}], "X")
    assert snap.rows["A.10"]["start"] == "2026-02-02"
    assert snap.rows["A.10"]["finish"] == "2026-02-20"
