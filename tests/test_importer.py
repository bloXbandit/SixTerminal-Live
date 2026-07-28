"""
test_importer.py — Deterministic extraction, focused on the messy-PDF cases.

The behaviour the user cares about: when a WBS section's activities flow off
the bottom of one page and continue on the next (the export reprints the
column header and often the WBS band on the continuation page), extraction must
keep assigning those continued activities to the SAME WBS until a genuinely new
section header appears — not start a new group and not duplicate the folder.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.importer import _rows_to_contract


def _two_page_rows():
    """Rows as pdfplumber/tabula would hand them back for a 2-page export where
    the 'Electrical' section spans the page break."""
    HDR = ["Activity ID", "Activity Name", "Duration", "Start", "Finish"]
    return [
        # ---- page 1 ----
        HDR,
        ["Electrical", "", "", "", ""],                         # WBS band
        ["A1000", "Rough-in feeders", "10", "01-Jun-26", "12-Jun-26"],
        ["A1010", "Pull wire",        "5",  "15-Jun-26", "19-Jun-26"],
        ["Page 1 of 2", "", "", "", ""],                        # footer furniture
        # ---- page 2: header + WBS band reprinted, section CONTINUES ----
        HDR,                                                    # reprinted col header
        ["Electrical", "", "", "", ""],                         # reprinted WBS band
        ["A1020", "Terminate",        "4",  "22-Jun-26", "25-Jun-26"],  # still Electrical
        ["A1030", "Energize",         "0",  "26-Jun-26", "26-Jun-26"],  # still Electrical
        ["Mechanical", "", "", "", ""],                         # NEW section
        ["M1000", "Set RTU",          "8",  "29-Jun-26", "08-Jul-26"],
        ["Page 2 of 2", "", "", "", ""],
    ]


def _contract():
    return _rows_to_contract(_two_page_rows(), {"source_type": "pdf"})


def test_section_continues_across_page_break():
    c = _contract()
    by_id = {a["activity_id"]: a for a in c["activities"]}
    # every A-prefixed activity — including the two that landed on page 2 —
    # belongs to the SAME Electrical WBS code
    elec_codes = {by_id[i]["wbs_code"] for i in ("A1000", "A1010", "A1020", "A1030")}
    assert len(elec_codes) == 1, f"Electrical split across codes: {elec_codes}"
    # and the genuinely new section is separate
    assert by_id["M1000"]["wbs_code"] != next(iter(elec_codes))


def test_reprinted_wbs_band_does_not_duplicate_the_folder():
    c = _contract()
    names = [w["name"] for w in c["wbs"]]
    assert names.count("Electrical") == 1, f"folder duplicated: {names}"
    assert names.count("Mechanical") == 1


def test_reprinted_column_header_and_footer_are_not_activities():
    c = _contract()
    ids = {a["activity_id"] for a in c["activities"]}
    # exactly the five real activities, nothing from headers/footers
    assert ids == {"A1000", "A1010", "A1020", "A1030", "M1000"}


def test_all_five_activities_extracted_in_order():
    c = _contract()
    ids = [a["activity_id"] for a in c["activities"]]
    assert ids == ["A1000", "A1010", "A1020", "A1030", "M1000"]
