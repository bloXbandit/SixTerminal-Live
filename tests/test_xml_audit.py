"""
test_xml_audit.py — the file has to hold together before P6 sees it.

P6 does not fail an import over a broken reference. It writes a line into the
import log —

  Referenced business object Calendar ... cannot be found,
  ignoring field CalendarObjectId

— and carries on. The file imports, a dialog says it worked, and an activity
quietly has no calendar.

Four bugs of exactly that shape turned up in one week on this exporter:
resources dropped, actual and remaining labour written as zero, calendars
replaced with canned ones, ObjectIds fabricated. Every one imported cleanly.
The only way any of them was found was by going and looking in P6 afterwards.

So every reference is checked against the file that carries it, as an
invariant across every shape of schedule rather than case by case. This is not
schema validation — element order and required fields are the XSD's business —
but it is the class of error a schema would pass anyway.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.calendars import WORKWEEK_6_DAY
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   Resource, ResourceAssignment, WBSNode)
from engine.xml_audit import (audit, audit_project, describe, duplicate_ids,
                              reference_problems)


def _job(**kw):
    p = Project(uid="1", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="6604", name="P5-DAY NO HOL", type="Project")]
    p.wbs_nodes = [WBSNode(uid="root", name="J", code="J"),
                   WBSNode(uid="w", name="Area", code="A", parent_uid="root")]
    p.activities = [
        Activity(uid=f"a{i}", activity_id=f"A{i}0", name=f"Task {i}", wbs_uid="w",
                 calendar_uid="6604", planned_duration=40, remaining_duration=40,
                 planned_start="2026-02-02", planned_finish="2026-02-06")
        for i in (1, 2)]
    p.relations = [Relation(uid="r1", predecessor_uid="a1", successor_uid="a2")]
    for k, v in kw.items():
        setattr(p, k, v)
    p.build_lookups()
    return p


def _ok(project, **kw):
    r = audit_project(project, **kw)
    assert r["ok"], describe(r)
    return r


# ── the shapes a real job comes in ───────────────────────────────────────────

def test_an_ordinary_schedule_holds_together():
    _ok(_job())


def test_a_schedule_with_its_own_calendars_holds_together():
    p = _job()
    p.calendars = [Calendar(uid="6604", name="P5-DAY NO HOL", type="Project"),
                   Calendar(uid="c6", name="6-Day 10hr", type="Project",
                            work_days=WORKWEEK_6_DAY, hours_per_day=10.0)]
    p.build_lookups()
    _ok(p)


def test_a_project_calendar_carrying_a_global_id_is_not_declared_twice():
    """
    The collision this check was written and immediately earned its keep on.

    The global calendar block is written from fixed ids. A project calendar
    that arrived holding one of those was kept as it was, so the same ObjectId
    was declared twice and P6 resolved every reference to whichever it loaded
    last — an activity on a calendar nobody assigned it.
    """
    p = _job()
    p.calendars = [Calendar(uid="6590", name="Looks global but is not",
                            type="Project")]
    for a in p.activities:
        a.calendar_uid = "6590"
    p.build_lookups()
    r = _ok(p)
    assert r["counts"]["Calendar"] >= 1


def test_a_schedule_with_resources_holds_together():
    p = _job()
    p.resources = [Resource(uid="4521", id="MDC-ELEC", name="MDC Electrician",
                            type="Labor", max_units=8, rate=72.5)]
    p.resource_assignments = [
        ResourceAssignment(uid="ra1", activity_uid="a1", resource_uid="4521",
                           planned_units=320)]
    p.build_lookups()
    _ok(p)


def test_a_schedule_with_no_calendars_holds_together():
    """An app-built one, relying on the fixed set."""
    p = _job()
    p.calendars = []
    p.build_lookups()
    _ok(p)


def test_an_activity_naming_a_calendar_that_does_not_exist_holds_together():
    p = _job()
    p.activities[0].calendar_uid = "ghost"
    p.build_lookups()
    _ok(p)


def test_an_empty_schedule_holds_together():
    p = _job()
    p.activities, p.relations = [], []
    p.build_lookups()
    _ok(p)


def test_the_subject_schedule_holds_together():
    """2,393 activities, 2,105 ties, 256 folders, six calendars."""
    src = ("/root/.claude/uploads/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/"
           "e0540941-251539INT3_edited_edited2.xml")
    if not os.path.exists(src):
        pytest.skip("the reference schedule is not on this machine")
    from engine.xml_reader import load_xml
    _ok(load_xml(src))


# ── the check itself has to be able to fail ──────────────────────────────────

def test_a_dangling_reference_is_caught():
    bad = ("<Root><Activity><ObjectId>1</ObjectId>"
           "<CalendarObjectId>999</CalendarObjectId></Activity></Root>")
    probs = reference_problems(bad)
    assert probs and probs[0]["field"] == "CalendarObjectId"
    assert probs[0]["value"] == "999"


def test_one_bad_id_on_two_thousand_rows_is_one_problem():
    """A single wrong calendar repeated across a schedule is one thing to fix,
    not two thousand — a report nobody can read is a report nobody reads."""
    rows = "".join(f"<Activity><ObjectId>{i}</ObjectId>"
                   f"<CalendarObjectId>77777</CalendarObjectId></Activity>"
                   for i in range(2000))
    probs = reference_problems(f"<Root>{rows}</Root>")
    assert len(probs) == 1 and probs[0]["occurrences"] == 2000


def test_a_reference_that_is_meant_to_leave_the_file_is_not_flagged():
    """ParentEPSObjectId names an EPS node that must already exist in the
    target database. That is the design, and it is settable per environment."""
    xml = ("<Root><Project><ObjectId>1</ObjectId>"
           "<ParentEPSObjectId>3063</ParentEPSObjectId></Project></Root>")
    assert reference_problems(xml) == []


def test_a_duplicate_object_id_is_caught():
    xml = ("<Root><Calendar><ObjectId>5</ObjectId></Calendar>"
           "<Calendar><ObjectId>5</ObjectId></Calendar></Root>")
    dups = duplicate_ids(xml)
    assert dups and dups[0]["count"] == 2


def test_the_same_id_on_two_different_kinds_of_element_is_fine():
    """P6 scopes ObjectIds per business object, so an Activity 5 and a
    Calendar 5 are not in conflict."""
    xml = ("<Root><Calendar><ObjectId>5</ObjectId></Calendar>"
           "<Activity><ObjectId>5</ObjectId></Activity></Root>")
    assert duplicate_ids(xml) == []


def test_a_clean_file_says_so():
    xml = ("<Root><Calendar><ObjectId>5</ObjectId></Calendar>"
           "<Activity><ObjectId>1</ObjectId>"
           "<CalendarObjectId>5</CalendarObjectId></Activity></Root>")
    r = audit(xml)
    assert r["ok"] and describe(r) == "Every reference in this file resolves."


# ── the endpoint ─────────────────────────────────────────────────────────────

def _serve(project=None):
    import server
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = project or _job()
    server._active_id[0] = "J"
    return server.app.test_client()


def test_the_export_check_reports_references_when_asked_for_them():
    d = _serve().get("/api/export/check?references=1").get_json()
    assert d["success"]
    assert d["reference_count"] == 0
    assert d["reference_problems"] == []


def test_the_export_button_does_not_pay_for_the_audit():
    """
    Finding a dangling reference means writing the whole file and parsing it
    back — 6.6 seconds on a 2,400-activity job, and worse on the host. This
    endpoint runs on the export BUTTON, which then sat there doing nothing
    visible for the duration; a dead button is indistinguishable from a broken
    one, and it was reported as exactly that. The date scan is what the button
    needs. The audit is a diagnostic and is asked for by name.
    """
    import time

    c = _serve()
    t = time.time()
    d = c.get("/api/export/check").get_json()
    elapsed = time.time() - t
    assert d["success"] and d["reference_count"] == 0
    assert elapsed < 1.0, f"the default check took {elapsed:.1f}s — it writes the file"

    # and the caller cannot get the audit by accident
    import server
    calls = []
    import engine.xml_audit as _xa
    real = _xa.audit_project
    _xa.audit_project = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        c.get("/api/export/check")
        assert not calls, "the default path ran the audit anyway"
        c.get("/api/export/check?references=1")
        assert calls, "asking for references did not run the audit"
    finally:
        _xa.audit_project = real
