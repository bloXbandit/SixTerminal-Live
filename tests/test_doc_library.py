"""
test_doc_library.py — documents the agent can still use next week.

A document used to be read once and thrown away: the reading joined the
conversation, scrolled out sixteen turns later, and "pull the generator
quantities from that scope PDF" meant uploading it again. For a 497-line
document that is absurd — the lines had already been extracted, exactly, for
free.

The trap on the other side is just as bad: keeping 497 lines in every prompt
would cost more per turn than re-reading the document ever did. So the split
is the whole design, and it is what these tests defend —

    always in the prompt   one catalogue line per document
    on request             the twelve lines that answer the question

Plus the two things that make it usable in practice: a workbook's sheets and
columns survive without anybody writing a schema per file, and a document can
be found by whatever the user happens to call it.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine import doc_library as dl
from engine import project_brain as pb
from engine import scope_reader
from engine.schedule_model import Activity, Calendar, Project, WBSNode
from tests.test_scope_document import _make_pdf


def _project():
    p = Project(uid="1", name="DC", id="DOC-TEST", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Phase 2", code="P2")]
    p.activities = [Activity(
        uid="a1", activity_id="A1000", name="Set Generator", wbs_uid="w",
        calendar_uid="1", activity_type="Task Dependent", status="Not Started",
        planned_duration=40.0, remaining_duration=40.0,
        planned_start="2026-02-02", planned_finish="2026-02-06")]
    p.relations = []
    p.build_lookups()
    return p


def _xlsx(sheets):
    """A real workbook: {sheet name: [[cell, ...], ...]}."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title=title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_BOOK = {
    "Phase 2 Electrical": [
        ["Item", "Description", "Qty", "Unit"],
        [1, "Furnish and install 2500kW generators", 4, "EA"],
        [2, "Terminate generator feeders at switchgear", 4, "EA"],
    ],
    "Long Lead": [
        ["Item", "Equipment", "Lead time"],
        [1, "MV switchgear lineup", "38 weeks"],
        [2, "Standby generators", "52 weeks"],
    ],
}


# ── a workbook: every sheet, every column, no schema guessing ───────────────

def test_every_sheet_of_a_workbook_is_read():
    rep = scope_reader.read_excel(_xlsx(_BOOK))
    assert rep["sheets"] == ["Phase 2 Electrical", "Long Lead"]
    assert rep["pages"] == 2


def test_the_columns_of_a_row_arrive_together_as_one_line():
    """No schema is guessed per sheet — the words carry the meaning, which is
    what survives the next workbook being laid out differently."""
    rep = scope_reader.read_excel(_xlsx(_BOOK))
    text = " || ".join(l.text for l in rep["lines"])
    assert "Furnish and install 2500kW generators 4 EA" in text
    assert "MV switchgear lineup 38 weeks" in text


def test_blank_rows_and_bare_headers_do_not_become_content():
    book = {"S": [["Item", "Qty"], [], [1, 2], ["", ""],
                  ["Install four generators in phase two", 4]]}
    rep = scope_reader.read_excel(_xlsx(book))
    assert rep["line_count"] == 1


def test_a_workbook_with_nothing_in_it_says_so():
    with pytest.raises(RuntimeError) as e:
        scope_reader.read_excel(_xlsx({"Empty": [[], []]}))
    assert "no rows" in str(e.value)


def test_read_any_picks_the_reader_from_the_filename():
    assert scope_reader.read_any(_xlsx(_BOOK), "log.xlsx")["method"] == "excel"
    pdf = _make_pdf(["Furnish and install (4) generators, Phase 2"])
    assert scope_reader.read_any(pdf, "scope.pdf")["method"] in ("tables", "clustered", "text")


def test_a_csv_reads_too():
    """The header line is kept on purpose — in a spreadsheet it is what says
    what the columns mean, and dropping it loses that."""
    csv = b"Item,Description,Qty\n1,Furnish and install generators,4\n"
    rep = scope_reader.read_any(csv, "list.csv")
    assert rep["line_count"] == 2
    assert any("generators" in l.text for l in rep["lines"])


# ── the catalogue is small; the contents are not carried ────────────────────

def _lib_with_scope(n=497):
    from tests.test_scope_document import _big_scope
    lib = dl.Library()
    lib.add_text("scope-of-work.pdf", dl.PDF,
                 scope_reader.read_scope(_make_pdf(_big_scope(n), per_page=60)))
    return lib


def test_the_prompt_carries_one_line_per_document_not_the_document():
    """The token guard. Carrying 497 lines every turn would cost more than
    reading the document ever did."""
    block = _lib_with_scope().catalogue_block()
    assert "scope-of-work.pdf" in block and "497 lines" in block
    assert len(block) < 700, f"{len(block)} chars is not a catalogue"
    assert "Furnish and install" not in block, "contents reached the prompt"


def test_the_catalogue_tells_the_agent_how_to_open_one():
    assert "read_document" in _lib_with_scope().catalogue_block()


def test_no_documents_means_nothing_in_the_prompt():
    assert dl.Library().catalogue_block() == ""


def test_a_runaway_document_is_capped_and_says_so():
    lib = _lib_with_scope(dl.MAX_LINES + 500)
    doc = lib.docs[0]
    assert doc.truncated and len(doc.lines) == dl.MAX_LINES
    assert doc.line_count == dl.MAX_LINES + 500, "the real size must still be reported"
    assert "kept" in doc.label()


# ── finding a document by whatever it was called ────────────────────────────

def test_a_document_is_found_by_a_loose_name():
    lib = _lib_with_scope()
    for asked in ("scope-of-work.pdf", "the scope pdf", "scope", "SCOPE OF WORK"):
        assert lib.find(asked) is not None, asked


def test_the_right_document_is_picked_out_of_several():
    lib = _lib_with_scope()
    lib.add_text("long-lead-log.xlsx", dl.SPREADSHEET,
                 scope_reader.read_excel(_xlsx(_BOOK)))
    assert lib.find("long lead").name == "long-lead-log.xlsx"
    assert lib.find("scope").name == "scope-of-work.pdf"


def test_a_name_that_matches_nothing_returns_nothing():
    assert _lib_with_scope().find("submittal register") is None


def test_re_uploading_the_same_file_replaces_it():
    """Otherwise "the scope PDF" turns ambiguous the moment a corrected copy
    arrives."""
    lib = _lib_with_scope()
    lib.add_text("scope-of-work.pdf", dl.PDF,
                 scope_reader.read_scope(_make_pdf(["Install (9) chillers, Phase 1"])))
    assert len(lib.docs) == 1
    assert lib.docs[0].line_count == 1


def test_old_documents_fall_off_rather_than_accumulating():
    lib = dl.Library()
    for i in range(dl.MAX_DOCS + 5):
        lib.add_text(f"doc-{i}.pdf", dl.PDF,
                     {"lines": [], "pages": 1, "sheets": []})
    assert len(lib.docs) == dl.MAX_DOCS
    assert lib.find("doc-0.pdf") is None


def test_a_request_that_does_not_distinguish_returns_nothing():
    """A tie means the request did not pick one out. Handing back whichever
    sorted first would be the wrong document with no sign it was a guess —
    the caller says what it has instead."""
    lib = dl.Library()
    for name in ("rfi-log.pdf", "rfi-responses.pdf"):
        lib.add_text(name, dl.PDF, {"lines": [], "pages": 1, "sheets": []})
    assert lib.find("the rfi pdf") is None          # matches both equally
    assert lib.find("rfi responses").name == "rfi-responses.pdf"


# ── searching one, without sending the file ─────────────────────────────────

def test_a_search_returns_the_matching_lines_not_the_document():
    lib = _lib_with_scope()
    got = lib.search(lib.docs[0], "generator", limit=12)
    assert 0 < len(got["lines"]) <= 12
    assert got["matched"] > 12, "there should be more matches than were sent"
    assert all("generator" in r["text"].lower() for r in got["lines"])


def test_a_search_says_where_each_line_came_from():
    lib = _lib_with_scope()
    got = lib.search(lib.docs[0], "generator")
    assert all(r["where"] for r in got["lines"])


def test_a_search_with_no_query_gives_the_opening_lines():
    """What "what's in this file?" actually wants."""
    lib = _lib_with_scope()
    got = lib.search(lib.docs[0], "", limit=5)
    assert len(got["lines"]) == 5 and got["lines"][0]["n"] == 1


def test_a_search_that_finds_nothing_is_empty_not_a_guess():
    lib = _lib_with_scope()
    assert lib.search(lib.docs[0], "zzzznotinhere")["lines"] == []


def test_the_hit_limit_is_bounded_however_much_is_asked_for():
    lib = _lib_with_scope()
    assert len(lib.search(lib.docs[0], "generator", limit=9999)["lines"]) <= dl.MAX_HITS


# ── images are held differently, on purpose ─────────────────────────────────

def test_an_image_keeps_its_reading_rather_than_pretending_to_be_text():
    lib = dl.Library()
    doc = lib.add_image("E03-021AB", "Grounding plan for segments A and B",
                        ["Ground grid ties to building steel"])
    assert not doc.searchable
    assert "Grounding plan" in doc.label()
    got = lib.search(doc, "grounding")
    assert got["lines"] == [] and "image" in got["note"]


def test_an_image_can_still_be_found_by_its_sheet_number():
    lib = dl.Library()
    lib.add_image("E03-021AB", "Grounding plan", [])
    assert lib.find("E03-021AB") is not None


# ── it survives with the job ────────────────────────────────────────────────

def test_the_library_survives_being_saved_and_reloaded():
    b = pb.Brain("k")
    b.library = _lib_with_scope()
    back = pb.Brain.from_json(b.to_json())
    assert back.library is not None
    doc = back.library.find("scope")
    assert doc.line_count == 497
    assert back.library.search(doc, "generator")["lines"]


def test_a_library_alone_is_worth_saving():
    b = pb.Brain("k")
    b.docs().add_image("E1", "a sheet", [])
    assert not b.is_empty()


def test_a_corrupt_saved_library_is_dropped_not_crashed_on():
    back = pb.Brain.from_json({"key": "k", "library": {"docs": [{"nope": 1}]}})
    assert back.library is None or not back.library.docs


# ── the agent reaching for one ──────────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _upload(c, blob, name):
    return c.post("/api/documents",
                  data={"file": (io.BytesIO(blob), name)},
                  content_type="multipart/form-data")


def test_a_workbook_can_be_filed_and_listed():
    c = _client()
    body = _upload(c, _xlsx(_BOOK), "long-lead.xlsx").get_json()
    assert body["success"] and body["document"]["sheets"] == list(_BOOK)
    listed = c.get("/api/documents").get_json()["documents"]
    assert listed[0]["name"] == "long-lead.xlsx"


def test_filing_a_document_changes_nothing_in_the_schedule():
    c = _client()
    before = [(a.activity_id, a.name) for a in server._projects["t"]["project"].activities]
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    p = server._projects["t"]["project"]
    assert [(a.activity_id, a.name) for a in p.activities] == before
    assert p.relations == []


def test_an_unsupported_file_type_is_refused():
    resp = _upload(_client(), b"x", "notes.docx")
    assert resp.status_code == 400 and "PDF" in resp.get_json()["error"]


def test_the_search_route_returns_lines_from_the_named_document():
    c = _client()
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    got = c.get("/api/documents/search?document=long lead&q=switchgear").get_json()
    assert got["lines"] and "switchgear" in got["lines"][0]["text"].lower()


def test_the_search_route_names_what_it_has_when_asked_for_the_wrong_file():
    c = _client()
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    resp = c.get("/api/documents/search?document=nonexistent")
    assert resp.status_code == 404
    assert "long-lead.xlsx" in resp.get_json()["have"]


def test_a_document_can_be_removed():
    c = _client()
    doc_id = _upload(c, _xlsx(_BOOK), "long-lead.xlsx").get_json()["document"]["id"]
    assert c.delete(f"/api/documents/{doc_id}").status_code == 200
    assert c.get("/api/documents").get_json()["documents"] == []


# ── read_document, the action the agent actually uses ───────────────────────

def _run(c, commands):
    return c.post("/api/edit", json={"instruction": "look it up",
                                     "force_commands": commands}).get_json()


def test_the_agent_can_read_a_filed_document_by_name():
    c = _client()
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    body = _run(c, [{"action": "read_document", "document": "long lead",
                     "query": "generator"}])
    msg = body["results"][0]["message"]
    assert body["results"][0]["success"]
    assert "generator" in msg.lower()


def test_reading_a_document_is_advisory_and_counts_as_a_check_not_an_edit():
    """The same rule as any report: it looked, it did not touch."""
    c = _client()
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    body = _run(c, [{"action": "read_document", "document": "long lead"}])
    assert body["edits_made"] == 0 and body["checks_run"] == 1


def test_asking_for_a_document_that_was_never_given_names_what_there_is():
    c = _client()
    _upload(c, _xlsx(_BOOK), "long-lead.xlsx")
    body = _run(c, [{"action": "read_document", "document": "the drawings"}])
    assert not body["results"][0]["success"]
    assert "long-lead.xlsx" in body["results"][0]["message"]


def test_reading_with_no_documents_at_all_says_to_attach_one():
    body = _run(_client(), [{"action": "read_document", "document": "anything"}])
    assert not body["results"][0]["success"]
    assert "attach" in body["results"][0]["message"].lower()


def test_a_scope_upload_is_also_filed_so_it_can_be_reopened():
    """Reading it into the sequencing graph and keeping it to consult are two
    different jobs, and the second used to be missing."""
    from tests.test_scope_document import _make_pdf as mk
    c = _client()
    c.post("/api/scope", data={"file": (io.BytesIO(mk(
        ["Furnish and install (4) 2500kW generators, Phase 2",
         "Terminate generator feeders at MV switchgear, Phase 2"])), "scope.pdf")},
        content_type="multipart/form-data")
    body = _run(c, [{"action": "read_document", "document": "scope",
                     "query": "generator"}])
    assert body["results"][0]["success"]
    assert "2500kW" in body["results"][0]["message"]


def test_a_drawing_is_filed_so_it_can_be_referred_back_to(monkeypatch):
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: {
        "sheet_number": "E03-021AB", "sheet_title": "Grounding Plan",
        "discipline": "electrical", "summary": "Grounding for segments A/B",
        "rooms": [], "equipment": [], "facts": ["Ground grid under MV 105"],
        "directives": []})
    c = _client()
    c.post("/api/brain/image", data={"file": (io.BytesIO(b"png"), "snip.png")},
           content_type="multipart/form-data")
    body = _run(c, [{"action": "read_document", "document": "E03-021AB"}])
    assert body["results"][0]["success"]
    assert "Ground grid under MV 105" in body["results"][0]["message"]
