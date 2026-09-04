"""
test_image_format.py — trust the bytes, not the filename.

An upload was checked purely on its extension, so two ordinary mistakes
reached the provider and came back as its own error:

  A phone photo. iOS names a share "IMG_3439.jpeg" while the bytes are HEIC.
  Sent as image/jpeg, OpenAI answers "Invalid base64 image_url" — which reads
  as a bug in this app rather than "your photo is in a format no model reads".

  An empty upload. Encoded to an empty string and posted as
  "data:image/jpeg;base64," with nothing after the comma, which is literally
  the invalid base64 being complained about.

Both cost a round trip and tokens to learn nothing. Both are now caught before
the call, with a message saying what to do.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from interpreter.vision import _check, sniff_format

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 40
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40
PDF = b"%PDF-1.7" + b"\x00" * 40


# ── sniffing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data,ext", [
    (PNG, ".png"), (JPG, ".jpg"), (GIF, ".gif"),
    (WEBP, ".webp"), (HEIC, ".heic"), (PDF, ".pdf"),
])
def test_the_format_is_read_from_the_bytes(data, ext):
    assert sniff_format(data) == ext


def test_unrecognised_bytes_report_nothing_rather_than_guessing():
    assert sniff_format(b"not an image at all") is None
    assert sniff_format(b"") is None


# ── the two failures that were reaching the provider ────────────────────────

def test_an_iphone_heic_named_jpeg_is_caught_here():
    """The reported case. Named .jpeg, HEIC inside."""
    with pytest.raises(RuntimeError) as e:
        _check(HEIC, "IMG_3439.jpeg")
    msg = str(e.value)
    assert "HEIC" in msg
    assert "IMG_3439.jpeg" in msg, "the message should name the file"
    assert "Most Compatible" in msg or "JPEG" in msg, "no way out offered"


def test_an_empty_upload_is_caught_before_the_api_call():
    """It became 'data:image/jpeg;base64,' with nothing after the comma —
    exactly the invalid base64 the provider complained about."""
    with pytest.raises(RuntimeError) as e:
        _check(b"", "IMG_3439.jpeg")
    assert "empty" in str(e.value).lower()


# ── the name is a hint; the bytes decide ─────────────────────────────────────

def test_a_png_named_jpg_is_sent_as_a_png():
    """Otherwise it goes out labelled image/jpeg and may be refused."""
    ext, is_pdf = _check(PNG, "screenshot.jpg")
    assert ext == ".png" and not is_pdf


def test_a_pdf_named_png_is_still_treated_as_a_pdf():
    ext, is_pdf = _check(PDF, "sheet.png")
    assert ext == ".pdf" and is_pdf


def test_a_correctly_named_jpeg_still_works():
    ext, is_pdf = _check(JPG, "IMG_3439.jpeg")
    assert ext == ".jpg" and not is_pdf


def test_a_file_with_no_extension_is_read_from_its_bytes():
    """A clipboard paste often arrives unnamed."""
    ext, is_pdf = _check(PNG, "pasted")
    assert ext == ".png"


# ── what it still refuses, and what it still lets through ────────────────────

def test_a_plainly_wrong_file_type_is_refused():
    with pytest.raises(RuntimeError) as e:
        _check(b"hello", "notes.txt")
    assert "not a readable image" in str(e.value)


def test_unrecognised_bytes_with_an_image_name_are_still_attempted():
    """Deliberately permissive: an unusual but valid encoding should not be
    refused locally. The empty and HEIC cases — the two that actually happen —
    are caught above, and this path at worst costs one provider error."""
    ext, is_pdf = _check(b"not an image at all", "x.jpeg")
    assert ext == ".jpeg" and not is_pdf


def test_the_size_limits_still_apply():
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (6 * 1024 * 1024)
    with pytest.raises(RuntimeError) as e:
        _check(big, "huge.jpg")
    assert "5MB" in str(e.value)


def test_a_big_pdf_is_measured_against_the_pdf_limit():
    """A PDF gets 30MB, not the image 5MB — and sniffing must not lose that."""
    pdf = b"%PDF-1.7" + b"\x00" * (6 * 1024 * 1024)
    ext, is_pdf = _check(pdf, "sheet.pdf")
    assert is_pdf, "a 6MB PDF was refused under the image limit"
