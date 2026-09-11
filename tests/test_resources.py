"""
test_resources.py — what P6 sent has to be what P6 gets back.

Resources were dropped on the floor. Neither reader kept them, the model had
nowhere to put them, and the writer made up ONE synthetic "Costs MLCB"
nonlabor line and pointed every assignment at it. So a schedule that arrived
with six crews, their rates and their budgeted hours came back with six
assignments to a cost account nobody had ever created — and the only trace of
the original was a per-activity total rolled up from the hours.

That is silent loss on a round trip: nothing errors, nothing warns, and it is
only visible if you go looking in P6 afterwards. These hold the whole path
open — XER in, XML in, XML out, and back in again with the same numbers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.schedule_model import (Activity, Calendar, Project, Resource,
                                   ResourceAssignment, WBSNode)
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml


def _job(resources=True):
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
    p.activities = [Activity(uid="u1", activity_id="A10", name="Pull wire",
                             wbs_uid="w", calendar_uid="1",
                             planned_duration=40, remaining_duration=40,
                             planned_start="2026-02-02",
                             planned_finish="2026-02-06")]
    p.relations = []
    if resources:
        p.resources = [
            Resource(uid="r1", id="ELEC-JW", name="Journeyman Electrician",
                     type="Labor", max_units=8, rate=72.5),
            Resource(uid="r2", id="LIFT", name="Scissor Lift",
                     type="Nonlabor", max_units=1, rate=40.0),
        ]
        p.resource_assignments = [
            ResourceAssignment(uid="ra1", activity_uid="u1", resource_uid="r1",
                               planned_units=320, actual_units=80,
                               planned_cost=23200, actual_cost=5800, rate=72.5),
            ResourceAssignment(uid="ra2", activity_uid="u1", resource_uid="r2",
                               planned_units=40, planned_cost=1600, rate=40.0),
        ]
    p.build_lookups()
    return p


@pytest.fixture
def roundtrip(tmp_path):
    def _go(project, name="out.xml"):
        path = str(tmp_path / name)
        write_p6_xml(project, path)
        return load_xml(path), path
    return _go


# ── the library survives ─────────────────────────────────────────────────────

def test_every_resource_comes_back(roundtrip):
    back, _ = roundtrip(_job())
    assert {r.id for r in back.resources} == {"ELEC-JW", "LIFT"}


def test_a_resource_comes_back_as_itself_not_as_a_cost_account(roundtrip):
    """The export used to write one synthetic nonlabor line called
    "Costs MLCB" and call everything that."""
    back, path = roundtrip(_job())
    jw = next(r for r in back.resources if r.id == "ELEC-JW")
    assert jw.name == "Journeyman Electrician"
    assert jw.type == "Labor", "a crew came back as something else"
    assert "Costs MLCB" not in open(path, encoding="utf-8").read()


def test_the_rate_and_availability_survive(roundtrip):
    back, _ = roundtrip(_job())
    jw = next(r for r in back.resources if r.id == "ELEC-JW")
    assert jw.rate == 72.5
    assert jw.max_units == 8, "P6 reads availability off the rate block"


def test_a_nonlabor_resource_stays_nonlabor(roundtrip):
    back, _ = roundtrip(_job())
    assert next(r for r in back.resources if r.id == "LIFT").type == "Nonlabor"


# ── the assignments survive, pointed at the right resource ───────────────────

def test_every_assignment_comes_back(roundtrip):
    back, _ = roundtrip(_job())
    assert len(back.resource_assignments) == 2


def test_each_assignment_points_at_the_resource_it_came_in_on(roundtrip):
    """Everything used to be pinned to one made-up resource, so two
    assignments on one activity were indistinguishable."""
    back, _ = roundtrip(_job())
    by_uid = {r.uid: r.id for r in back.resources}
    got = {by_uid[a.resource_uid]: a.planned_units for a in back.resource_assignments}
    assert got == {"ELEC-JW": 320, "LIFT": 40}


def test_the_budgeted_hours_survive(roundtrip):
    back, _ = roundtrip(_job())
    assert sum(a.planned_units for a in back.resource_assignments) == 360


def test_the_actuals_survive(roundtrip):
    """They were written as a hard zero. On a job part-built that is the
    difference between what has been spent and a clean sheet."""
    back, _ = roundtrip(_job())
    assert sum(a.actual_units for a in back.resource_assignments) == 80
    assert sum(a.actual_cost for a in back.resource_assignments) == 5800


def test_the_costs_survive(roundtrip):
    back, _ = roundtrip(_job())
    assert sum(a.planned_cost for a in back.resource_assignments) == 24800


# ── the schedule with no resources is unchanged ──────────────────────────────

def test_a_schedule_with_no_resources_writes_none(roundtrip):
    """The stub existed to make assignments importable. With no assignments
    there is nothing to make importable, and an orphan resource block is
    clutter in someone's enterprise library."""
    back, path = roundtrip(_job(resources=False))
    assert back.resources == [] and back.resource_assignments == []
    assert "<Resource>" not in open(path, encoding="utf-8").read()


def test_the_rest_of_the_schedule_is_unaffected(roundtrip):
    back, _ = roundtrip(_job())
    assert len(back.activities) == 1
    assert back.activities[0].activity_id == "A10"
    assert back.activities[0].planned_start == "2026-02-02"


# ── reading an XER ───────────────────────────────────────────────────────────

_XER = """ERMHDR\t19.12\t2026-01-05\tProject\tadmin\tadmin\tdbxDatabaseNoName\tProject Management\tUSD
%T\tPROJECT
%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date
%R\t1\tJ1\t2026-01-05 00:00\t2026-01-05 00:00
%T\tCALENDAR
%F\tclndr_id\tclndr_name\tday_hr_cnt\tclndr_data
%R\t1\tStandard\t8\t
%T\tPROJWBS
%F\twbs_id\tproj_id\twbs_short_name\twbs_name\tparent_wbs_id\tproj_node_flag
%R\t10\t1\tA\tArea\t\tN
%T\tTASK
%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\tclndr_id\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\ttarget_start_date\ttarget_end_date
%R\t100\t1\t10\tA10\tPull wire\tTT_Task\tTK_NotStart\t1\t40\t40\t2026-02-02 08:00\t2026-02-06 17:00
%T\tRSRC
%F\trsrc_id\trsrc_short_name\trsrc_name\trsrc_type\tclndr_id\tdef_qty_per_hr\tparent_rsrc_id\tactive_flag
%R\t500\tELEC-JW\tJourneyman Electrician\tRT_Labor\t1\t8\t\tY
%R\t501\tLIFT\tScissor Lift\tRT_Equip\t1\t1\t\tY
%T\tRSRCRATE
%F\trsrc_rate_id\trsrc_id\tcost_per_qty
%R\t900\t500\t72.5
%R\t901\t501\t40
%T\tTASKRSRC
%F\ttaskrsrc_id\ttask_id\tproj_id\trsrc_id\ttarget_qty\tact_reg_qty\tremain_qty\ttarget_cost\tact_reg_cost\tcost_per_qty
%R\t700\t100\t1\t500\t320\t80\t240\t23200\t5800\t72.5
%R\t701\t100\t1\t501\t40\t0\t40\t1600\t0\t40
%E
"""


@pytest.fixture
def xer(tmp_path):
    p = tmp_path / "job.xer"
    p.write_text(_XER, encoding="utf-8")
    from engine.xer_reader import load_xer
    return load_xer(str(p))


def test_an_xer_brings_its_resource_library(xer):
    assert {r.id for r in xer.resources} == {"ELEC-JW", "LIFT"}


def test_an_xer_resource_type_is_translated(xer):
    kinds = {r.id: r.type for r in xer.resources}
    assert kinds == {"ELEC-JW": "Labor", "LIFT": "Nonlabor"}


def test_an_xer_rate_is_read_from_its_own_table(xer):
    assert next(r for r in xer.resources if r.id == "ELEC-JW").rate == 72.5


def test_an_xer_brings_its_assignments(xer):
    assert len(xer.resource_assignments) == 2
    by_uid = {r.uid: r.id for r in xer.resources}
    got = {by_uid[a.resource_uid]: a.planned_units for a in xer.resource_assignments}
    assert got == {"ELEC-JW": 320, "LIFT": 40}


def test_the_per_activity_roll_up_still_works(xer):
    """It predates the assignments and the grid reads it. Keeping the
    assignments must not cost the total."""
    assert xer.activities[0].planned_labor_units == 360


def test_an_xer_round_trips_through_the_exporter(roundtrip, xer):
    """The whole point: XER in, XML out, same crews and same hours."""
    back, _ = roundtrip(xer)
    assert {r.id for r in back.resources} == {"ELEC-JW", "LIFT"}
    assert sum(a.planned_units for a in back.resource_assignments) == 360
    assert sum(a.actual_units for a in back.resource_assignments) == 80


# ── nothing dangling ─────────────────────────────────────────────────────────

def test_an_assignment_to_an_activity_that_is_gone_is_not_written(roundtrip):
    """It loads and then fails to schedule — the same reason a relation
    pointing at nothing is dropped."""
    p = _job()
    p.resource_assignments.append(
        ResourceAssignment(uid="ra9", activity_uid="ghost", resource_uid="r1",
                           planned_units=10))
    back, _ = roundtrip(p)
    assert len(back.resource_assignments) == 2


def test_an_assignment_to_a_resource_that_is_gone_is_not_read_back(roundtrip):
    p = _job()
    p.resource_assignments.append(
        ResourceAssignment(uid="ra9", activity_uid="u1", resource_uid="ghost",
                           planned_units=10))
    back, _ = roundtrip(p)
    assert all(a.resource_uid in {r.uid for r in back.resources}
               for a in back.resource_assignments)


def test_a_parent_outside_the_library_is_written_as_no_parent(roundtrip):
    """A reference P6 cannot resolve fails the import; no parent is merely
    less than P6 had."""
    p = _job()
    p.resources[0].parent_uid = "not-in-this-file"
    back, _ = roundtrip(p)
    assert next(r for r in back.resources if r.id == "ELEC-JW").parent_uid is None


# ── the three labour numbers on the activity itself ──────────────────────────
#
# P6 keeps Budgeted, Actual and Remaining Labor Units separately, and the
# usage profile plots the last two: actual behind the data date, remaining in
# front of it. The exporter wrote both out as a hard "0" and the reader never
# read them, so every round trip erased the history of a job already under way
# and forecast no labour at all — while keeping the budget, which made the
# result look populated. This is what produced a profile that started at the
# data date with nothing behind it.

def _underway():
    p = _job(resources=False)
    a = p.activities[0]
    a.status, a.percent_complete = "In Progress", 0.25
    a.actual_start, a.remaining_duration = "2026-01-02", 30
    a.planned_labor_units = 320
    a.actual_labor_units = 80
    a.remaining_labor_units = 240
    return p


def test_hours_already_spent_survive_the_export(roundtrip):
    back, _ = roundtrip(_underway())
    assert back.activities[0].actual_labor_units == 80


def test_hours_still_to_spend_survive_the_export(roundtrip):
    back, _ = roundtrip(_underway())
    assert back.activities[0].remaining_labor_units == 240


def test_the_budget_still_survives(roundtrip):
    """It was the one field that did. Keeping the other two must not cost it."""
    back, _ = roundtrip(_underway())
    assert back.activities[0].planned_labor_units == 320


def test_at_completion_is_spent_plus_remaining(roundtrip):
    """P6's own definition. Left at zero it contradicted the other two."""
    import re
    _, path = roundtrip(_underway())
    got = re.search(r"<AtCompletionLaborUnits>([^<]*)<",
                    open(path, encoding="utf-8").read()).group(1)
    assert float(got) == 320


def test_an_activity_with_no_hours_still_writes_zeroes(roundtrip):
    back, _ = roundtrip(_job(resources=False))
    a = back.activities[0]
    assert (a.actual_labor_units, a.remaining_labor_units) == (0, 0)


def test_an_xer_rolls_up_all_three_not_just_the_budget(xer):
    a = xer.activities[0]
    assert a.planned_labor_units == 360, "the budget roll-up changed"
    assert a.actual_labor_units == 80
    assert a.remaining_labor_units == 280


def test_an_xer_round_trip_keeps_the_history(roundtrip, xer):
    back, _ = roundtrip(xer)
    assert back.activities[0].actual_labor_units == 80
    assert back.activities[0].remaining_labor_units == 280
