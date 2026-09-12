"""
test_resource_restore.py — putting the labour back after it was lost.

This app used to drop resource assignments on import and write every
activity's actual and remaining labour out as a hard zero. Both are fixed, but
the files that went through in the meantime came out stripped and a schedule
cannot be un-round-tripped. What is left is a job with dates, progress and
logic intact and nothing behind any of it.

Two sources, and they are not equally good. A donor export that still has the
assignments is real data. A crew count typed into a UDF is not a resource
assignment at all — P6's profile cannot read it — but it is the headcount
somebody meant, and units = crew x duration turns it into one.

So: keep what is there, take the donor's where it reaches, derive the rest
from the crew count, and never invent a number for an activity that has
neither. The last one is the whole point — a default of one electrician would
look like data.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.resource_restore import (audit, crew_field_of, plan, restore)
from engine.schedule_model import (Activity, Calendar, Project, Resource,
                                   ResourceAssignment, WBSNode)

CREW = "Number of Electricians"


def _job(crews=(3, 5, None), statuses=None, dur=40, field=CREW):
    """Three activities; crews[i] is the headcount typed on each, or None."""
    statuses = statuses or ["Not Started"] * len(crews)
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
    p.activities = []
    for i, (c, st) in enumerate(zip(crews, statuses), start=1):
        a = Activity(uid=f"u{i}", activity_id=f"A{i}0", name=f"Step {i}",
                     wbs_uid="w", calendar_uid="1", status=st,
                     planned_duration=dur, remaining_duration=dur,
                     planned_start="2026-02-02", planned_finish="2026-02-06")
        if c is not None and field:
            a.udfs = {field: str(c)}
        p.activities.append(a)
    p.relations = []
    p.build_lookups()
    return p


def _donor(units_by_aid, res_id="ELEC-JW"):
    p = _job(crews=(None, None, None))
    p.resources = [Resource(uid="r1", id=res_id, name="Journeyman Electrician",
                            type="Labor", max_units=8, rate=72.5)]
    p.resource_assignments = []
    for i, a in enumerate(p.activities, start=1):
        u = units_by_aid.get(a.activity_id)
        if u is None:
            continue
        p.resource_assignments.append(ResourceAssignment(
            uid=f"ra{i}", activity_uid=a.uid, resource_uid="r1",
            planned_units=u[0], actual_units=u[1], remaining_units=u[2],
            rate=72.5))
    return p


def _units(project, aid):
    a = project.get_activity(activity_id=aid)
    return (a.planned_labor_units, a.actual_labor_units, a.remaining_labor_units)


# ── finding the crew field ───────────────────────────────────────────────────

def test_the_crew_field_is_found_by_name():
    assert crew_field_of(_job()) == CREW


def test_a_job_that_calls_it_something_else_is_still_found():
    assert crew_field_of(_job(field="Crew Size")) == "Crew Size"


def test_a_text_field_that_is_not_a_headcount_is_not_mistaken_for_one():
    p = _job(crews=(None, None, None))
    for a in p.activities:
        a.udfs = {"COMMENTS": "waiting on the owner"}
    assert crew_field_of(p) is None


def test_a_job_with_no_udfs_reports_none():
    assert crew_field_of(_job(field=None)) is None


# ── the audit ────────────────────────────────────────────────────────────────

def test_the_audit_counts_what_can_be_recovered_from_crew_counts():
    a = audit(_job(crews=(3, 5, None)))
    assert a["with_crew_count"] == 2
    assert a["recoverable_from_crew"] == 2


def test_the_audit_names_the_real_problem():
    """Work already under way with no record of what it cost — which is what
    an emptied usage profile actually is."""
    p = _job(crews=(3, None, None), statuses=["Completed", "Completed", "Not Started"])
    for x in p.activities[:2]:
        x.actual_start = "2026-01-06"
    a = audit(p)
    assert a["started"] == 2
    assert a["started_without_actual"] == 2


def test_the_audit_spots_a_roll_up_with_nothing_under_it():
    """The fingerprint of the loss: P6 keeps the activity total as a roll-up
    of its assignments, so a total with no rows beneath it is one whose rows
    were taken away."""
    p = _job()
    p.activities[0].planned_labor_units = 96
    a = audit(p)
    assert a["orphaned_budget"] == 1
    assert a["orphaned_sample"] == ["A10"]


def test_milestones_are_not_counted_as_missing_labour():
    p = _job()
    p.activities[2].activity_type = "Finish Milestone"
    assert audit(p)["activities"] == 2


# ── deriving from a crew count ───────────────────────────────────────────────

def test_a_crew_count_becomes_hours_by_multiplying_the_duration():
    p = _job(crews=(3, None, None), dur=40)
    restore(p)
    assert _units(p, "A10")[0] == 120     # 3 men x 40 hours


def test_an_activity_with_no_crew_count_is_left_alone():
    """A default of one electrician would look like data."""
    p = _job(crews=(3, None, None))
    ok, msg, det = restore(p)
    assert _units(p, "A20") == (0, 0, 0)
    assert det["counts"]["none"] == 2
    assert "left alone" in msg


def test_not_started_work_is_all_remaining():
    """Nothing has been spent on it, and remaining is the half of the profile
    in front of the data date."""
    p = _job(crews=(3, None, None))
    restore(p)
    assert _units(p, "A10") == (120, 0, 120)


def test_completed_work_is_all_spent():
    p = _job(crews=(3, None, None), statuses=["Completed", "Not Started", "Not Started"])
    restore(p)
    assert _units(p, "A10") == (120, 120, 0)


def test_work_in_progress_is_cut_at_its_percentage():
    p = _job(crews=(3, None, None), statuses=["In Progress", "Not Started", "Not Started"])
    p.activities[0].percent_complete = 0.25
    restore(p)
    assert _units(p, "A10") == (120, 30, 90)


def test_percent_complete_is_read_as_a_fraction():
    """It is 0..1 here, not 0..100. Read as a whole number it would put a
    hundred times the hours on the wrong side of the data date."""
    p = _job(crews=(1, None, None), dur=8,
             statuses=["In Progress", "Not Started", "Not Started"])
    p.activities[0].percent_complete = 0.5
    assert _units(p, "A10")[0] == 0
    restore(p)
    assert _units(p, "A10") == (8, 4, 4)


def test_an_assignment_is_written_not_just_the_activity_total():
    """The profile reads assignments; the grid reads the total. Writing one
    without the other is how the schedule ended up with roll-ups that had
    nothing underneath them."""
    p = _job(crews=(3, None, None))
    restore(p)
    assert len(p.resource_assignments) == 1
    assert p.resource_assignments[0].planned_units == 120


def test_the_derived_crew_is_a_labor_resource():
    p = _job(crews=(3, None, None))
    restore(p, resource_id="ELEC", resource_name="Electrician")
    r = p.resources[0]
    assert (r.id, r.name, r.type) == ("ELEC", "Electrician", "Labor")


# ── taking it from a donor ───────────────────────────────────────────────────

def test_the_donor_supplies_the_real_numbers():
    p = _job(crews=(None, None, None))
    d = _donor({"A10": (320, 80, 240)})
    restore(p, donor=d)
    assert _units(p, "A10") == (320, 80, 240)


def test_the_donor_beats_a_crew_count_where_both_reach():
    """Real data over a derived one, every time."""
    p = _job(crews=(3, None, None), dur=40)      # would derive 120
    d = _donor({"A10": (320, 80, 240)})
    ok, msg, det = restore(p, donor=d)
    assert _units(p, "A10") == (320, 80, 240)
    assert det["made"] == {"donor": 1, "crew": 0}


def test_a_crew_count_fills_in_where_the_donor_does_not_reach():
    p = _job(crews=(None, 5, None), dur=40)
    d = _donor({"A10": (320, 80, 240)})
    _, _, det = restore(p, donor=d)
    assert det["made"] == {"donor": 1, "crew": 1}
    assert _units(p, "A20")[0] == 200


def test_the_donors_resource_comes_across_by_name():
    """A restored assignment should name the crew it was actually on, not a
    generic line."""
    p = _job(crews=(None, None, None))
    restore(p, donor=_donor({"A10": (320, 80, 240)}))
    assert any(r.id == "ELEC-JW" for r in p.resources)


def test_a_donor_that_lost_its_own_assignments_still_gives_its_totals():
    """The donor went through the same stripping; its activity roll-ups are
    the last thing standing and are still worth having."""
    p = _job(crews=(None, None, None))
    d = _job(crews=(None, None, None))
    d.get_activity(activity_id="A10").planned_labor_units = 500
    restore(p, donor=d)
    assert _units(p, "A10")[0] == 500


def test_the_donor_is_matched_on_activity_id_not_uid():
    p = _job(crews=(None, None, None))
    d = _donor({"A10": (320, 80, 240)})
    for i, a in enumerate(d.activities):
        a.uid = f"totally-different-{i}"
    for ra in d.resource_assignments:
        ra.activity_uid = "totally-different-0"
    d.build_lookups()
    restore(p, donor=d)
    assert _units(p, "A10") == (320, 80, 240)


# ── it is safe to run twice ──────────────────────────────────────────────────

def test_work_that_already_has_an_assignment_is_not_touched():
    p = _job(crews=(3, None, None))
    restore(p)
    first = _units(p, "A10")
    p.activities[0].udfs = {CREW: "99"}     # someone retypes the crew
    restore(p)
    assert _units(p, "A10") == first
    assert len(p.resource_assignments) == 1, "a second assignment was stacked on"


def test_a_second_pass_fills_only_what_the_first_could_not():
    p = _job(crews=(3, None, None))
    restore(p)
    p.activities[1].udfs = {CREW: "4"}
    _, _, det = restore(p)
    assert det["made"]["crew"] == 1
    assert len(p.resource_assignments) == 2


def test_overwrite_replaces_rather_than_stacking():
    p = _job(crews=(3, None, None))
    restore(p)
    p.activities[0].udfs = {CREW: "6"}
    restore(p, overwrite=True)
    assert len(p.resource_assignments) == 1
    assert _units(p, "A10")[0] == 240


# ── the plan says what would happen, before it happens ───────────────────────

def test_the_plan_changes_nothing():
    p = _job(crews=(3, 5, None))
    plan(p)
    assert p.resource_assignments == [] and p.resources == []


def test_the_plan_says_where_each_activity_would_get_its_labour():
    p = _job(crews=(None, 5, None))
    got = plan(p, donor=_donor({"A10": (320, 80, 240)}))
    assert got["counts"] == {"donor": 1, "crew": 1, "none": 1}
    assert got["uncovered"] == ["A30"]


def test_the_plan_totals_the_hours_by_source():
    p = _job(crews=(3, 5, None), dur=40)
    assert plan(p)["hours"]["crew"] == 320.0     # 120 + 200


# ── the endpoints ────────────────────────────────────────────────────────────

def _serve(project, pid="j"):
    import server
    server._projects.clear()
    server._projects[pid] = server._make_session(pid, f"{pid}.xml")
    server._projects[pid]["project"] = project
    server._active_id[0] = pid
    return server


def test_the_audit_endpoint_reports_without_changing_anything():
    p = _job(crews=(3, 5, None))
    server = _serve(p)
    d = server.app.test_client().get("/api/resources/audit").get_json()
    assert d["audit"]["with_crew_count"] == 2
    assert p.resource_assignments == []


def test_the_restore_endpoint_reports_by_default():
    p = _job(crews=(3, None, None))
    server = _serve(p)
    d = server.app.test_client().post("/api/resources/restore", json={}).get_json()
    assert d["applied"] is False
    assert p.resource_assignments == [], "a report changed the schedule"


def test_the_restore_endpoint_applies_when_told_to():
    p = _job(crews=(3, None, None))
    server = _serve(p)
    d = server.app.test_client().post("/api/resources/restore",
                                      json={"apply": True}).get_json()
    assert d["applied"] and d["undo_count"] >= 1
    assert len(p.resource_assignments) == 1


def test_the_restore_endpoint_takes_a_donor_by_project_id():
    import server
    p = _job(crews=(None, None, None))
    server = _serve(p)
    server._projects["old"] = server._make_session("old", "old.xml")
    server._projects["old"]["project"] = _donor({"A10": (320, 80, 240)})
    d = server.app.test_client().post(
        "/api/resources/restore",
        json={"donor_project_id": "old", "apply": True}).get_json()
    assert d["applied"] and _units(p, "A10") == (320, 80, 240)


def test_a_schedule_cannot_be_its_own_donor():
    server = _serve(_job())
    r = server.app.test_client().post("/api/resources/restore",
                                      json={"donor_project_id": "j"})
    assert r.status_code == 400


def test_an_unknown_donor_is_refused():
    server = _serve(_job())
    r = server.app.test_client().post("/api/resources/restore",
                                      json={"donor_project_id": "nope"})
    assert r.status_code == 404


# ── a donor whose activity ids no longer line up ─────────────────────────────
#
# Ids are not sacred. Renaming them is a thing people do — this app has a tool
# for exactly that — so a donor exported before a renumbering would match
# nothing at all and read as an empty file. Name plus folder is the fallback,
# the same one compare_projects uses, and how each row matched is reported so
# a donor that matched mostly on name can be judged rather than trusted.

def _renamed(donor_units):
    """A donor with the right work under different ids."""
    d = _donor(donor_units)
    for i, a in enumerate(d.activities):
        a.activity_id = f"OLD{i}"
    d.build_lookups()
    return d


def test_a_donor_whose_ids_were_renumbered_still_matches_on_name():
    p = _job(crews=(None, None, None))
    restore(p, donor=_renamed({"A10": (320, 80, 240)}))
    assert _units(p, "A10") == (320, 80, 240)


def test_the_plan_says_how_many_matched_on_name_rather_than_id():
    """A donor matched mostly on name is a fact about the two files that the
    user should get to see, not one to bury."""
    p = _job(crews=(None, None, None))
    got = plan(p, donor=_renamed({"A10": (320, 80, 240)}))
    assert got["matched_by_name"] == 1
    assert got["counts"]["donor"] == 1


def test_a_clean_id_match_is_not_reported_as_a_name_match():
    p = _job(crews=(None, None, None))
    assert plan(p, donor=_donor({"A10": (320, 80, 240)}))["matched_by_name"] == 0


def test_the_id_is_preferred_when_both_could_match():
    """Otherwise a renamed activity could steal another one's hours."""
    p = _job(crews=(None, None, None))
    d = _donor({"A10": (320, 80, 240), "A20": (100, 0, 100)})
    d.get_activity(activity_id="A20").name = "Step 1"   # same name as A10
    d.build_lookups()
    restore(p, donor=d)
    assert _units(p, "A10")[0] == 320
    assert _units(p, "A20")[0] == 100


def test_a_name_match_in_a_different_folder_is_not_accepted():
    """Two folders often carry the same activity name — "Conduit Rough In" is
    in every room. Matching on the name alone would scatter hours at random."""
    p = _job(crews=(None, None, None))
    d = _renamed({"A10": (320, 80, 240)})
    d.wbs_nodes = [WBSNode(uid="w", name="Somewhere Else", code="B")]
    d.build_lookups()
    restore(p, donor=d)
    assert _units(p, "A10") == (0, 0, 0)


# ── loading a donor adds what it has and leaves the rest alone ───────────────
#
# The question this was built for: upload the older file, take the assignments
# it carries, skip anything already covered here.

def test_a_donor_fills_only_what_is_missing():
    p = _job(crews=(None, None, None))
    restore(p, donor=_donor({"A10": (320, 80, 240)}))       # first pass
    assert len(p.resource_assignments) == 1

    bigger = _donor({"A10": (999, 0, 999), "A20": (100, 0, 100)})
    _, _, det = restore(p, donor=bigger)                     # second pass
    assert det["counts"]["keep"] == 1, "the covered activity was not skipped"
    assert _units(p, "A10")[0] == 320, "an existing assignment was overwritten"
    assert _units(p, "A20")[0] == 100, "the new one was not picked up"


def test_a_donor_that_covers_nothing_new_changes_nothing():
    p = _job(crews=(None, None, None))
    d = _donor({"A10": (320, 80, 240)})
    restore(p, donor=d)
    before = [(a.planned_labor_units, a.actual_labor_units) for a in p.activities]
    _, msg, det = restore(p, donor=d)
    assert det["made"] == {"donor": 0, "crew": 0}
    assert [(a.planned_labor_units, a.actual_labor_units) for a in p.activities] == before


def test_two_donors_can_be_layered():
    """One older file covers the history, another the rest — neither has to
    cover everything, and the second does not disturb the first."""
    p = _job(crews=(None, None, None))
    restore(p, donor=_donor({"A10": (320, 80, 240)}))
    restore(p, donor=_donor({"A20": (100, 0, 100)}))
    assert _units(p, "A10") == (320, 80, 240)
    assert _units(p, "A20") == (100, 0, 100)
    assert len(p.resource_assignments) == 2


# ── will this donor actually fill the history? ───────────────────────────────
#
# The question a donor is chosen to answer. Work already under way is the half
# a crew count cannot reach — a headcount typed for planning is rarely on
# activities that are already finished — and it is invisible in a total
# dominated by future work.

def _with_history(crews=(None, None, None)):
    p = _job(crews=crews, statuses=["Completed", "In Progress", "Not Started"])
    p.activities[0].actual_start = "2026-01-06"
    p.activities[1].actual_start = "2026-01-08"
    return p


def test_the_plan_counts_how_much_of_the_history_is_covered():
    p = _with_history()
    got = plan(p, donor=_donor({"A10": (320, 320, 0)}))
    assert (got["started_covered"], got["started_total"]) == (1, 2)


def test_a_donor_that_reaches_no_started_work_says_so():
    """The honest answer to "will this fill my profile" is sometimes no, and
    it should be readable before anything is written."""
    p = _with_history()
    got = plan(p, donor=_donor({"A30": (100, 0, 100)}))
    assert got["counts"]["donor"] == 1
    assert got["started_covered"] == 0, "future-only cover looked like history"


def test_crew_counts_alone_can_cover_history_when_they_are_there():
    p = _with_history(crews=(3, 4, None))
    got = plan(p)
    assert got["started_covered"] == 2


def test_a_schedule_that_has_not_started_reports_no_history_to_fill():
    assert plan(_job())["started_total"] == 0
