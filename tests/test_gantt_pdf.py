"""
test_gantt_pdf.py — a schedule back out of the Gantt chart somebody printed.

Sometimes the only copy anyone will give you is a PDF of the Gantt. It is not
a schedule; it is a picture of one. But a P6 print is a fixed-column table, and
everything except the logic is in it — including, in the indentation of the
left column, the WBS.

That last part is what these tests are mostly about. An earlier attempt at this
read the rows and threw the indentation away, so 3,400 activities arrived as
siblings in one flat list and the hierarchy had to be guessed back out of the
activity ids afterwards. The indent is right there.

The other thing they are about is not losing rows. A milestone has no duration,
so P6 prints one date for it instead of two — and a reader that insists on both
drops every contractual date on the job while still looking like it worked.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import gantt_pdf as gp


# ── the doubled text a bold row prints ───────────────────────────────────────

def test_a_bold_folder_row_is_undoubled():
    """
    The renderer draws a bold row twice, overlapping, so it looks right on the
    page and reads as a stutter once the words are pulled out.
    """
    assert gp.undouble("Funding Funding") == "Funding"
    assert gp.undouble("Milestones Milestones (Phase (Phase 1) 1)") \
        == "Milestones (Phase 1)"


def test_a_name_that_genuinely_repeats_a_word_survives():
    """Halving on any repetition would turn real names into nonsense."""
    assert gp.undouble("Pour 2 Level 1") == "Pour 2 Level 1"
    assert gp.undouble("Wall to Wall Inspection") == "Wall to Wall Inspection"


# ── the dates P6 prints ──────────────────────────────────────────────────────

def test_a_printed_date_becomes_a_real_one():
    assert gp._date("28-Jan-26") == "2026-01-28"
    assert gp._date("03-Sep-24") == "2024-09-03"


def test_something_that_is_not_a_date_is_not_guessed_at():
    assert gp._date("157") is None
    assert gp._date("Funding") is None
    assert gp._date("32-Jan-26") is None


# ── depth from the indent ────────────────────────────────────────────────────

def test_the_indent_steps_become_depths():
    """
    Derived from the document rather than assumed: the step is a P6 layout
    setting — about 2.4 points on this print — and a print made with a
    different one would otherwise come out flat.
    """
    levels = gp._levels([46.7, 49.1, 51.5, 53.9, 49.1, 51.5])
    assert levels[46.7] == 0
    assert levels[49.1] == 1
    assert levels[51.5] == 2
    assert levels[53.9] == 3


def test_a_ragged_indent_is_not_read_as_a_new_level():
    """Two points of jitter in the same column is not a level of hierarchy."""
    levels = gp._levels([50.0, 50.4, 53.0])
    assert len(set(levels.values())) == 2


# ── building the project ─────────────────────────────────────────────────────

def _rows(*specs):
    """specs: (kind, depth, id, name, days, start, finish, sA, fA)."""
    out = []
    for kind, depth, aid, name, days, s, f, sa, fa in specs:
        out.append({"kind": kind, "depth": depth, "indent": 40.0 + depth * 2.4,
                    "activity_id": aid, "name": name, "duration_days": days,
                    "start": s, "finish": f, "start_actual": sa,
                    "finish_actual": fa, "activity_type": "", "page": 1,
                    "unknown_indent": False})
    return {"rows": out, "levels": {}, "counts": {}}


def test_a_folder_takes_the_rows_beneath_it():
    p = gp.to_project(_rows(
        ("wbs", 0, None, "The Job", 100, "2026-01-05", "2026-06-01", 0, 0),
        ("wbs", 1, None, "Phase 1", 50, "2026-01-05", "2026-03-01", 0, 0),
        ("activity", 2, "X.1000", "Pull wire", 5, "2026-01-05", "2026-01-09", 0, 0),
    ), "J", "Job")
    act = p.activities[0]
    by_uid = {n.uid: n for n in p.wbs_nodes}
    assert by_uid[act.wbs_uid].name == "Phase 1"


def test_the_prints_own_outer_row_is_the_project_not_a_folder_in_it():
    """
    Kept as a folder it produced a root inside a root — the extra level
    everybody then has to expand past.
    """
    p = gp.to_project(_rows(
        ("wbs", 0, None, "MDC2-Weekly-20251205", 753, "2024-09-03", "2027-08-30", 1, 0),
        ("wbs", 1, None, "Milestones", 10, "2026-01-05", "2026-01-19", 0, 0),
        ("activity", 2, "X.1000", "NTP", 0, "2026-01-05", "2026-01-05", 0, 0),
    ), "J", "Job")
    roots = [n for n in p.wbs_nodes if not n.parent_uid]
    assert len(roots) == 1
    assert not any((n.name or "") == "MDC2-Weekly-20251205" for n in p.wbs_nodes)


def test_opening_a_folder_closes_the_deeper_ones():
    """Otherwise a later shallow row lands inside a branch it has left."""
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 100, "2026-01-05", "2026-06-01", 0, 0),
        ("wbs", 1, None, "Phase 1", 50, "2026-01-05", "2026-03-01", 0, 0),
        ("wbs", 2, None, "Gen 315", 20, "2026-01-05", "2026-02-01", 0, 0),
        ("wbs", 1, None, "Phase 2", 50, "2026-03-01", "2026-06-01", 0, 0),
        ("activity", 2, "X.2000", "Pull wire", 5, "2026-03-01", "2026-03-05", 0, 0),
    ), "J", "Job")
    by_uid = {n.uid: n for n in p.wbs_nodes}
    holder = by_uid[p.activities[0].wbs_uid]
    assert holder.name == "Phase 2", f"landed in {holder.name}"


def test_an_actual_start_makes_the_activity_in_progress():
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 10, "2026-01-05", "2026-01-19", 0, 0),
        ("activity", 1, "X.1000", "Mobilize", 5, "2026-01-05", "2026-01-09", 1, 0),
    ), "J", "Job")
    assert p.activities[0].status == "In Progress"
    assert p.activities[0].actual_start == "2026-01-05"


def test_an_actual_finish_makes_it_complete_with_nothing_remaining():
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 10, "2026-01-05", "2026-01-19", 0, 0),
        ("activity", 1, "X.1000", "Mobilize", 5, "2026-01-05", "2026-01-09", 1, 1),
    ), "J", "Job")
    a = p.activities[0]
    assert a.status == "Completed"
    assert a.remaining_duration == 0.0
    assert a.percent_complete == 100.0


def test_duration_is_converted_from_days_to_the_hours_p6_stores():
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 10, "2026-01-05", "2026-01-19", 0, 0),
        ("activity", 1, "X.1000", "Pull wire", 5, "2026-01-05", "2026-01-09", 0, 0),
    ), "J", "Job", hours_per_day=8.0)
    assert p.activities[0].planned_duration == 40.0


def test_every_activity_lands_in_a_folder_that_exists():
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 100, "2026-01-05", "2026-06-01", 0, 0),
        ("activity", 1, "X.1000", "Straight under the root", 5,
         "2026-01-05", "2026-01-09", 0, 0),
    ), "J", "Job")
    uids = {n.uid for n in p.wbs_nodes}
    assert all(a.wbs_uid in uids for a in p.activities)


def test_a_gantt_print_yields_no_logic_and_does_not_pretend_otherwise():
    """A property of the source, reported rather than papered over."""
    p = gp.to_project(_rows(
        ("wbs", 0, None, "Job", 10, "2026-01-05", "2026-01-19", 0, 0),
        ("activity", 1, "X.1000", "Pull wire", 5, "2026-01-05", "2026-01-09", 0, 0),
    ), "J", "Job")
    assert p.relations == []
    assert "no logic" in gp.describe({"rows": [], "levels": {},
                                      "counts": {"rows": 0, "activities": 0,
                                                 "folders": 0}})
