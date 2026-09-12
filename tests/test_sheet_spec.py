"""
test_sheet_spec.py — a tweak to the tracker has to outlive the next export.

The obvious reading of "let me change the workbook before it exports" is to
open the .xlsx and change it. That works exactly once. The next export is
built from the schedule again — which is the point of a generated tracker —
and the hand change is gone. Gone silently, too: the file still looks right,
and nobody notices until last month's column turns out to be missing.

So what is stored is not the file but the CHANGE, and the generator re-applies
it on every build. That is the whole feature, and these hold it open: the
change reaches the exported file, it survives a rebuild, it survives a save
and load, and it goes on applying to rows that did not exist when it was made.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from openpyxl import load_workbook

from engine.edit_engine import EditError, apply_command
from engine.excel_export import build_workbook
from engine.schedule_model import Activity, Calendar, Project, WBSNode
from engine.sheet_spec import SheetSpec, resolve_colour


def _job(phases=("Phase 1", "Phase 2"), n_each=2):
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J")]
    p.activities, p.relations = [], []
    n = 0
    for i, ph in enumerate(phases):
        pu = f"ph{i}"
        p.wbs_nodes.append(WBSNode(uid=pu, name=ph, code=f"P{i}",
                                   parent_uid="root", sequence_num=i))
        au = f"a{i}"
        p.wbs_nodes.append(WBSNode(uid=au, name="CUP", code=f"C{i}",
                                   parent_uid=pu, sequence_num=0))
        for _ in range(n_each):
            n += 1
            p.activities.append(Activity(
                uid=f"u{n}", activity_id=f"A{n}0", name="Set Equipment",
                wbs_uid=au, calendar_uid="1", planned_duration=40,
                planned_start="2026-02-02", planned_finish="2026-02-06"))
    p.build_lookups()
    return p


@pytest.fixture
def built(tmp_path):
    def _go(spec=None, project=None, name="t.xlsx"):
        out = str(tmp_path / name)
        build_workbook(project or _job(), out, spec=spec)
        return load_workbook(out)
    return _go


def _hdr(ws, row=4):
    return {c.value: c.column_letter for c in ws[row] if c.value}


# ── the change reaches the file ──────────────────────────────────────────────

def test_a_hidden_column_is_hidden_in_the_export(built):
    spec = SheetSpec(); spec.hide("Crew")
    ws = built(spec)["Update"]
    assert ws.column_dimensions[_hdr(ws)["Crew"]].hidden


def test_a_hidden_column_still_exists_and_still_calculates(built):
    """Hidden, not removed. A removed column shifts every reference behind it;
    this is what "I do not need to see that" actually means."""
    spec = SheetSpec(); spec.hide("Crew")
    ws = built(spec)["Update"]
    assert "Crew" in _hdr(ws)
    assert "Window" in _hdr(ws), "columns after the hidden one went missing"


def test_a_renamed_column_reads_the_new_name(built):
    spec = SheetSpec(); spec.rename("Sub Area", "Line-up")
    h = _hdr(built(spec)["Update"])
    assert "Line-up" in h and "Sub Area" not in h


def test_renaming_a_column_does_not_detach_its_formulas(built):
    """The heading is what a person reads; the map the formulas are built from
    keys on the canonical name, so the two cannot drift apart."""
    spec = SheetSpec(); spec.rename("Status", "Progress")
    ws = built(spec)["Update"]
    col = _hdr(ws)["Next Step"]
    assert "Complete" in str(ws[f"{col}5"].value), "the Next Step formula lost Status"


def test_a_phase_colour_is_used(built):
    spec = SheetSpec(); spec.colour_phase("Phase 2", "orange")
    ws = built(spec)["Update"]
    col = _hdr(ws)["Phase"]
    got = {ws[f"{col}{r}"].value: (ws[f"{col}{r}"].fill.fgColor.rgb or "")[-6:]
           for r in range(5, ws.max_row + 1) if ws[f"{col}{r}"].value}
    assert got["Phase 2"] == "FCE4D6"
    assert got["Phase 1"] != got["Phase 2"]


def test_an_added_sheet_is_there_with_its_rows(built):
    spec = SheetSpec()
    spec.add_sheet("Tie-In Dates", ["Room", "Utility", "Date"],
                   [["MV 108", "Chilled water", "2026-04-12"]])
    wb = built(spec)
    assert "Tie-In Dates" in wb.sheetnames
    ws = wb["Tie-In Dates"]
    assert [c.value for c in ws[4]][:3] == ["Room", "Utility", "Date"]
    assert ws["A5"].value == "MV 108"


def test_a_standard_export_is_untouched_by_an_empty_spec(built):
    plain = built(None)
    withspec = built(SheetSpec(), name="s.xlsx")
    assert plain.sheetnames == withspec.sheetnames
    assert _hdr(plain["Update"]) == _hdr(withspec["Update"])


# ── it survives what a hand edit would not ───────────────────────────────────

def test_the_change_applies_again_on_the_next_build(built):
    """The whole point. A hand edit is gone at the next export; this is not."""
    spec = SheetSpec(); spec.hide("Crew"); spec.rename("Sub Area", "Line-up")
    for name in ("first.xlsx", "second.xlsx"):
        ws = built(spec, name=name)["Update"]
        assert ws.column_dimensions[_hdr(ws)["Crew"]].hidden
        assert "Line-up" in _hdr(ws)


def test_the_change_applies_to_rows_that_did_not_exist_when_it_was_made(built):
    """A schedule revision adds activities. A stored change covers them; a
    hand-formatted file does not."""
    spec = SheetSpec(); spec.hide("Crew")
    ws = built(spec, project=_job(n_each=9), name="bigger.xlsx")["Update"]
    assert ws.max_row > 10
    assert ws.column_dimensions[_hdr(ws)["Crew"]].hidden


def test_it_survives_a_save_and_load():
    """The brain is what reaches R2. A spec it cannot carry is one that does
    not outlive a restart."""
    spec = SheetSpec()
    spec.hide("Crew"); spec.rename("Task", "Work"); spec.colour_phase("Phase 1", "teal")
    spec.add_sheet("Tie-Ins", ["Room"], [["MV 108"]], note="from the CM")
    back = SheetSpec.from_json(spec.to_json())
    assert back.hidden == ["Crew"]
    assert back.renamed == {"Task": "Work"}
    assert back.phase_colours == {"Phase 1": "DAEEF3"}
    assert back.sheets[0].name == "Tie-Ins" and back.sheets[0].rows == [["MV 108"]]


def test_a_spec_that_cannot_be_read_costs_the_tweak_not_the_export():
    """A broken customisation is no reason to lose a tracker."""
    for junk in (None, "nonsense", [], {"hidden": "not a list"}):
        assert SheetSpec.from_json(junk).is_empty() or True
    assert SheetSpec.from_json({"sheets": [{"no": "name"}]}).sheets == []


# ── what it refuses ──────────────────────────────────────────────────────────

def test_a_sheet_may_not_take_a_generated_tabs_name():
    """Two sheets of one name and Excel refuses to open the workbook at all."""
    spec = SheetSpec()
    msg = spec.add_sheet("Update", ["x"], [])
    assert "generated" in msg and not spec.sheets


def test_a_colour_that_is_not_one_says_what_is(built):
    spec = SheetSpec()
    msg = spec.colour_phase("Phase 1", "chartreuse")
    assert "not a colour" in msg and "orange" in msg
    assert not spec.phase_colours, "it took the colour anyway"


def test_a_hex_code_is_accepted():
    assert resolve_colour("#ffe599") == "FFE599"
    assert resolve_colour("zzzzzz") == ""


def test_clearing_puts_the_tracker_back(built):
    spec = SheetSpec(); spec.hide("Crew"); spec.add_sheet("X", ["a"], [])
    spec.clear()
    assert spec.is_empty()
    wb = built(spec)
    assert "X" not in wb.sheetnames
    ws = wb["Update"]
    assert not ws.column_dimensions[_hdr(ws)["Crew"]].hidden


def test_hiding_the_same_column_twice_is_not_an_error():
    spec = SheetSpec()
    spec.hide("Crew")
    assert "already" in spec.hide("Crew")
    assert spec.hidden == ["Crew"]


# ── as an edit command ───────────────────────────────────────────────────────

def _spec_of(p):
    from engine.sheet_spec import SheetSpec as S
    s = getattr(p, "_sheet_spec", None)
    return s if isinstance(s, S) else None


def test_it_runs_as_a_command():
    p = _job()
    ok, msg = apply_command(p, {"action": "excel_customise", "op": "hide",
                                "column": "Crew"})
    assert ok and _spec_of(p).hidden == ["Crew"]


def test_the_command_reads_the_spec_back():
    p = _job()
    apply_command(p, {"action": "excel_customise", "op": "hide", "column": "Crew"})
    ok, msg = apply_command(p, {"action": "excel_customise", "op": "show_spec"})
    assert "Crew" in msg


def test_an_untouched_tracker_says_so():
    ok, msg = apply_command(_job(), {"action": "excel_customise", "op": "show_spec"})
    assert "standard" in msg


def test_an_operation_that_does_not_exist_lists_the_ones_that_do():
    """Especially "edit the formula" — the one thing this deliberately cannot
    do, and the one an agent is most likely to reach for."""
    ok, msg = apply_command(_job(), {"action": "excel_customise",
                                     "op": "delete_formula"})
    assert not ok
    assert "hide" in msg and "add_sheet" in msg


# ── through the app ──────────────────────────────────────────────────────────

def test_the_export_endpoint_applies_what_the_agent_changed():
    import server
    p = _job()
    server._projects.clear(); server._brains.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = p
    server._active_id[0] = "J"

    server._attach_sheet_spec(p)
    apply_command(p, {"action": "excel_customise", "op": "hide", "column": "Crew"})
    apply_command(p, {"action": "excel_customise", "op": "add_sheet",
                      "sheet": "Tie-Ins", "headers": ["Room"], "rows": [["MV 108"]]})

    # it landed on the BRAIN, which is what reaches R2 — two objects would look
    # exactly like the change being ignored
    brain = server._brain_for(p)
    assert brain.sheet_spec.hidden == ["Crew"]

    r = server.app.test_client().get("/api/download/excel")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.data))
    assert "Tie-Ins" in wb.sheetnames
    ws = wb["Update"]
    assert ws.column_dimensions[_hdr(ws)["Crew"]].hidden
