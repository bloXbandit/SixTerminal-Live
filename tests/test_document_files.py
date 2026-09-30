"""
test_document_files.py — keep the file, not just what was read out of it.

A PDF was read for its text and then dropped on the floor. That is fine right
up to the first time somebody asks to see the drawing again — and then there
is nothing to show, because what survived the read is a list of lines with the
layout, the tables and the figures gone.

So the original is kept beside the schedule it belongs to, and can be taken
back exactly as it arrived. Three things have to hold: the bytes come back
unchanged, the name comes back with them, and a failure to keep a file never
costs the reading that was already done.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.doc_library import Document, Library
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _pdf(text="RFI 214 Conduit routing Gen 316"):
    """A small but genuinely valid PDF — the reader rejects anything less."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ]
    stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offs:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, x))
    return bytes(out)


@pytest.fixture
def app():
    import server
    p = Project(uid="1", name="J", id="J", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities = [Activity(uid="a1", activity_id="A10", name="T",
                             wbs_uid="w", calendar_uid="1")]
    p.relations = []
    p.build_lookups()
    server._projects.clear()
    server._brains.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = p
    server._active_id[0] = "J"
    return server.app.test_client()


def _upload(client, blob, name):
    r = client.post("/api/documents",
                    data={"file": (io.BytesIO(blob), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return client.get("/api/documents").get_json()["documents"][0]


# ── the file survives ────────────────────────────────────────────────────────

def test_the_original_comes_back_byte_for_byte(app):
    blob = _pdf()
    doc = _upload(app, blob, "RFI 214 - rev B.pdf")
    got = app.get(f"/api/documents/{doc['id']}/file")
    assert got.status_code == 200
    assert got.data == blob, "the file that came back is not the file that went in"


def test_the_name_comes_back_with_it(app):
    """A file called "RFI 214 - rev B.pdf" should not come back as an id."""
    doc = _upload(app, _pdf(), "RFI 214 - rev B.pdf")
    cd = app.get(f"/api/documents/{doc['id']}/file").headers["Content-Disposition"]
    assert "RFI 214 - rev B.pdf" in cd


def test_the_listing_says_whether_there_is_a_file_to_fetch(app):
    """Offering a download that cannot work is worse than not offering one."""
    doc = _upload(app, _pdf(), "spec.pdf")
    assert doc["has_file"] is True
    assert doc["file_bytes"] > 0


def test_the_text_is_still_read_and_searchable(app):
    """Keeping the file must not cost the reading — that is what the agent
    actually uses."""
    _upload(app, _pdf("Conduit routing for Gen 316"), "rfi.pdf")
    d = app.get("/api/documents/search?document=rfi&q=conduit").get_json()
    assert d["success"]
    assert any("conduit" in (l["text"] or "").lower() for l in d["lines"])


# ── removing one takes the file with it ──────────────────────────────────────

def test_removing_a_document_removes_its_file(app):
    """An entry gone and the bytes left behind is how a bucket fills with
    objects nothing refers to any more."""
    doc = _upload(app, _pdf(), "gone.pdf")
    assert app.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert app.get(f"/api/documents/{doc['id']}/file").status_code == 404


# ── the cases that must not become errors ────────────────────────────────────

def test_a_document_with_no_file_kept_says_so_plainly(app):
    """Everything filed before this existed has its text and no original. That
    is a sentence to read, not a stack trace."""
    import server
    brain = server._brain_for(server._projects["J"]["project"])
    doc = brain.docs().add_text("old.pdf", "pdf", {"lines": ["a line"], "pages": 1})
    r = app.get(f"/api/documents/{doc.id}/file")
    assert r.status_code == 404
    assert "not kept" in r.get_json()["error"]


def test_asking_for_a_document_that_does_not_exist(app):
    assert app.get("/api/documents/nope/file").status_code == 404


def test_a_failure_to_keep_the_file_never_loses_the_reading(app, monkeypatch):
    """The reading is the thing the agent works from. A storage problem must
    cost the convenience, not the content."""
    import server
    monkeypatch.setattr(server, "_local_doc_path",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    doc = _upload(app, _pdf(), "rfi.pdf")
    assert doc["has_file"] is False, "it claimed to have kept a file it could not"
    assert doc["line_count"] > 0, "the reading was lost with the file"
    d = app.get("/api/documents/search?document=rfi&q=conduit").get_json()
    assert d["success"] and d["lines"]


# ── the record of it ─────────────────────────────────────────────────────────

def test_the_library_remembers_a_file_across_a_save_and_load():
    """The brain is what reaches R2; a flag it does not carry is a flag that
    does not survive a restart."""
    lib = Library([Document(id="x", name="n.pdf", kind="pdf")])
    lib.mark_file("x", ".pdf", 4096)
    back = Library.from_json(lib.to_json())
    d = back.docs[0]
    assert d.has_file and d.file_ext == ".pdf" and d.file_bytes == 4096


def test_a_document_from_before_this_feature_loads_without_it():
    """An older manifest has no has_file at all and must still open."""
    old = {"docs": [{"id": "x", "name": "n.pdf", "kind": "pdf",
                     "lines": ["a"], "places": ["1"], "line_count": 1}]}
    back = Library.from_json(old)
    assert back is not None and back.docs[0].has_file is False


# ── every path that files a document keeps the file ──────────────────────────

def test_a_pdf_dropped_in_the_chat_keeps_the_file_not_only_the_reading(app, monkeypatch):
    """
    A chat drop classifies a PDF as a drawing sheet and reads it with vision.
    That is fine — but the reading is a summary and a dozen facts, and the
    drawing is the drawing. Throwing the original away put the document in the
    library with Download greyed out, which is the worst of both: it looks
    filed and it is gone.
    """
    import io

    from interpreter import vision as _vision

    monkeypatch.setattr(_vision, "classify_image_intent", lambda *a, **k: "drawing")
    monkeypatch.setattr(_vision, "read_drawing", lambda *a, **k: {
        "sheet_number": "E-101", "sheet_title": "Power Plan",
        "discipline": "electrical", "summary": "Gen room power",
        "facts": ["Gen 315 feeds MV 101"], "directives": []})

    blob = b"%PDF-1.4 not really a pdf but never parsed here"
    r = app.post(
        "/api/brain/image",
        data={"file": (io.BytesIO(blob), "E-101.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]

    docs = app.get("/api/documents").get_json()["documents"]
    assert len(docs) == 1
    assert docs[0]["has_file"] is True, "the sheet was read and then discarded"

    back = app.get(f"/api/documents/{docs[0]['id']}/file")
    assert back.status_code == 200 and back.data == blob


def test_a_scope_pdf_keeps_its_file_too(app, monkeypatch):
    """The extraction is lossy wherever it happens, so the rule is the same."""
    import io

    from engine import scope_graph as _sg

    # A real ScopeGraph, not a stand-in — a hand-rolled stub only tracks the
    # interface until the endpoint reaches for the next method on it.
    from engine.scope_graph import ScopeGraph, ScopeNode
    g = ScopeGraph()
    g.nodes = {"conduit": ScopeNode(system="conduit", stage="rough_in",
                                    phase="Phase 1")}
    g.classified = 1
    monkeypatch.setattr(_sg, "read_and_build", lambda b, n: (
        g, {"line_count": 10, "pages": 2, "lines": [], "sheets": [],
            "method": "text-layer"}))

    blob = b"%PDF-1.4 scope"
    r = app.post(
        "/api/scope", data={"file": (io.BytesIO(blob), "scope.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    docs = app.get("/api/documents").get_json()["documents"]
    assert docs[0]["has_file"] is True


def test_the_download_comes_back_under_the_name_it_was_sent_with(app, monkeypatch):
    """
    A drawing is filed under its sheet number, because "E-101" is what anyone
    looking for it will say. But the DOWNLOAD has to be the file that was sent
    — "E-101" with no extension is bytes the user's machine will not open, so
    the round trip fails at the last step while looking like it worked.
    """
    from interpreter import vision as _vision
    monkeypatch.setattr(_vision, "classify_image_intent", lambda *a, **k: "drawing")
    monkeypatch.setattr(_vision, "read_drawing", lambda *a, **k: {
        "sheet_number": "E-101", "sheet_title": "Power Plan",
        "discipline": "electrical", "summary": "s", "facts": [], "directives": []})

    blob = b"%PDF-1.4 sheet"
    app.post("/api/brain/image",
             data={"file": (io.BytesIO(blob), "MDC1 Lookahead Wk45.pdf")},
             content_type="multipart/form-data")
    doc = app.get("/api/documents").get_json()["documents"][0]
    assert doc["name"] == "E-101", "filed under something other than the sheet"
    assert doc["file_name"] == "MDC1 Lookahead Wk45.pdf", \
        "the library cannot say which upload this was"

    r = app.get(f"/api/documents/{doc['id']}/file")
    assert r.data == blob
    assert "MDC1 Lookahead Wk45.pdf" in r.headers["Content-Disposition"]


def test_a_document_kept_before_the_name_was_recorded_still_downloads(app):
    """An older manifest has no file_name. It must fall back, not 500."""
    doc = _upload(app, _pdf(), "old.pdf")
    lib = __import__("server")._brain_for(
        __import__("server")._projects["J"]["project"]).library
    lib.docs[0].file_name = ""                 # as an old manifest loads
    r = app.get(f"/api/documents/{doc['id']}/file")
    assert r.status_code == 200
    assert "old" in r.headers["Content-Disposition"]


# ── the schedule you loaded is a document too ────────────────────────────────

def test_the_uploaded_schedule_is_archived_and_downloadable(tmp_path):
    """
    The app holds a MODEL of the schedule, not the schedule. A re-export is
    this app's rendering of it, and anything the model does not carry is gone
    from that rendering forever. The original is the only copy of what P6
    actually sent — and "can I get back the XER I loaded in September" had no
    other answer.
    """
    import server

    server._projects.clear()
    server._brains.clear()
    c = server.app.test_client()

    blob = (b'<?xml version="1.0"?><APIBusinessObjects>'
            b"<Project><Id>J</Id><Name>J</Name><ObjectId>1</ObjectId></Project>"
            b"</APIBusinessObjects>")
    r = c.post("/api/upload",
               data={"file": (io.BytesIO(blob), "sept-baseline.xml")},
               content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]

    docs = c.get("/api/documents").get_json().get("documents", [])
    mine = [d for d in docs if d["name"] == "sept-baseline.xml"]
    assert mine, f"the upload was not archived: {[d['name'] for d in docs]}"
    assert mine[0]["has_file"], "archived without the file"

    back = c.get(f"/api/documents/{mine[0]['id']}/file")
    assert back.status_code == 200
    assert back.data == blob, "what came back is not what was uploaded"


def test_a_failed_archive_never_costs_the_upload():
    """Filing a copy is a convenience. Losing the schedule over it is not."""
    import server

    server._projects.clear()
    server._brains.clear()
    real = server._keep_document_file

    def boom(*a, **k):
        raise RuntimeError("no room at the inn")

    server._keep_document_file = boom
    try:
        blob = (b'<?xml version="1.0"?><APIBusinessObjects>'
                b"<Project><Id>J</Id><Name>J</Name><ObjectId>1</ObjectId></Project>"
                b"</APIBusinessObjects>")
        r = server.app.test_client().post(
            "/api/upload", data={"file": (io.BytesIO(blob), "x.xml")},
            content_type="multipart/form-data")
        assert r.status_code == 200, "the upload failed because the archive did"
    finally:
        server._keep_document_file = real


# ── the keep has to say when it did not really keep ──────────────────────────

def _upload_a_schedule(tmp_path):
    """Put a real schedule through /api/upload and return (client, response)."""
    from engine.schedule_model import Relation
    from engine.xml_writer import write_p6_xml
    import server

    p = Project(uid="1", name="Probe", id="PROBE-KEEP",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
    p.activities = [Activity(uid="u1", activity_id="A10", name="Pull wire",
                             wbs_uid="w", calendar_uid="1",
                             planned_duration=40, remaining_duration=40,
                             planned_start="2026-02-02",
                             planned_finish="2026-02-06")]
    p.relations = []
    p.build_lookups()
    src = str(tmp_path / "probe.xml")
    write_p6_xml(p, src)

    c = server.app.test_client()
    with open(src, "rb") as fh:
        r = c.post("/api/upload",
                   data={"file": (io.BytesIO(fh.read()), "probe.xml")},
                   content_type="multipart/form-data")
    return server, c, r


def test_uploading_a_schedule_files_the_original(tmp_path):
    """
    The app holds a MODEL of the schedule, not the schedule. A re-export is
    this app's rendering of it, and anything it does not model is gone from
    that rendering forever — so the file as it arrived is the only copy of
    what P6 actually sent.
    """
    _, c, r = _upload_a_schedule(tmp_path)
    assert r.status_code == 200
    docs = c.get("/api/documents").get_json()["documents"]
    mine = [d for d in docs if d["name"] == "probe.xml"]
    assert mine, "the uploaded schedule was not filed at all"
    assert mine[0]["has_file"], "filed with nothing behind it"
    assert mine[0]["kind"] == "schedule"


def test_an_archive_that_will_not_survive_says_so(tmp_path):
    """
    The bug this exists for. The keep is best-effort by design, so a full
    disk, a read-only mount or an unconfigured bucket all came out as silence
    — and a copy written to a hosted container's local disk is gone on the
    next restart. The document goes on being listed with nothing behind it,
    which a day later looks exactly like an archive that never happened.

    Being unable to keep it durably is acceptable. Not saying so is not.
    """
    server, c, _ = _upload_a_schedule(tmp_path)
    if server.cloud_store.is_configured():
        pytest.skip("cloud storage is configured here, so the keep is durable")
    said = [m for m in (server._get_session()["chat_history"] or [])
            if "not filed durably" in str(m.get("text", ""))]
    assert said, "kept it somewhere temporary and never mentioned it"
    assert "wiped when the host restarts" in str(said[0].get("context", ""))


def test_the_keep_reports_its_reason_rather_than_swallowing_it(tmp_path):
    """_keep_document_file returns '' only when the file is durably kept."""
    import server
    doc = Document(id="d1", name="x.pdf", kind="pdf", added_at="2026-01-01")
    why = server._keep_document_file("no-such-project", doc, b"bytes", "x.pdf")
    assert why, "a keep against a project that does not exist reported success"


def test_revising_onto_a_newer_file_keeps_that_one_too(tmp_path):
    """
    Revise is the door every rev after the first comes in through, so it is
    where most of the archive should accumulate — and it kept nothing. A job
    revised weekly for a year held only the file it started with, which is the
    one rev nobody ever needs back.
    """
    import io

    from engine.xml_writer import write_p6_xml
    import server

    def build(name, n):
        p = Project(uid="1", name="Rev", id="REV-ARCH",
                    data_date="2026-01-05", planned_start="2026-01-05")
        p.calendars = [Calendar(uid="1", name="Standard")]
        p.wbs_nodes = [WBSNode(uid="w", name="Area", code="A")]
        p.activities = [
            Activity(uid=f"u{i}", activity_id=f"A{i}0", name=f"Task {i}",
                     wbs_uid="w", calendar_uid="1", planned_duration=40,
                     remaining_duration=40, planned_start="2026-02-02",
                     planned_finish="2026-02-06")
            for i in range(n)]
        p.relations = []
        p.build_lookups()
        path = str(tmp_path / name)
        write_p6_xml(p, path)
        with open(path, "rb") as fh:
            return fh.read()

    one, two = build("r1.xml", 2), build("r2.xml", 5)
    c = server.app.test_client()
    assert c.post("/api/upload", data={"file": (io.BytesIO(one), "job-rev1.xml")},
                  content_type="multipart/form-data").status_code == 200
    assert c.post("/api/revise", data={"file": (io.BytesIO(two), "job-rev2.xml")},
                  content_type="multipart/form-data").status_code == 200

    docs = {d["name"]: d for d in c.get("/api/documents").get_json()["documents"]}
    assert "job-rev1.xml" in docs, "the file the job started with was dropped"
    assert "job-rev2.xml" in docs, "the revised-onto file was never kept"
    for name, blob in (("job-rev1.xml", one), ("job-rev2.xml", two)):
        got = c.get(f"/api/documents/{docs[name]['id']}/file")
        assert got.status_code == 200
        assert got.data == blob, f"{name} did not come back byte for byte"


# ── a big document must not take the host down with it ───────────────────────

def _ruled_pdf(pages=30, rows=40):
    """A text-heavy, ruled PDF — the shape that costs the most to read."""
    import zlib

    def stream(pno):
        ops = []
        for i in range(rows + 1):
            y = 760 - i * 13
            ops.append(f"0.5 w 40 {y} m 560 {y} l S")
        for x in (40, 200, 380, 560):
            ops.append(f"0.5 w {x} 760 m {x} {760 - rows * 13} l S")
        for i in range(rows):
            y = 760 - i * 13 - 9
            ops.append(f"BT /F1 8 Tf 44 {y} Td "
                       f"(MDC1.PH1.GEN.{1000 + i * 10} page {pno}) Tj ET")
            ops.append(f"BT /F1 8 Tf 204 {y} Td (Install Hangers Gen {300 + i}) Tj ET")
        return zlib.compress("\n".join(ops).encode())

    out, offs = bytearray(b"%PDF-1.4\n"), []

    def add(body):
        offs.append(len(out))
        out.extend(b"%d 0 obj\n" % len(offs) + body + b"\nendobj\n")

    kids = " ".join(f"{4 + i} 0 R" for i in range(pages))
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(pages):
        add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            f"<< /Font << /F1 3 0 R >> >> /Contents {4 + pages + i} 0 R >>".encode())
    for i in range(pages):
        s = stream(i + 1)
        add(b"<< /Filter /FlateDecode /Length %d >>\nstream\n" % len(s)
            + s + b"\nendstream")
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(offs) + 1, x))
    return bytes(out)


def _rss_mb():
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[1]) * 4096 / 1e6


def test_reading_a_long_document_does_not_grow_without_bound():
    """
    The bug that took the host down. pdfplumber keeps every character, line
    and rect it has parsed on the page object, and holds the page for as long
    as the document is open — so a read cost memory in proportion to the
    document's LENGTH, not the page being read.

    Measured over 120 pages before the fix: 50 MB at page 1, 259 at page 20,
    701 at page 60, 1,365 at page 120, on a host that has 512 MB for
    everything including the loaded schedules.
    """
    import gc

    from engine.scope_reader import read_scope
    short, long_ = _ruled_pdf(pages=6), _ruled_pdf(pages=60)

    gc.collect()
    before = _rss_mb()
    read_scope(short)
    gc.collect()
    after_short = _rss_mb() - before

    gc.collect()
    before = _rss_mb()
    read_scope(long_)
    gc.collect()
    after_long = _rss_mb() - before

    # Ten times the pages must not mean anything like ten times the memory.
    assert after_long < max(60.0, after_short * 4 + 30), (
        f"6 pages cost {after_short:.0f} MB, 60 pages cost {after_long:.0f} MB "
        f"— that is growth with length, not with page size")


def test_a_read_stops_on_the_page_limit_and_says_so():
    """
    A document that stopped still files, still searches and still hands back
    the original. But a reader who thinks all 400 pages were indexed will
    trust a search that came up empty.
    """
    from engine.scope_reader import DEFAULT_MAX_PAGES, read_scope
    r = read_scope(_ruled_pdf(pages=DEFAULT_MAX_PAGES + 10), max_pages=5)
    assert r["pages"] == 5
    assert r["stopped_at"] == "pages"


def test_a_read_that_fits_reports_no_truncation():
    from engine.scope_reader import read_scope
    r = read_scope(_ruled_pdf(pages=3), max_pages=60)
    assert r["pages"] == 3
    assert r["stopped_at"] is None


def test_the_time_limit_bounds_the_request():
    """
    A page budget alone does not bound it. Sixty pages of a ruled schedule
    print takes twelve seconds; sixty pages of a dense drawing set takes far
    longer, and the host in front of this gives up at thirty — at which point
    the caller sees a gateway error and cannot tell a slow document from a
    broken one.
    """
    from engine.scope_reader import read_scope
    r = read_scope(_ruled_pdf(pages=40), max_pages=40, max_seconds=0.001)
    assert r["stopped_at"] == "time"
    assert r["pages"] < 40
    assert r["line_count"] > 0, "gave up without returning what it had read"


def test_an_oversized_document_is_refused_as_json_not_html():
    """
    Werkzeug's own 413 is an HTML page and the UI reads JSON, so a file that
    was simply too big came across as "unexpected token < in JSON" — an error
    about the error, which reads as the app being broken.
    """
    import io

    import server
    p = Project(uid="1", name="P", id="P-CAP", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities = [Activity(uid="u1", activity_id="A10", name="x", wbs_uid="w",
                             calendar_uid="1", planned_duration=8,
                             remaining_duration=8)]
    p.relations = []
    p.build_lookups()
    sess = server._make_session("P-CAP", "p.xml")
    sess["project"] = p
    server._projects["P-CAP"] = sess
    server._active_id[0] = "P-CAP"

    blob = _ruled_pdf(pages=10)
    was = server._MAX_DOC_BYTES
    server._MAX_DOC_BYTES = 1024          # refuse anything real
    try:
        c = server.app.test_client()
        r = c.post("/api/documents",
                   data={"file": (io.BytesIO(blob), "big.pdf")},
                   content_type="multipart/form-data")
    finally:
        server._MAX_DOC_BYTES = was
    assert r.status_code == 413
    assert r.headers["Content-Type"].startswith("application/json")
    assert "error" in r.get_json()
