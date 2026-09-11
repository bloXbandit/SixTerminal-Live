"""
test_excel_export.py — the tracker has to fit the NEXT job, not just this one.

The workbook started life as a one-off cut by hand for MDC1, and the things
that made it useful there are exactly the things that would have made it
useless anywhere else: the project code, the crew field, the phase blocks and
the contract dates were all typed in. Every test here holds one of those open.

The other half is Excel itself. openpyxl writes no spill metadata, so a
spilling function (FILTER, XLOOKUP, SORT, UNIQUE) fills one cell and leaves
the rest silently blank — the sheet looks built and reports nothing. And a
function Excel stores with an `_xlfn.` prefix is not the function you typed.
Both went wrong once; both are checked below.
"""

import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.excel_export import (CREW_TBD, CREW_WBO, _code_for, _collect,
                                 build_workbook)
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


# ── a small job, built the way a real one is ─────────────────────────────────

def _job(code="ACME", crew_field="Number of Electricians", phases=2):
    """
    Two phases, each an area of work running into a Substantial Completion
    milestone. The milestones sit together in one "Milestones" folder, which
    is where P6 actually puts them and the reason phase keying reads the name.
    """
    p = Project(uid="p", name="Test Job", id="99-0001-INT-1",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Test Job", code="TJ")]
    p.activities, p.relations = [], []

    p.wbs_nodes.append(WBSNode(uid="mil", name="Milestones", code="MIL",
                               parent_uid="root"))
    n = 0
    for ph in range(1, phases + 1):
        p.wbs_nodes.append(WBSNode(uid=f"ph{ph}", name=f"Phase {ph}",
                                   code=f"PH{ph}", parent_uid="root"))
        p.wbs_nodes.append(WBSNode(uid=f"ar{ph}", name=f"Data Hall {ph}",
                                   code=f"DH{ph}", parent_uid=f"ph{ph}"))
        prev = None
        for step in range(3):
            n += 1
            a = Activity(
                uid=f"a{n}", activity_id=f"{code}.PH{ph}.ER.{1000 + n * 10}",
                name=f"Conduit Rough In (Gen {200 + step})", wbs_uid=f"ar{ph}",
                calendar_uid="1", planned_duration=40, remaining_duration=40,
                planned_start=f"2026-0{ph}-05", planned_finish=f"2026-0{ph}-09",
                early_finish=f"2026-0{ph}-09", total_float=float(step * 8),
                udfs={crew_field: str(4 + step)} if crew_field else {})
            p.activities.append(a)
            if prev:
                p.relations.append(Relation(uid=f"r{n}", predecessor_uid=prev,
                                            successor_uid=a.uid))
            prev = a.uid
        m = Activity(uid=f"sc{ph}", activity_id=f"{code}.PH{ph}.C.OUT.9000",
                     name=f"Substantial Completion - Phase {ph} (PH{ph})",
                     wbs_uid="mil", calendar_uid="1",
                     activity_type="Finish Milestone",
                     planned_start=f"2026-0{ph}-10",
                     planned_finish=f"2026-0{ph}-10",
                     early_finish=f"2026-0{ph}-10")
        p.activities.append(m)
        p.relations.append(Relation(uid=f"rm{ph}", predecessor_uid=prev,
                                    successor_uid=m.uid))
    p.build_lookups()
    return p


@pytest.fixture
def book(tmp_path):
    def _build(project=None, **kw):
        out = str(tmp_path / "t.xlsx")
        build_workbook(project or _job(), out, **kw)
        return out
    return _build


def _formulas(path):
    """Every formula in the file, as written to the XML."""
    out = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("xl/worksheets/sheet"):
                out += re.findall(r"<f[^>]*>(.*?)</f>", z.read(n).decode("utf-8"))
    return out


def _text(path):
    with zipfile.ZipFile(path) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace")
                         for n in z.namelist() if n.endswith(".xml"))


# ── nothing about one job is baked in ────────────────────────────────────────

def test_the_project_code_comes_from_the_activity_ids():
    """P6 calls the job "25-1539-INT-3"; the crews call it MDC1, and that is
    what the activity codes carry. The codes win."""
    assert _code_for(_job(code="MDC2")) == "MDC2"


def test_a_schedule_with_no_id_prefix_falls_back_to_what_p6_calls_it():
    p = _job()
    for i, a in enumerate(p.activities):
        a.activity_id = f"A{1000 + i}"
    assert _code_for(p) == "A1000" or _code_for(p) == p.id


def test_a_weak_prefix_signal_is_not_trusted():
    """Four rows agreeing is a coincidence; the P6 id is the safer answer."""
    p = _job()
    p.activities = p.activities[:3]
    assert _code_for(p) == p.id


def test_no_other_jobs_details_leak_into_this_ones_workbook(book):
    """The roster and the previous job's code must not appear in a file built
    for a schedule that has nothing to do with either."""
    body = _text(book(_job(code="ZEBRA")))
    for stray in ("MDC1", "MDC2", "MDC3", "Kris", "Jerome", "Kyle"):
        assert stray not in body, f"{stray} was carried over from another job"
    assert "ZEBRA" in body


def test_the_crew_field_is_found_rather_than_assumed():
    """Every job names its headcount UDF differently. Hardcoding one means
    every other schedule exports a blank column."""
    rows = _collect(_job(crew_field="Crew Size"), "ACME")["rows"]
    assert any(r["crew"] for r in rows), "the crew column came out empty"


def test_a_schedule_with_no_udfs_at_all_still_exports(book):
    book(_job(crew_field=None))


# ── who is doing the work ────────────────────────────────────────────────────

def test_work_by_others_is_read_off_the_activity_name():
    p = _job()
    p.activities[0].name = "Conduit Rough In ** WBO"
    rows = {r["activity_id"]: r for r in _collect(p, "ACME")["rows"]}
    assert rows[p.activities[0].activity_id]["by"] == CREW_WBO


def test_unclaimed_work_says_so_rather_than_naming_a_crew():
    """Unassigned work is a question the PM wants to filter on, not a blank —
    and not a guess at who will end up doing it."""
    assert all(r["by"] == CREW_TBD for r in _collect(_job(), "ACME")["rows"])


def test_the_old_default_crew_name_is_gone(book):
    assert "Six Terminal" not in _text(book())


def test_the_crew_the_work_is_assigned_to_is_a_parameter(book):
    assert "Voltaic Electric" in _text(book(own_crew="Voltaic Electric"))


# ── the phase blocks ─────────────────────────────────────────────────────────

def test_each_phase_gets_its_own_chain_though_the_milestones_share_a_folder():
    """Every Substantial Completion milestone lives under "Milestones", so
    keying the chain on the folder collapsed three phases into one and only
    one block was ever drawn. The phase is in the milestone's NAME."""
    chains = _collect(_job(phases=3), "ACME")["chains"]
    assert set(chains) == {"Phase 1", "Phase 2", "Phase 3"}


def test_a_chain_walks_back_through_the_work_that_drives_the_date():
    chain = _collect(_job(), "ACME")["chains"]["Phase 1"]
    assert chain, "no driving chain was found"
    assert all(".PH1." in i for i in chain)
    assert chain == sorted(chain), "the chain should read in the order it runs"


def test_the_chain_carries_no_milestones():
    """A milestone has no duration to model, so modelling one shifts every
    date after it."""
    p = _job()
    chain = set(_collect(p, "ACME")["chains"]["Phase 1"])
    mils = {a.activity_id for a in p.activities
            if "Milestone" in (a.activity_type or "")}
    assert not (chain & mils)


def test_a_schedule_with_no_completion_milestone_still_exports(book):
    """Not every job names one, and the tracker must not need it."""
    p = _job()
    p.activities = [a for a in p.activities if "Substantial" not in a.name]
    p.build_lookups()
    book(p)


# ── percent complete is a fraction ───────────────────────────────────────────

def test_percent_complete_is_carried_as_p6_stores_it():
    """P6 keeps it 0..1. Dividing by 100 again made every dashboard read a
    hundredth of the truth."""
    p = _job()
    p.activities[0].percent_complete = 0.5
    rows = {r["activity_id"]: r for r in _collect(p, "ACME")["rows"]}
    assert rows[p.activities[0].activity_id]["pct"] == 0.5


# ── what Excel will actually do with the file ────────────────────────────────

def test_nothing_that_spills_is_written(book):
    """openpyxl writes no spill metadata, so a spilling formula fills one cell
    and leaves the rest blank — the sheet looks built and reports nothing."""
    bad = re.compile(r"\b(FILTER|XLOOKUP|SORT|SORTBY|UNIQUE|SEQUENCE|TEXTSPLIT)\s*\(",
                     re.I)
    offenders = [f for f in _formulas(book()) if bad.search(f)]
    assert not offenders, offenders[:3]


def test_the_newer_functions_carry_the_prefix_excel_stores_them_with(book):
    """MINIFS and MAXIFS are written `_xlfn.MINIFS`. Unprefixed, Excel opens
    the file and reports #NAME?."""
    for f in _formulas(book()):
        for fn in ("MINIFS", "MAXIFS", "IFS"):
            for m in re.finditer(r"(_xlfn\.)?\b" + fn + r"\s*\(", f):
                assert m.group(1), f"{fn} needs the _xlfn. prefix: {f[:90]}"


def test_the_working_day_maths_does_not_depend_on_workday_intl(book):
    """WORKDAY.INTL is stored prefixed and was evaluating to 0 — every
    Critical Path date came out as 1900-01-00. The six-day week is arithmetic
    now: +1, and +1 again only if that lands on a Sunday."""
    joined = " ".join(_formulas(book()))
    assert "WORKDAY" not in joined.upper()
    assert "WEEKDAY(" in joined


def test_the_controls_are_named_so_every_sheet_can_reach_them(book):
    from openpyxl import load_workbook
    names = set(load_workbook(book()).defined_names)
    assert {"StatusDate", "LookaheadWeeks", "ActivityIDs"} <= names


def test_the_activity_id_list_backs_the_type_ahead(book):
    """Typing an id into the Critical Path tab narrows against this range.
    Pointed at the wrong sheet it silently offers nothing."""
    from openpyxl import load_workbook
    dn = load_workbook(book()).defined_names["ActivityIDs"]
    assert "Data" in dn.value


def test_every_sheet_the_tracker_promises_is_there(book):
    from openpyxl import load_workbook
    assert load_workbook(book()).sheetnames == [
        "Start Here", "Update", "Dashboard", "Areas", "Critical Path",
        "Notes", "Data"]


def test_the_status_date_starts_at_the_projects_own_data_date(book):
    from openpyxl import load_workbook
    ws = load_workbook(book())["Start Here"]
    assert str(ws["B6"].value)[:10] == "2026-01-05"


def test_every_activity_reaches_the_workbook(book):
    """A tracker that quietly drops rows is worse than no tracker."""
    from openpyxl import load_workbook
    p = _job(phases=3)
    ws = load_workbook(book(p))["Data"]
    ids = {c.value for c in ws["A"] if isinstance(c.value, str)}
    assert {a.activity_id for a in p.activities} <= ids


# ── the export does not disturb the schedule ─────────────────────────────────

def test_building_the_tracker_reschedules_nothing(book):
    """It refreshes float to seed the critical tab, which must not become an
    edit to the dates anybody is working to."""
    p = _job()
    before = [(a.planned_start, a.planned_finish) for a in p.activities]
    book(p)
    assert [(a.planned_start, a.planned_finish) for a in p.activities] == before


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_endpoint_returns_a_workbook():
    import server
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "MyJob.xml")
    server._projects["J"]["project"] = _job()
    server._active_id[0] = "J"
    r = server.app.test_client().get("/api/download/excel")
    assert r.status_code == 200
    assert r.data[:2] == b"PK", "that is not an xlsx"
    assert "MyJob_Progress_Tracker.xlsx" in r.headers["Content-Disposition"]


def test_the_endpoint_says_so_when_nothing_is_loaded():
    import server
    server._projects.clear()
    server._active_id[0] = None
    r = server.app.test_client().get("/api/download/excel")
    assert r.status_code == 400
    assert "No schedule loaded" in r.get_json()["error"]


# ── the jobs that are not the subject project ────────────────────────────────
#
# The milestone table ran backwards (E14:E13) on a schedule with no milestones
# and openpyxl refused the whole file. That is a class of bug, not one bug —
# every range built from a len() has the same shape — so the degenerate
# schedules are exercised rather than assumed.

def test_a_one_activity_schedule_exports(book):
    p = _job()
    p.activities = p.activities[:1]
    p.relations = []
    p.build_lookups()
    book(p)


def test_a_schedule_with_no_relations_at_all_exports(book):
    p = _job()
    p.relations = []
    p.build_lookups()
    book(p)


def test_a_schedule_with_no_wbs_depth_exports(book):
    """Everything sitting at the root: no phase, no area to split on."""
    p = _job()
    p.wbs_nodes = [WBSNode(uid="root", name="Test Job", code="TJ")]
    for a in p.activities:
        a.wbs_uid = "root"
    p.build_lookups()
    book(p)


def test_a_schedule_with_no_dates_exports(book):
    """A freshly built job, before anybody has scheduled it."""
    p = _job()
    for a in p.activities:
        a.planned_start = a.planned_finish = a.early_finish = None
    p.build_lookups()
    book(p)


# ── the Flag column ──────────────────────────────────────────────────────────
#
# It was a locked formula headed "Flag", blank on most rows: it looked like
# something to click, did nothing when clicked, and showed nothing most of the
# time. Now there are two columns — one you set, one worked out for you — and
# the names say which is which.

def _col(path, sheet, letter, first=5, last=None):
    from openpyxl import load_workbook
    ws = load_workbook(path)[sheet]
    return [ws[f"{letter}{r}"].value
            for r in range(first, (last or ws.max_row) + 1)]


def test_the_flag_is_a_cell_the_field_can_actually_type_in(book):
    from openpyxl import load_workbook
    ws = load_workbook(book())["Update"]
    assert ws["W4"].value.startswith("Flag")
    assert ws["W5"].protection.locked is False, "the flag cannot be set"
    assert ws["W5"].value in (None, ""), "a flag is raised by a person, not seeded"


def test_the_flag_offers_the_reasons_work_stops_without_gating_them(book):
    """A dropdown that refuses anything else is a dropdown the field fights."""
    from openpyxl import load_workbook
    from engine.excel_export import FLAGS
    ws = load_workbook(book())["Update"]
    dv = [d for d in ws.data_validations.dataValidation if "W5" in str(d.sqref)]
    assert dv, "no dropdown on the flag column"
    assert all(f in dv[0].formula1 for f in FLAGS)
    assert dv[0].showErrorMessage is False, "typed text would be refused"


def test_the_column_that_is_computed_no_longer_calls_itself_a_flag(book):
    from openpyxl import load_workbook
    ws = load_workbook(book())["Update"]
    assert ws["V4"].value == "Next Step"


def test_the_computed_column_says_something_on_every_row(book):
    """Blank on most rows is why it read as broken. Every branch now lands."""
    joined = " ".join(f for f in _formulas(book()) if "Next Step" not in f)
    v = [f for f in _formulas(book()) if '"Done"' in f]
    assert v, "the Next Step formula is missing"
    assert '"Later"' in v[0], "the final branch still falls through to blank"


def test_a_raised_flag_reaches_the_dashboard(book):
    """Somewhere to type that nobody reads is worse than nowhere."""
    from openpyxl import load_workbook
    ws = load_workbook(book())["Dashboard"]
    assert ws["M6"].value.startswith("Flags")
    assert "$W$" in str(ws["M7"].value), "the count does not look at the flag column"


def test_blocked_outranks_complete_in_the_row_colouring(book):
    """Excel gives the first rule precedence, and a blocked row is the one
    thing that must not be painted over."""
    from openpyxl import load_workbook
    ws = load_workbook(book())["Update"]
    body = [rng for rng in ws.conditional_formatting
            if str(rng.sqref).startswith("A5:W")]
    assert body
    first = body[0].rules[0].formula[0]
    assert 'W5="Blocked"' in first, f"first rule is {first}"
