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
