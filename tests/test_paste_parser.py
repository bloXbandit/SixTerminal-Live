"""
test_paste_parser.py — Reading activities pasted out of a PDF.

The hard part is that the name is free text sitting between two rigid fields, so
these tests pin the cases that would silently corrupt data: names containing
numbers being eaten as durations, and the trailing "A" actual-marker being lost
(which would import every completed activity as Not Started).

Rows here are copied from a real P6 PDF export.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.paste_parser import parse_pasted_text, contract_from_paste


PDF_BLOCK = """MDC1.PMT.1190     Building Permit Review (Interior Build-out)      68  24-Nov-25 A   05-Mar-26
MDC1.PMT.1250     Electrical FAA Permit                            45  12-Dec-25*    18-Feb-26
Funding                                                            20  20-Mar-26     16-Apr-26
MDC1.FDG.1290     Team Approach Trades Award (A Trades)             1  02-Jul-25 A   02-Jul-25 A
MDC1.FDG.1480     LLE Selection / Pricing MV Switchgear 38 kV (GIS SE)   34  25-Aug-25 A  10-Oct-25 A
MDC1.FDG.1670     LLE Selection / Pricing Generators (4MW)          77  25-Aug-25 A   12-Dec-25
MDC1.FDG.1430     Partial Funding PCCO # 1 Review and Approval      15  12-Aug-25 A   02-Sep-25"""


def _rows():
    rows, _, _ = parse_pasted_text(PDF_BLOCK)
    return {r[0]: r for r in rows[1:] if r[0]}


# ── Column inference ─────────────────────────────────────────────────────────

def test_every_activity_row_is_read():
    rows, outline, info = parse_pasted_text(PDF_BLOCK)
    assert info["mode"] == "text"
    assert info["rows_parsed"] == 7          # 6 activities + 1 section band


def test_digits_inside_a_name_are_not_taken_as_the_duration():
    """'MV Switchgear 38 kV' and 'Generators (4MW)' must keep their real dur."""
    r = _rows()
    assert r["MDC1.FDG.1480"][2] == "34"     # not 38
    assert "38 kV (GIS SE)" in r["MDC1.FDG.1480"][1]
    assert r["MDC1.FDG.1670"][2] == "77"     # not 4
    assert "(4MW)" in r["MDC1.FDG.1670"][1]
    assert r["MDC1.FDG.1430"][2] == "15"     # not the '1' in 'PCCO # 1'
    assert "PCCO # 1 Review and Approval" in r["MDC1.FDG.1430"][1]


def test_actual_marker_survives_parsing():
    """The trailing 'A' drives imported status — losing it silently marks
    finished work as Not Started."""
    r = _rows()
    assert r["MDC1.PMT.1190"][3] == "24-Nov-25 A"
    assert r["MDC1.FDG.1290"][3] == "02-Jul-25 A"
    assert r["MDC1.FDG.1290"][4] == "02-Jul-25 A"


def test_constraint_marker_survives_parsing():
    assert _rows()["MDC1.PMT.1250"][3] == "12-Dec-25*"


def test_a_line_without_an_id_is_a_section_heading():
    rows, _, _ = parse_pasted_text(PDF_BLOCK)
    band = [r for r in rows[1:] if not r[0]]
    assert len(band) == 1
    assert band[0][1] == "Funding"


# ── Contract building ────────────────────────────────────────────────────────

def test_actuals_become_status_and_dates():
    c = contract_from_paste(PDF_BLOCK)
    by = {a["activity_id"]: a for a in c["activities"]}
    # both dates actual -> Completed
    done = by["MDC1.FDG.1290"]
    assert done["status"] == "Completed"
    assert done["actual_start"] == "2025-07-02" and done["actual_finish"] == "2025-07-02"
    # start actual only -> In Progress
    wip = by["MDC1.FDG.1670"]
    assert wip["status"] == "In Progress"
    assert wip["actual_start"] == "2025-08-25" and wip["actual_finish"] is None
    # no actual -> Not Started, planned dates
    ns = by["MDC1.PMT.1250"]
    assert ns["status"] == "Not Started"
    assert ns["planned_start"] == "2025-12-12"


def test_activities_after_a_band_belong_to_that_section():
    c = contract_from_paste(PDF_BLOCK)
    by = {a["activity_id"]: a for a in c["activities"]}
    funding_code = next(w["code"] for w in c["wbs"] if w["name"] == "Funding")
    for aid in ("MDC1.FDG.1290", "MDC1.FDG.1480", "MDC1.FDG.1670"):
        assert by[aid]["wbs_code"] == funding_code
    # the ones printed before the band must NOT be swept into it
    assert by["MDC1.PMT.1190"]["wbs_code"] != funding_code


def test_tab_separated_paste_from_excel():
    text = ("Activity ID\tActivity Name\tDuration\tStart\tFinish\n"
            "A1000\tRough-in\t10\t01-Jun-26\t12-Jun-26\n"
            "A1010\tTerminate\t5\t15-Jun-26\t19-Jun-26")
    rows, outline, info = parse_pasted_text(text)
    assert info["mode"] == "tab"
    assert info["rows_parsed"] == 2          # header line dropped
    c = contract_from_paste(text)
    assert {a["activity_id"] for a in c["activities"]} == {"A1000", "A1010"}


def test_committed_project_has_dates_without_a_manual_schedule_run():
    """A pasted contract carries no data date (paste_info sets it to None), so
    build_project_from_contract must fall back to an origin itself — otherwise
    compute_dates() has nothing to schedule from and bails out, leaving every
    activity's Start/Finish column blank until the user manually hits
    Schedule. Completed/In-Progress rows (actual dates only) and Not-Started
    rows with no dates at all are both affected."""
    from engine.importer import build_project_from_contract

    c = contract_from_paste(PDF_BLOCK)
    p = build_project_from_contract(c)
    assert p.data_date or p.planned_start
    by = {a.activity_id: a for a in p.activities}
    # Completed, actual dates only -> planned_start must be derived, not blank
    assert by["MDC1.FDG.1290"].planned_start
    # In Progress, actual start only -> planned_start must be derived
    assert by["MDC1.FDG.1670"].planned_start

    text = "A1000\tRough-in\t10\nA1010\tTerminate\t5"
    c2 = contract_from_paste(text)
    p2 = build_project_from_contract(c2)
    assert all(a.planned_start for a in p2.activities)


def test_empty_paste_is_reported_not_crashed():
    rows, _, info = parse_pasted_text("   \n  \n")
    assert info["rows_parsed"] == 0
    assert info["warnings"]


def test_names_only_paste_warns_about_missing_ids():
    rows, _, info = parse_pasted_text("Pour slab\nStrip forms\nCure")
    assert any("ID" in w for w in info["warnings"])


# ── No-date rows: duration must not vanish into the name ─────────────────────

def test_duration_survives_when_the_row_has_no_dates():
    """A paste of just id + name + duration is common. Without dates to anchor
    on, the column gap is the only signal — but the duration must still land in
    the duration column rather than being glued onto the name."""
    rows, _, _ = parse_pasted_text("MDC1.FDG.1290   Team Approach Award   45")
    _, name, dur, _, _ = rows[1]
    assert name == "Team Approach Award"
    assert dur == "45"


def test_a_number_inside_the_name_is_not_a_duration_when_there_are_no_dates():
    rows, _, _ = parse_pasted_text("MDC1.FDG.1290   Install Panel 42")
    _, name, dur, _, _ = rows[1]
    assert name == "Install Panel 42"
    assert dur == ""
