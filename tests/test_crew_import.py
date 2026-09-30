"""
test_crew_import.py — bringing a headcount in from the labour loading sheet.

The sheet was filled in against a schedule whose activity IDs have since been
renumbered by hand, so ID alone reaches about a quarter of it. Name alone is
worse than useless: "Overhead Electrical/FA" appears in every generator room,
so a name match picks an arbitrary one of twenty-eight and looks like it
worked.

Which is the thing these tests are really about. A headcount written onto the
wrong activity is worse than a blank one, because a blank one is visibly
missing and a wrong one becomes hours in a usage profile nobody questions. So
every strategy here has to be provably unambiguous, and anything that is not
has to come back as unmatched rather than as a best guess.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl
import pytest

from engine import crew_import as ci
from engine.schedule_model import Activity, Calendar, Project, WBSNode

CREW = ci.CREW_FIELD


def _sheet(rows):
    """rows: (level, id_or_folder, name, crew) — name blank means a folder."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["WBS", "Activity ID", "Activity Name", "BL1 Duration",
               "BL1 Start", "BL1 Finish", "Number of Electricians"])
    for lvl, label, name, crew in rows:
        ws.append([lvl, label, name, None, None, None, crew])
    fh = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    fh.close()
    wb.save(fh.name)
    return fh.name


def _job(acts):
    """acts: (activity_id, name, folder, crew_already)."""
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    folders = {}
    p.wbs_nodes, p.activities = [], []
    for i, (aid, name, folder, have) in enumerate(acts, start=1):
        if folder not in folders:
            folders[folder] = f"w{len(folders) + 1}"
            p.wbs_nodes.append(WBSNode(uid=folders[folder], name=folder,
                                       code=folder))
        a = Activity(uid=f"u{i}", activity_id=aid, name=name,
                     wbs_uid=folders[folder], calendar_uid="1",
                     planned_duration=40, remaining_duration=40)
        if have:
            a.udfs = {CREW: str(have)}
        p.activities.append(a)
    p.relations = []
    p.build_lookups()
    return p


# ── reading the outline ──────────────────────────────────────────────────────

def test_the_folder_path_is_rebuilt_from_the_indent():
    """Folder plus name is unique where name alone is not, so the outline is
    the whole reason this is tractable."""
    path = _sheet([(2, "Building", "", None),
                   (3, "Gen 314", "", None),
                   (4, "MDC1.GEN.4080", "Overhead Electrical/FA", 10)])
    rows = ci.read_sheet(path)
    os.unlink(path)
    assert len(rows) == 1
    assert rows[0]["folder_path"] == ["Building", "Gen 314"]
    assert rows[0]["crew"] == 10


def test_a_deeper_folder_does_not_outlive_its_parent():
    path = _sheet([(2, "Phase 1", "", None),
                   (3, "Gen 314", "", None),
                   (2, "Phase 2", "", None),
                   (3, "Gen 315", "", None),
                   (4, "A20", "Set Engine", 2)])
    rows = ci.read_sheet(path)
    os.unlink(path)
    assert rows[0]["folder_path"] == ["Phase 2", "Gen 315"]


def test_rows_with_no_headcount_are_left_out():
    path = _sheet([(4, "A10", "Pull wire", 5), (4, "A20", "Terminate", None)])
    rows = ci.read_sheet(path)
    os.unlink(path)
    assert [r["activity_id"] for r in rows] == ["A10"]


def test_a_zero_headcount_is_not_a_headcount():
    path = _sheet([(4, "A10", "Pull wire", 0)])
    rows = ci.read_sheet(path)
    os.unlink(path)
    assert rows == []


# ── matching ─────────────────────────────────────────────────────────────────

def test_an_exact_activity_id_wins():
    p = _job([("A10", "Pull wire", "Gen 314", None)])
    r = ci.plan(p, [{"activity_id": "A10", "name": "anything at all",
                     "crew": 5, "folder_path": ["elsewhere"]}])
    assert r["counts"]["applied"] == 1
    assert r["applied"][0]["matched_by"] == "activity id"


def test_folder_and_name_resolve_what_name_alone_cannot():
    """
    The case the whole module exists for: the same name in every room, and
    the IDs renumbered out from under the sheet.
    """
    p = _job([("MDC1.PH2.GEN.3510", "Overhead Electrical/FA", "Gen 314", None),
              ("MDC1.PH2.GEN.4820", "Overhead Electrical/FA", "Gen 315", None)])
    r = ci.plan(p, [{"activity_id": "MDC1.GEN.4080",
                     "name": "Overhead Electrical/FA", "crew": 10,
                     "folder_path": ["Building", "Gen 314"]}])
    assert r["counts"]["applied"] == 1
    got = r["applied"][0]
    assert got["activity_id"] == "MDC1.PH2.GEN.3510"
    assert got["matched_by"] == "folder + name"


def test_the_scope_suffix_does_not_break_the_folder_match():
    """The sheet says 'Gen 314'; the schedule splits it into JER and WBO."""
    p = _job([("A10", "Set Engine", "Gen 314 - JER", None)])
    r = ci.plan(p, [{"activity_id": "OLD", "name": "Set Engine", "crew": 2,
                     "folder_path": ["Gen 314"]}])
    assert r["counts"]["applied"] == 1


def test_our_own_scope_wins_over_work_by_others():
    """
    JER is this contractor, WBO is work by others. Loading our headcount into
    somebody else's folder puts their crew in our usage profile.
    """
    p = _job([("A10", "Set Engine", "Gen 314 - WBO", None),
              ("A20", "Set Engine", "Gen 314 - JER", None)])
    r = ci.plan(p, [{"activity_id": "OLD", "name": "Set Engine", "crew": 2,
                     "folder_path": ["Gen 314"]}])
    assert r["counts"]["applied"] == 1
    assert r["applied"][0]["activity_id"] == "A20"


def test_an_ambiguous_name_is_reported_not_guessed():
    """
    The failure that matters. Eleven rooms share this name and the sheet's
    folder reaches none of them, so there is no right answer to pick — and a
    headcount on the wrong activity is worse than a blank one, because a blank
    one is visibly missing.
    """
    p = _job([("A10", "Overhead Rough In", "ER 101", None),
              ("A20", "Overhead Rough In", "ER 102", None)])
    r = ci.plan(p, [{"activity_id": "OLD", "name": "Overhead Rough In",
                     "crew": 5, "folder_path": ["MV", "MV 102"]}])
    assert r["counts"]["applied"] == 0
    assert r["counts"]["unmatched"] == 1
    assert "share this name" in r["unmatched"][0]["why"]


def test_a_name_nothing_carries_is_reported():
    p = _job([("A10", "Pull wire", "Gen 314", None)])
    r = ci.plan(p, [{"activity_id": "OLD", "name": "Something else entirely",
                     "crew": 5, "folder_path": ["Gen 314"]}])
    assert r["counts"]["unmatched"] == 1


# ── what it does with what it finds ──────────────────────────────────────────

def test_a_count_already_there_and_the_same_is_left_alone():
    p = _job([("A10", "Pull wire", "Gen 314", "5")])
    r = ci.plan(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 5,
                     "folder_path": []}])
    assert r["counts"] == {"applied": 0, "unchanged": 1, "conflict": 0,
                           "unmatched": 0, "rows": 1}


def test_a_disagreement_is_reported_and_not_written():
    """The schedule is newer than the sheet, so the sheet does not get to
    overrule it silently."""
    p = _job([("A10", "Pull wire", "Gen 314", "12")])
    r = ci.apply(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 10,
                      "folder_path": []}])
    assert r["counts"]["conflict"] == 1
    assert p.activities[0].udfs[CREW] == "12", "overwrote without being asked"


def test_overwrite_is_available_when_asked_for():
    p = _job([("A10", "Pull wire", "Gen 314", "12")])
    ci.apply(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 10,
                  "folder_path": []}], overwrite=True)
    assert p.activities[0].udfs[CREW] == "10"


def test_the_headcount_lands_in_the_udf_the_restore_reads():
    """It has to be the field crew_field_of finds, or none of this derives
    into hours."""
    from engine.resource_restore import crew_field_of
    p = _job([("A10", "Pull wire", "Gen 314", None)])
    ci.apply(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 6,
                  "folder_path": []}])
    assert p.activities[0].udfs[CREW] == "6"
    assert crew_field_of(p) == CREW


def test_a_whole_number_is_written_without_a_decimal_point():
    p = _job([("A10", "Pull wire", "Gen 314", None)])
    ci.apply(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 5.0,
                  "folder_path": []}])
    assert p.activities[0].udfs[CREW] == "5"


def test_planning_changes_nothing():
    p = _job([("A10", "Pull wire", "Gen 314", None)])
    ci.plan(p, [{"activity_id": "A10", "name": "Pull wire", "crew": 5,
                 "folder_path": []}])
    assert not (p.activities[0].udfs or {}).get(CREW)


def test_every_row_lands_in_exactly_one_bucket():
    """The counts have to add up, or the report is lying about coverage."""
    p = _job([("A10", "Pull wire", "Gen 314", None),
              ("A20", "Terminate", "Gen 314", "3")])
    rows = [{"activity_id": "A10", "name": "Pull wire", "crew": 5, "folder_path": []},
            {"activity_id": "A20", "name": "Terminate", "crew": 3, "folder_path": []},
            {"activity_id": "A20", "name": "Terminate", "crew": 9, "folder_path": []},
            {"activity_id": "ZZ", "name": "Ghost", "crew": 1, "folder_path": []}]
    c = ci.plan(p, rows)["counts"]
    assert c["applied"] + c["unchanged"] + c["conflict"] + c["unmatched"] == len(rows)
