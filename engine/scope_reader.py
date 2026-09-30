"""
scope_reader.py — get every line out of a scope document, exactly, for free.

A 497-line MEP scope across nine multi-column pages is the wrong thing to hand
a vision model. It would cost a fortune, take several minutes, and — worse —
you would have no way of knowing whether it read 497 lines or 460. "Did it
miss anything?" is unanswerable, which is exactly the laziness this has to
avoid.

A scope document of that kind is a generated PDF, so it has a real text
layer. Reading it is therefore deterministic: every line, exactly as written,
at zero token cost, with a COUNT you can check. That is the discipline —
spend nothing on the part a machine can do perfectly, and save the model for
the part that needs judgement.

Three passes, each a genuine fallback rather than a retry:

  1. ruled tables      — the document says where its own columns are
  2. word clustering   — borderline/borderless tables, grouped by position
  3. raw text lines    — no table structure at all, just get the words

Columns are deliberately NOT relied on. What matters in a scope line is the
WORDS — "Furnish and install (4) 2500kW generators, Phase 2" — and those
survive any column layout. Trying to be clever about which column is which is
how an importer breaks on the next document that is formatted differently.
"""

import io
import re
import time as _time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ScopeLine:
    """One line of scope, as written."""
    n: int                     # 1-based, in document order
    text: str
    page: int

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


# Page furniture repeats on every page and says nothing about scope. Left in,
# it would show up as dozens of phantom "scope lines".
_FURNITURE = re.compile(
    r"(?i)^\s*(?:page\s+\d+|\d+\s*of\s*\d+|sheet\s+\d+|rev(?:ision)?\s*[:.]?\s*\w{0,4}"
    r"|confidential|proprietary|printed\s+on|©|copyright)\b")

# A line has to carry some actual language to be scope. Bare numbers, a lone
# item code, or a stray bullet are structure, not content.
_MIN_WORDS = 3
_MIN_LETTERS = 8


def _is_content(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < _MIN_LETTERS or _FURNITURE.match(t):
        return False
    letters = sum(1 for ch in t if ch.isalpha())
    if letters < _MIN_LETTERS:
        return False
    return len([w for w in re.split(r"\s+", t) if w]) >= _MIN_WORDS


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def _open(source: Any):
    """pdfplumber over a path or raw bytes, without a temp file."""
    import pdfplumber
    if isinstance(source, (bytes, bytearray)):
        return pdfplumber.open(io.BytesIO(bytes(source)))
    return pdfplumber.open(source)


# How much of a document is read, and for how long. Both are needed: sixty
# pages of a ruled schedule print takes twelve seconds, sixty pages of a dense
# drawing set takes far longer, and the host in front of this gives up at
# thirty — at which point the caller sees a gateway error rather than a result.
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_SECONDS = 18.0


def _take(pdf, limit: int):
    """Up to `limit` pages, built one at a time as they are asked for."""
    for i, page in enumerate(pdf.pages):
        if i >= limit:
            return
        yield page


def _chain(first, rest):
    yield first
    for page in rest:
        yield page


def read_scope(source: Any, max_pages: int = DEFAULT_MAX_PAGES,
               max_seconds: float = DEFAULT_MAX_SECONDS) -> Dict[str, Any]:
    """
    Every scope line in the document, with the count so it can be checked.

    Returns {lines, line_count, pages, method, has_text_layer}. `method` says
    which pass produced the result, because "we fell back to raw text" is
    worth knowing when the lines look odd.

    Raises RuntimeError only when there is genuinely nothing to read — a
    scanned document with no text layer, which needs the vision path instead.
    """
    try:
        pdf = _open(source)
    except Exception as e:
        raise RuntimeError(f"That file could not be opened as a PDF: {e}")

    lines: List[ScopeLine] = []
    method = "tables"
    with pdf:
        # Taken one at a time rather than as `pdf.pages[:max_pages]`, which
        # builds a page object for every one of them before the first is read.
        # On a 500-page drawing set that is 440 pages of parsing this will
        # never look at.
        pages = _take(pdf, max_pages)
        first = next(pages, None)
        if first is None:
            raise RuntimeError("That PDF has no pages.")

        has_text = bool((first.extract_text() or "").strip())
        if not has_text:
            raise RuntimeError(
                "That PDF is a scan — there is no text in it to read. Send it "
                "as a drawing/image instead, or supply a text-based export.")
        pages = _chain(first, pages)

        n = 0
        total = 0
        stopped = None
        deadline = (_time.monotonic() + max_seconds) if max_seconds else None
        for page_no, page in enumerate(pages, 1):
            # A page budget alone does not bound the request. Sixty pages of a
            # ruled schedule print takes twelve seconds; sixty pages of a dense
            # drawing set takes far longer, and the host in front of this gives
            # up at thirty — at which point the caller sees a gateway error and
            # cannot tell a slow document from a broken one. So the read stops
            # on whichever limit comes first and SAYS which, rather than being
            # killed partway through and reporting nothing at all.
            if deadline and page_no > 1 and _time.monotonic() > deadline:
                stopped = "time"
                break
            total = page_no
            got: List[str] = []

            # 1. the document's own ruled table structure
            for table in (page.extract_tables() or []):
                for row in table:
                    cells = [_clean(str(c)) for c in row if c is not None]
                    joined = " ".join(c for c in cells if c)
                    if joined:
                        got.append(joined)

            # 2. borderless — cluster words back into visual rows
            if not got:
                method = "clustered" if method == "tables" else method
                words = page.extract_words(use_text_flow=True) or []
                for row in _cluster(words):
                    joined = _clean(" ".join(row))
                    if joined:
                        got.append(joined)

            # 3. no structure at all — take the text as it reads
            if not got:
                method = "text"
                for raw in (page.extract_text() or "").splitlines():
                    joined = _clean(raw)
                    if joined:
                        got.append(joined)

            for text in got:
                if _is_content(text):
                    n += 1
                    lines.append(ScopeLine(n=n, text=text, page=page_no))

            # Let the page go. pdfplumber keeps every character, line and rect
            # it has parsed on the page object, and holds the page for as long
            # as the document is open — so reading a document cost memory in
            # proportion to its LENGTH, not to the page being read. Measured
            # over 120 pages: 50 MB at page 1, 259 at page 20, 701 at page 60,
            # 1,365 at page 120, on a host that has 512 MB. Flushing here it
            # stays flat, and the read gets faster as well because there is
            # less to keep.
            _release(page)
        else:
            if total >= max_pages:
                stopped = "pages"

    return {
        "lines": lines,
        "line_count": len(lines),
        "pages": total,
        "method": method,
        "has_text_layer": True,
        "stopped_at": stopped,
    }


def _release(page) -> None:
    """Drop everything pdfplumber cached for one page."""
    try:
        page.flush_cache()
    except Exception:
        pass
    for attr in ("_objects", "_layout"):
        try:
            if hasattr(page, attr):
                setattr(page, attr, None)
        except Exception:
            pass


def _cluster(words: List[Dict], row_tol: float = 3.0,
             col_gap: float = 20.0) -> List[List[str]]:
    """
    Words back into visual rows.

    A multi-column page interleaves columns at the same height, so the cells
    of one visual row may belong to two different columns. That is fine here
    and deliberately not untangled: the words are what carry the meaning, and
    guessing at column ownership is how this breaks on the next document.
    """
    if not words:
        return []
    buckets: Dict[int, List[Dict]] = {}
    for w in words:
        buckets.setdefault(int(round(w["top"] / row_tol)), []).append(w)
    out: List[List[str]] = []
    for key in sorted(buckets):
        line = sorted(buckets[key], key=lambda w: w["x0"])
        cells, buf, last = [], [], None
        for w in line:
            if last is not None and (w["x0"] - last) > col_gap:
                cells.append(" ".join(buf))
                buf = []
            buf.append(w["text"])
            last = w["x1"]
        if buf:
            cells.append(" ".join(buf))
        out.append(cells)
    return out


def read_excel(source: Any, max_rows: int = 20000) -> Dict[str, Any]:
    """
    Every row of every sheet, flattened to a line of text.

    A workbook is a harder shape than a PDF, not an easier one: the useful
    content is spread over several sheets, the columns differ between them,
    and a header may sit anywhere. Rather than guessing at a schema per sheet
    — which breaks on the next workbook — each row becomes one line of text
    with its sheet name carried alongside, and the words do the work. Same
    reasoning as the PDF side: what a line MEANS survives any layout.

    Sheet names are kept because they are usually the most informative thing
    in the file ("Phase 2 Electrical", "Long Lead"), and losing them would
    throw away the document's own organisation.
    """
    try:
        import openpyxl
    except ImportError:                                   # pragma: no cover
        raise RuntimeError("Reading .xlsx needs openpyxl — pip install openpyxl.")
    try:
        src = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray)) else source
        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    except Exception as e:
        raise RuntimeError(f"That file could not be opened as a workbook: {e}")

    lines: List[ScopeLine] = []
    sheets: List[str] = []
    n = 0
    try:
        for ws in wb.worksheets:
            sheets.append(ws.title)
            for row in ws.iter_rows(values_only=True):
                if row is None:
                    continue
                cells = [_clean(str(c)) for c in row if c is not None and str(c).strip()]
                if not cells:
                    continue
                text = " ".join(cells)
                if not _is_content(text):
                    continue
                n += 1
                lines.append(ScopeLine(n=n, text=text, page=len(sheets)))
                if n >= max_rows:
                    break
            if n >= max_rows:
                break
    finally:
        wb.close()

    if not lines:
        raise RuntimeError("That workbook has no rows with readable content in it.")
    return {"lines": lines, "line_count": len(lines), "pages": len(sheets),
            "sheets": sheets, "method": "excel", "has_text_layer": True}


def read_any(source: Any, filename: str = "") -> Dict[str, Any]:
    """Lines out of a PDF or a workbook, whichever this is."""
    low = (filename or "").lower()
    if low.endswith((".xlsx", ".xlsm", ".xltx")):
        return read_excel(source)
    if low.endswith(".csv") or low.endswith(".tsv"):
        return read_delimited(source)
    return read_scope(source)


def read_delimited(source: Any, max_rows: int = 20000) -> Dict[str, Any]:
    """CSV/TSV — the same flattening, for the plainest export there is."""
    import csv as _csv
    raw = source
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    elif not isinstance(raw, str):
        with open(raw, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    sample = raw[:4096]
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        dialect = _csv.excel
    lines, n = [], 0
    for row in _csv.reader(io.StringIO(raw), dialect):
        cells = [_clean(str(c)) for c in row if str(c).strip()]
        if not cells:
            continue
        text = " ".join(cells)
        if not _is_content(text):
            continue
        n += 1
        lines.append(ScopeLine(n=n, text=text, page=1))
        if n >= max_rows:
            break
    if not lines:
        raise RuntimeError("That file has no rows with readable content in it.")
    return {"lines": lines, "line_count": len(lines), "pages": 1,
            "sheets": [], "method": "delimited", "has_text_layer": True}


def page_window(result: Dict[str, Any], start: int, end: int) -> List[ScopeLine]:
    """
    The lines on a range of pages — the primitive for going back over part of
    a long document without re-reading all of it.
    """
    return [l for l in result["lines"] if start <= l.page <= end]
