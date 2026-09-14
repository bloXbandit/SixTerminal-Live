"""
test_memory_ceiling.py — the worker must survive the second schedule.

Sessions live in this process. If the host kills the worker for using too much
memory, the projects go with it and the user is simply thrown out mid-request —
which is exactly how this was reported: "I get kicked out when I upload a new
project", and again on a big Excel export.

An undo snapshot is a shallow copy of every activity, relation, folder and
calendar. Fifty of those is free on a small job and ~170 MB on a 2,400-activity
one, and the old limit was a flat fifty per project with no ceiling on how many
projects kept one. Two schedules open was over the line on a 512 MB host.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job(n):
    p = Project(uid=f"p{n}", name="J", id=f"J{n}", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities = [
        Activity(uid=f"u{i}", activity_id=f"A{1000 + i}", name=f"T{i}",
                 wbs_uid="w", calendar_uid="1", planned_duration=8,
                 planned_start="2026-02-02", planned_finish="2026-02-03")
        for i in range(n)
    ]
    p.relations = []
    p.build_lookups()
    return p


def _session(pid, project):
    server._projects[pid] = server._make_session(pid, f"{pid}.xml")
    server._projects[pid]["project"] = project
    server._active_id[0] = pid


@pytest.fixture(autouse=True)
def clean():
    server._projects.clear()
    server._brains.clear()
    yield
    server._projects.clear()
    server._brains.clear()


def test_a_small_job_still_remembers_fifty_steps():
    """The budget must not cost small projects their undo depth."""
    assert server._undo_depth(_job(50)) == server._MAX_UNDO


def test_a_big_job_remembers_fewer_steps_rather_than_more_megabytes():
    deep = server._undo_depth(_job(2400))
    assert 5 <= deep < server._MAX_UNDO, deep


def test_what_is_held_constant_is_the_memory_not_the_step_count():
    """
    Two jobs past the cap, one twice the size: the bigger one remembers half as
    many steps, so the bytes retained are the same. That is what budgeting in
    megabytes means, and it is why one job's history cannot crowd out the host.
    """
    small = 2400
    big = small * 2
    rows_small = server._undo_depth(_job(small)) * small
    rows_big = server._undo_depth(_job(big)) * big
    assert abs(rows_small - rows_big) < rows_small * 0.25, (rows_small, rows_big)


def test_the_depth_is_never_zero():
    """A schedule too big to undo at all would be worse than the crash."""
    assert server._undo_depth(_job(500000)) >= 5


def test_the_stack_is_trimmed_to_the_budget_not_to_fifty():
    p = _job(2400)
    _session("big", p)
    depth = server._undo_depth(p)
    for i in range(depth + 20):
        server._push_undo(f"edit {i}")
    assert len(server._projects["big"]["undo_stack"]) == depth


def test_loading_a_second_schedule_drops_the_first_ones_history():
    """Holding two full histories is what ran the host out of memory."""
    _session("a", _job(800))
    for i in range(6):
        server._push_undo(f"e{i}")
    assert server._projects["a"]["undo_stack"]

    _session("b", _job(800))
    freed = server._shed_idle_history("b")
    assert freed == 6
    assert server._projects["a"]["undo_stack"] == []
    assert server._projects["a"]["project"] is not None, "it dropped the project too"


def test_shedding_leaves_the_active_project_alone():
    _session("a", _job(100))
    for i in range(3):
        server._push_undo(f"e{i}")
    server._shed_idle_history("a")
    assert len(server._projects["a"]["undo_stack"]) == 3
