"""
test_pdf_reading.py — a PDF sheet on whatever model the user actually has.

A PDF used to be Anthropic-only. Every other provider got a flat refusal
telling the user to go and switch models — including OpenAI, which reads PDFs
perfectly well. A hard-coded "only Claude can do this" was true once, stopped
being true, and left somebody with a working key being told the tool could not
help them.

What is defended here: the PDF is offered to each provider in ITS OWN shape
first, because the real text layer reads far better than a picture of the same
page; a provider that refuses gets locally-rendered images instead, which any
model with eyes accepts; and neither path is a capability list that has to be
kept up to date, so a model that gains PDF support starts using it with no
change here.
"""

import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import interpreter.vision as vz


# A real, minimal one-page PDF — enough for a renderer to open and draw.
_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
        b"/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 44>>stream\nBT /F1 24 Tf 20 100 Td (SHEET) Tj ET\n"
        b"endstream endobj\ntrailer<</Root 1 0 R>>")

_ANSWER = '{"summary": "a sheet", "facts": [], "directives": []}'


class _Recorder:
    """Stands in for a provider SDK and remembers what it was sent."""

    def __init__(self, fail_on_pdf=False):
        self.fail_on_pdf = fail_on_pdf
        self.calls = []

    # -- OpenAI shape ---------------------------------------------------
    def openai_client(self):
        rec = self

        class _Completions:
            def create(self, **kw):
                rec.calls.append(kw)
                parts = kw["messages"][-1]["content"]
                if rec.fail_on_pdf and any(p.get("type") == "file" for p in parts):
                    raise RuntimeError("This model does not support file input")
                import types
                return types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=_ANSWER))])

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        return lambda **kw: _Client()

    def parts(self, call=-1):
        return self.calls[call]["messages"][-1]["content"]

    def types(self, call=-1):
        return [p.get("type") for p in self.parts(call)]


def _openai(monkeypatch, rec):
    monkeypatch.setattr(vz, "_OPENAI_AVAILABLE", True, raising=False)
    monkeypatch.setattr(vz, "OpenAI", rec.openai_client(), raising=False)


# ── rendering a PDF to images ────────────────────────────────────────────────

def test_a_pdf_renders_to_page_images():
    pages = vz.rasterize_pdf(_PDF)
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG"), "not a PNG"


def test_a_runaway_page_count_is_capped():
    pages = vz.rasterize_pdf(_PDF, max_pages=1)
    assert len(pages) <= 1


def test_rendered_pages_stay_under_the_image_limit():
    """A page sent over the cap is refused by the API — better slightly softer
    than rejected outright."""
    for page in vz.rasterize_pdf(_PDF):
        assert len(page) <= vz._MAX_BYTES


def test_something_that_is_not_a_pdf_is_a_plain_sentence():
    with pytest.raises(RuntimeError) as e:
        vz.rasterize_pdf(b"this is not a pdf at all")
    assert "could not be opened" in str(e.value)


# ── OpenAI: the native path, which used to be refused outright ───────────────

def test_openai_is_sent_the_pdf_itself_not_a_refusal(monkeypatch):
    rec = _Recorder()
    _openai(monkeypatch, rec)
    out = vz._ask_model(_PDF, ".pdf", True, "sys", "read it", "gpt-4.1-mini", "k")
    assert out == _ANSWER
    assert "file" in rec.types(), "the PDF was not sent as a file"
    sent = next(p for p in rec.parts() if p["type"] == "file")
    assert sent["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_the_pdf_sent_is_the_pdf_given(monkeypatch):
    rec = _Recorder()
    _openai(monkeypatch, rec)
    vz._ask_model(_PDF, ".pdf", True, "sys", "read it", "gpt-4.1-mini", "k")
    sent = next(p for p in rec.parts() if p["type"] == "file")
    raw = sent["file"]["file_data"].split("base64,", 1)[1]
    assert base64.standard_b64decode(raw) == _PDF


# ── OpenAI: the fallback, for a model that will not take one ────────────────

def test_a_model_that_refuses_a_pdf_gets_rendered_pages_instead(monkeypatch):
    rec = _Recorder(fail_on_pdf=True)
    _openai(monkeypatch, rec)
    out = vz._ask_model(_PDF, ".pdf", True, "sys", "read it", "gpt-4.1-nano", "k")
    assert out == _ANSWER
    assert len(rec.calls) == 2, "it did not try the native shape first"
    assert "file" in rec.types(0)
    assert rec.types(1).count("image_url") >= 1
    img = next(p for p in rec.parts(1) if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_the_fallback_still_carries_the_question(monkeypatch):
    rec = _Recorder(fail_on_pdf=True)
    _openai(monkeypatch, rec)
    vz._ask_model(_PDF, ".pdf", True, "sys", "what rooms are on it?",
                  "gpt-4.1-nano", "k")
    text = next(p for p in rec.parts(1) if p["type"] == "text")
    assert text["text"] == "what rooms are on it?"


def test_without_a_renderer_the_refusal_says_what_to_do(monkeypatch):
    rec = _Recorder(fail_on_pdf=True)
    _openai(monkeypatch, rec)
    # The renderer is loaded on first use now, so "not installed" is a
    # resolved-to-nothing cache rather than a flag set at import.
    monkeypatch.setattr(vz, "_RASTER", [None])
    with pytest.raises(RuntimeError) as e:
        vz._ask_model(_PDF, ".pdf", True, "sys", "read it", "gpt-4.1-nano", "k")
    assert "screenshot" in str(e.value).lower()


# ── images are untouched by any of this ─────────────────────────────────────

def test_an_image_still_goes_as_an_image(monkeypatch):
    rec = _Recorder()
    _openai(monkeypatch, rec)
    vz._ask_model(b"\x89PNG", ".png", False, "sys", "read it", "gpt-4.1-mini", "k")
    assert rec.types() == ["image_url", "text"]
    assert len(rec.calls) == 1, "an image must not try the PDF path"


# ── Anthropic keeps its own native path ─────────────────────────────────────

def test_anthropic_still_gets_a_document_block(monkeypatch):
    import types
    sent = {}

    class _Messages:
        def create(self, **kw):
            sent.update(kw)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=_ANSWER)])

    monkeypatch.setattr(vz, "_ANTHROPIC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(vz, "anthropic", types.SimpleNamespace(
        Anthropic=lambda **kw: types.SimpleNamespace(messages=_Messages())),
        raising=False)
    out = vz._ask_model(_PDF, ".pdf", True, "sys", "read it", "claude", "k")
    assert out == _ANSWER
    block = sent["messages"][0]["content"][0]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


# ── the reader as a whole ───────────────────────────────────────────────────

def test_a_pdf_sheet_reads_through_read_drawing_on_openai(monkeypatch):
    """The path a user actually takes: attach a PDF sheet with a GPT key."""
    rec = _Recorder()
    _openai(monkeypatch, rec)
    reading = vz.read_drawing(_PDF, "E03-021.pdf", model_key="gpt-4.1-mini",
                              api_key="k")
    assert reading["summary"] == "a sheet"


def test_an_oversize_pdf_is_still_refused_before_any_call():
    with pytest.raises(RuntimeError) as e:
        vz.read_drawing(b"x" * (31 * 1024 * 1024), "big.pdf",
                        model_key="gpt-4.1-mini", api_key="k")
    assert "30MB" in str(e.value)


def test_a_model_id_this_file_has_never_heard_of_still_gets_the_pdf(monkeypatch):
    """
    resolve_model deliberately passes an unrecognised id through to its
    provider rather than whitelisting — new models ship faster than this file
    changes. A PDF has to survive that same passthrough, or the next GPT
    release would be back to being told it cannot read one.
    """
    rec = _Recorder()
    _openai(monkeypatch, rec)
    out = vz._ask_model(_PDF, ".pdf", True, "sys", "read it",
                        "gpt-6-something-unreleased", "k")
    assert out == _ANSWER
    assert "file" in rec.types()
    assert rec.calls[0]["model"] == "gpt-6-something-unreleased"


def test_no_key_is_a_settings_message_not_a_network_call(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as e:
        vz._ask_model(_PDF, ".pdf", True, "sys", "x", "gpt-4.1-mini", None)
    assert "key" in str(e.value).lower()
