"""
test_big_schedule_is_fast.py — the free plan gives a TENTH of a core.

Two pieces of hot work sat inside every edit request on a 2,776-activity
schedule: the CPM recompute at 8.3 seconds, and the cloud save's XML export
at 6.2. Both were measured on a full core. On 0.1 CPU that is roughly 83 and
62 seconds — and a single worker pegged for that long cannot answer the
health check either, so the platform concludes the whole service is down.
That is what a 502 with an empty body and a 503 mid-request actually were.

Neither was inherent. The CPM walked day-by-day to count working days
between two dates — 12.9 million calendar checks for one pass — where the
answer is arithmetic. The exporter built the document with ElementTree and
then re-parsed all 215,601 elements with minidom purely to indent them.

These budgets are deliberately loose: they are here to catch a return to
walking or double-parsing, not to police tenths of a second. A machine
running the suite under load should still pass comfortably.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode, compute_dates)

ACTIVITIES = 2776          # the real reference schedule
WBS_NODES = 283
RELATIONS = 1675

# Roughly 10x headroom over what these now measure, so a slow CI box passes
# and a return to the old algorithm (100x slower) cannot.
CPM_BUDGET_SECONDS = 2.0
EXPORT_BUDGET_SECONDS = 6.0


@pytest.fixture(scope="module")
def big():
    p = Project(uid="1", name="DC", id="PERF", data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J")]
    for i in range(WBS_NODES):
        p.wbs_nodes.append(WBSNode(uid=f"w{i}", name=f"MV {100 + i}",
                                   code=f"M{i}", parent_uid="root"))
    # Spread over two years — the walk's cost was proportional to how far an
    # activity sat from the project start, so a schedule crammed into one
    # month would not have shown the problem at all.
    p.activities = [
        Activity(uid=f"a{i}", activity_id=f"MDC1.FDG.{1000 + i * 10}",
                 name=f"Pull Wire MV {100 + (i % WBS_NODES)} run {i}",
                 wbs_uid=f"w{i % WBS_NODES}", calendar_uid="1",
                 activity_type="Task Dependent", status="Not Started",
                 planned_duration=40.0, remaining_duration=40.0,
                 planned_start="2026-02-02", planned_finish="2026-02-06")
        for i in range(ACTIVITIES)]
    p.relations = [Relation(uid=f"r{i}", predecessor_uid=f"a{i}",
                            successor_uid=f"a{i + 1}",
                            type="Finish to Start", lag=0.0)
                   for i in range(RELATIONS)]
    p.build_lookups()
    return p


def test_the_cpm_recompute_is_not_walking_day_by_day(big):
    """Ran after every edit batch, inside the request."""
    t = time.time()
    compute_dates(big, apply_dates=False)
    took = time.time() - t
    assert took < CPM_BUDGET_SECONDS, (
        f"CPM took {took:.2f}s for {ACTIVITIES} activities — about "
        f"{took * 10:.0f}s on a tenth of a core, which is where the 503 came "
        f"from. It is counting day-by-day again.")


def test_the_xml_export_is_not_parsing_the_document_twice(big):
    """Runs on every cloud save, competing for the same CPU as requests."""
    t = time.time()
    xml = server._project_to_xml_bytes(big)
    took = time.time() - t
    assert xml.startswith(b'<?xml'), "the declaration went missing"
    assert took < EXPORT_BUDGET_SECONDS, (
        f"export took {took:.2f}s — about {took * 10:.0f}s on a tenth of a "
        f"core. It is round-tripping through minidom again.")


def test_the_export_is_still_indented_and_reimportable(big, tmp_path):
    """Speed is worthless if the file P6 gets is different."""
    from engine.xml_reader import load_xml
    path = tmp_path / "out.xml"
    server_xml = server._project_to_xml_bytes(big)
    path.write_bytes(server_xml)

    text = server_xml.decode("utf-8")
    assert "\n  <" in text, "the document is no longer indented"
    assert text.count("\n") > ACTIVITIES, "it collapsed onto one line"

    back = load_xml(str(path))
    assert len(back.activities) == ACTIVITIES
    assert len(back.relations) == RELATIONS
    assert back.get_activity(activity_id="MDC1.FDG.1000") is not None


def test_a_whole_edit_turn_stays_inside_a_sane_budget(big):
    """
    The two together, which is what one agent edit actually costs in CPU
    besides the model call itself.
    """
    t = time.time()
    compute_dates(big, apply_dates=False)
    server._project_to_xml_bytes(big)
    took = time.time() - t
    assert took < CPM_BUDGET_SECONDS + EXPORT_BUDGET_SECONDS, (
        f"{took:.2f}s of CPU per edit turn — roughly {took * 10:.0f}s on the "
        f"free plan, before the model call is counted at all.")
