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
