"""
test_wbs_rebuild.py — giving a parsed schedule its hierarchy back.

A schedule that came out of a PDF has no WBS. Exhibit S parses into 3,400 real
activities and 281 folders that are all siblings under one root, mixed in with
the page furniture the parser could not tell from work — including the Gantt
timescale header, which collected 375 activities on its way past.

The activities are fine. Their IDs already say where each one belongs, so the
tree is rebuilt from those and the old folders are used only for their names,
which are the real ones somebody typed in P6 before it was printed.

What these tests are really about is not losing anything. A rebuild that
silently drops forty rows of work still shows about the right total, which is
exactly how it would go unnoticed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import wbs_rebuild as wr
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job(rows, folders=("Everything",)):
    """rows: (activity_id, name, type, duration)."""
    p = Project(uid="1", name="Parsed", id="PARSED-1",
                data_date="2026-02-03", planned_start="2026-02-03")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Parsed", code="ROOT")]
    for i, f in enumerate(folders, start=1):
        p.wbs_nodes.append(WBSNode(uid=f"f{i}", name=f, code=f"F{i}",
                                   parent_uid="root"))
    p.activities = []
    for i, (aid, name, kind, dur) in enumerate(rows, start=1):
        a = Activity(uid=f"u{i}", activity_id=aid, name=name, wbs_uid="f1",
                     calendar_uid="1", planned_duration=dur,
                     remaining_duration=dur, planned_start="2026-03-02",
                     planned_finish="2026-03-06")
        a.activity_type = kind
        p.activities.append(a)
    p.relations = []
    p.build_lookups()
    return p


T = "Task Dependent"
M = "Finish Milestone"


# ── the names the parse mangled ──────────────────────────────────────────────

def test_a_doubled_folder_name_is_halved():
    """Every folder name came out twice with no separator between them."""
    assert wr.undouble("Segment ESegment E") == "Segment E"
    assert wr.undouble("Exterior/Site UtilitiesExterior/Site Utilities") \
        == "Exterior/Site Utilities"


def test_a_name_that_merely_repeats_a_word_is_left_alone():
    """Halving on any repetition would turn real names into nonsense."""
    assert wr.undouble("Pour 1") == "Pour 1"
    assert wr.undouble("Area 3 Erection North Crane") \
        == "Area 3 Erection North Crane"


def test_the_datacenter_letter_becomes_the_phase_number():
    """The client says Datacenter A; the schedule is worked in phases. Carrying
    both names for one thing is how "which datacenter is phase 2" becomes a
    question asked every week."""
    assert wr.rename_datacenters("LLE - Datacenter A") == "LLE - Phase 1"
    assert wr.rename_datacenters("MV Rooms - Datacenter B") == "MV Rooms - Phase 2"
    assert wr.rename_datacenters("CUP - Datacenter C") == "CUP - Phase 3"


def test_the_phase_folder_does_not_say_its_own_name_twice():
    assert wr.rename_datacenters('PH1: DH202 "Datacenter A"') \
        == "Phase 1 (Build-Out)"


# ── the page furniture ───────────────────────────────────────────────────────

def test_the_running_header_is_not_an_activity():
    p = _job([("MDC3-LIVE", "- MDC3 - LIVE Page 1 of 47 Printed:", T, 0),
              ("MDC3.EXT.SUT.1690", "Mobilize", T, 8)])
    r = wr.plan(p)
    assert r["counts"]["furniture"] == 1
    assert r["counts"]["placed"] == 1


def test_real_work_in_a_furniture_folder_is_still_real_work():
    """
    The Gantt timescale header collected 375 activities. They are genuine —
    "Area 3 Erection North Crane", 40 hours — and dropping the folder must not
    take them with it.
    """
    p = _job([("MDC3.ST.PC.A3.1010", "Area 3 Erection North Crane", T, 40)],
             folders=("Jan Feb Mar Apr May Jun",))
    r = wr.plan(p)
    assert r["counts"]["placed"] == 1
    assert r["counts"]["furniture"] == 0


# ── placement from the id ────────────────────────────────────────────────────

def test_a_phase_activity_lands_in_its_phase():
    p = _job([("MDC3.PH2.LLE.1000", "Pull feeders", T, 40)])
    path = wr.place(p.activities[0])
    assert path[0] == "Phase 2 (Build-Out)"
    assert path[1] == "LLE - Phase 2"


def test_the_shell_is_separated_from_the_fit_out():
    p = _job([("MDC3.EXT.SUT.1690", "Mobilize", T, 8)])
    assert wr.place(p.activities[0])[0] == "Construction (Core & Shell)"


def test_a_generator_room_goes_to_the_phase_that_owns_it():
    """
    Not derivable from the number — 324 sits in phase 2 between 323 and 325,
    which are both phase 1 — so it is carried from the sister job where the
    same 28 rooms are split the same way.
    """
    p = _job([("MDC3.GEN.324.1000", "Set Engine", T, 16),
              ("MDC3.GEN.323.1000", "Set Engine", T, 16)])
    assert wr.place(p.activities[0])[0] == "Phase 2 (Build-Out)"
    assert wr.place(p.activities[1])[0] == "Phase 1 (Build-Out)"


def test_a_generator_row_with_no_room_does_not_make_an_unnamed_folder():
    """A folder called "Gen " with nothing after it is worse than saying the
    room is not specified."""
    p = _job([("MDC3.GEN.RM.1940", "Final Paint", T, 8)])
    path = wr.place(p.activities[0])
    assert path[-1].strip() == path[-1]
    assert path[-1] != "Gen"


def test_a_milestone_is_gathered_with_the_other_milestones():
    """
    Buried in the area folder it was scheduled against, "Room Ready for Load"
    is one row among two hundred. Gathered up, the set of them is the
    schedule's spine.
    """
    p = _job([("MDC3.PH1.DH202E.1200", "Room Ready for Load - DH 202 (East)", M, 0)])
    path = wr.place(p.activities[0])
    assert path[0] == "Milestones"
    assert path[1] == "Phase 1"


# ── the name beats the id where they disagree ────────────────────────────────

def test_the_room_the_activity_names_wins_over_the_room_its_code_says():
    """
    48 activities name a different room from the one their id does. The id was
    renumbered around them; the words are what a person typed. An area folder
    full of the wrong rooms is worse than no area folder at all.
    """
    p = _job([("MDC3.PH3.MV.103.1020", "Drywall Framing - HV 102 - Data Center C",
               T, 16)])
    path = wr.place(p.activities[0])
    assert path[0] == "Phase 3 (Build-Out)"
    assert path[1] == "HV Rooms - Phase 3", f"followed the id, not the name: {path}"
    assert path[2] == "HV 102"


def test_the_building_named_in_the_text_fixes_the_phase():
    p = _job([("MDC3.PH1.MV.112.1000", "Paint - MV 106 - Data Center B", T, 8)])
    assert wr.place(p.activities[0])[0] == "Phase 2 (Build-Out)"


# ── repairing what the parse broke ───────────────────────────────────────────

def test_a_digit_that_fell_out_of_the_id_is_put_back():
    """
    `MDC3.PH1.DH202W.120` named `0 Room Ready for Load - DH 202 (West)`: the
    last digit of the code fell off the end of the id and landed at the front
    of the name. Both are wrong and each makes the other look deliberate.
    """
    p = _job([("MDC3.PH3.DH101W.120", "0 Room Ready for Load - DH 101 (West)",
               M, 0)])
    fixed = wr.repair_parse_damage(p)
    assert len(fixed) == 1
    assert p.activities[0].activity_id == "MDC3.PH3.DH101W.1200"
    assert p.activities[0].name == "Room Ready for Load - DH 101 (West)"


def test_a_name_that_really_starts_with_a_digit_is_not_mangled():
    """An id with a full four-digit tail has lost nothing, so a leading number
    in the name is part of the name."""
    p = _job([("MDC3.ST.L1.A10.1050", "2 Pours Complete", T, 8)])
    assert wr.repair_parse_damage(p) == []
    assert p.activities[0].name == "2 Pours Complete"


# ── the whole rebuild ────────────────────────────────────────────────────────

def test_nothing_is_lost():
    """The invariant. A rebuild that drops rows still shows about the right
    total, which is exactly how it would go unnoticed."""
    rows = [("MDC3.PH1.LLE.1000", "Pull feeders", T, 40),
            ("MDC3.PH2.ER.201.1000", "Paint - ER 201 - Data Center B", T, 8),
            ("MDC3.GEN.315.1000", "Set Engine", T, 16),
            ("MDC3.EXT.SUT.1690", "Mobilize", T, 8),
            ("MDC3.ROOF.MEP.1060", "Set curbs", T, 24),
            ("MDC3.PH3.DH101E.1200", "Room Ready for Load - DH 101 (East)", M, 0)]
    p = _job(rows)
    hours = sum(float(a.planned_duration) for a in p.activities)
    wr.rebuild(p)
    assert len(p.activities) == len(rows)
    assert sum(float(a.planned_duration) for a in p.activities) == hours
    uids = {n.uid for n in p.wbs_nodes}
    assert all(a.wbs_uid in uids for a in p.activities), "an activity has no folder"


def test_the_rebuild_refuses_rather_than_losing_an_activity():
    """An id the grammar does not cover is a question, not a folder."""
    p = _job([("MDC3.WAT.1000", "Something nobody mapped", T, 8)])
    with pytest.raises(ValueError, match="no place in the tree"):
        wr.rebuild(p)


def test_every_folder_it_builds_holds_something():
    p = _job([("MDC3.PH1.LLE.1000", "Pull feeders", T, 40),
              ("MDC3.GEN.315.1000", "Set Engine", T, 16)])
    wr.rebuild(p)
    kids = {n.parent_uid for n in p.wbs_nodes if n.parent_uid}
    used = {a.wbs_uid for a in p.activities}
    for n in p.wbs_nodes:
        if n.parent_uid is None:
            continue
        assert n.uid in kids or n.uid in used, f"{n.name} holds nothing"


def test_the_tree_is_actually_nested():
    """The whole complaint: 281 folders as siblings under one root."""
    p = _job([("MDC3.PH1.MV.101.1000", "Paint - MV 101 - Data Center A", T, 8)])
    wr.rebuild(p)
    by_uid = {n.uid: n for n in p.wbs_nodes}

    def depth(n):
        d = 0
        while n and n.parent_uid:
            n = by_uid.get(n.parent_uid)
            d += 1
        return d

    assert max(depth(n) for n in p.wbs_nodes) >= 3
