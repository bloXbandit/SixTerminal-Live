"""
test_rename_is_not_a_rebuild.py — a folder rename repaints a header, not the grid.

rename_wbs was classified as a tree change, and the WBS signature included
names, so typing a new folder name forced the client to refetch and rebuild
the whole grid — losing scroll, collapse state and focus for an edit that
changed one string on one header row. A rename does not move a single row;
it belongs on the patch path, with the response saying exactly which headers
to repaint (renamed_folders). Shape changes — folders added, deleted or
re-parented — still reload, because row placement really does depend on them.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V21.12/API/BusinessObjects"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <Project><ObjectId>1</ObjectId><Id>RN</Id><Name>Rename Test</Name>
  <DataDate>2026-01-05T00:00:00</DataDate>
  <PlannedStartDate>2026-01-05T00:00:00</PlannedStartDate>
  <WBS><ObjectId>10</ObjectId><Code>E</Code><Name>Elec</Name></WBS>
  <WBS><ObjectId>11</ObjectId><Code>M</Code><Name>Mech</Name></WBS>
  <Activity><ObjectId>100</ObjectId><Id>A1000</Id><Name>Pull Wire</Name>
   <Type>Task Dependent</Type><Status>Not Started</Status>
   <PlannedDuration>60</PlannedDuration><RemainingDuration>60</RemainingDuration>
   <WBSObjectId>10</WBSObjectId></Activity>
 </Project></APIBusinessObjects>"""


@pytest.fixture(autouse=True)
def _clean():
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    yield


def _client():
    c = server.app.test_client()
    c.post("/api/upload", data={"file": (io.BytesIO(_XML.encode()), "rn.xml")},
           content_type="multipart/form-data")
    return c


def _rename(c, new_name, wbs_name="Elec"):
    return c.post("/api/direct", json={
        "commands": [{"action": "rename_wbs", "wbs_name": wbs_name,
                      "new_name": new_name}],
        "label": "rename"}).get_json()


def test_a_rename_is_not_structural():
    d = _rename(_client(), "Electrical")
    assert d["success"]
    assert not d["structural"], "a rename moved no rows — nothing to rebuild"


def test_the_response_says_which_header_to_repaint():
    d = _rename(_client(), "Electrical")
    assert [f["name"] for f in d["renamed_folders"]] == ["Electrical"]
    assert d["renamed_folders"][0]["uid"], "the client patches by uid"


def test_only_the_renamed_folder_is_reported():
    d = _rename(_client(), "Electrical")
    assert len(d["renamed_folders"]) == 1, "Mech did not change"


def test_an_ordinary_edit_reports_no_renames():
    c = _client()
    d = c.post("/api/direct", json={
        "commands": [{"action": "update_duration", "activity_id": "A1000",
                      "new_duration_days": 20}],
        "label": "stretch"}).get_json()
    assert d["renamed_folders"] == []
    assert not d["structural"]


def test_shape_changes_still_reload():
    """Adding a folder genuinely changes row placement — the reload stays."""
    c = _client()
    d = c.post("/api/direct", json={
        "commands": [{"action": "add_wbs", "name": "Commissioning"}],
        "label": "add folder"}).get_json()
    assert d["structural"], "a new folder changes the shape of the grid"


def test_a_rename_is_still_undoable():
    c = _client()
    _rename(c, "Electrical")
    u = c.post("/api/undo", json={}).get_json()
    assert u.get("undo_count") == 0 or not u.get("error")
    p = server._projects[server._active_id[0]]["project"]
    assert any(w.name == "Elec" for w in p.wbs_nodes), \
        "undo must put the old name back"
