"""
doc_library.py — what you have given the agent, still there next week.

A document used to be read once and thrown away. The reading joined the
conversation, and sixteen turns later it had scrolled out — so "pull the
generator quantities from that scope PDF" meant uploading it again. For a
497-line document that is absurd: the lines were already extracted, exactly,
for free.

So they are kept with the job. What that has to avoid, though, is the obvious
trap: carrying 497 lines in every prompt would cost more than re-reading the
document ever did. It never happens. The prompt carries a CATALOGUE — one
line per document, saying what it is and how big — and the agent asks for
what it needs by name, with a query. A search returns the matching lines, not
the file.

That split is the whole design:

    always in the prompt   "scope-of-work.pdf — 497 lines, 9 pages"
    on request             the twelve lines that mention generators

Screenshots are held differently on purpose. Their content is a MODEL's
reading rather than extracted text, and their filenames are usually noise
("Screenshot 2026-08-24 at 14.32.11.png"), so what is kept is the reading and
a label — enough to say "the sheet you sent about MV 105", not enough to
pretend it can be searched like a text document.
"""

import datetime as _dt
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# Text documents keep their lines; past this a document is truncated rather
# than allowed to grow the saved brain without limit. 3000 lines is six times
# the document this was built for.
MAX_LINES = 3000
# Documents kept per job. Old ones fall off the end rather than accumulating
# forever — the catalogue has to stay readable.
MAX_DOCS = 24
# What a search hands back. Enough to answer from, small enough to send.
DEFAULT_HITS = 12
MAX_HITS = 40

PDF = "pdf"
SPREADSHEET = "spreadsheet"
IMAGE = "image"


@dataclass
class Document:
    id: str
    name: str
    kind: str
    added_at: str = ""
    # Text documents: the extracted lines, exactly as read.
    lines: List[str] = field(default_factory=list)
    # Where each line came from — page number or sheet name.
    places: List[str] = field(default_factory=list)
    line_count: int = 0          # before truncation, so a cap is visible
    pages: int = 0
    sheets: List[str] = field(default_factory=list)
    # Images: the model's reading, since there is no text to extract.
    summary: str = ""
    facts: List[str] = field(default_factory=list)
    truncated: bool = False

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def searchable(self) -> bool:
        return bool(self.lines)

    def label(self) -> str:
        """One line for the catalogue."""
        if self.kind == IMAGE:
            head = self.summary[:90] or "no summary"
            return f"{self.name} — image: {head}"
        where = (f"{len(self.sheets)} sheets" if self.sheets
                 else f"{self.pages} pages" if self.pages else "")
        bits = [f"{self.line_count} lines"] + ([where] if where else [])
        if self.truncated:
            bits.append(f"first {len(self.lines)} kept")
        return f"{self.name} — {', '.join(bits)}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _hits(word: str, tokens: List[str]) -> bool:
    """
    Does this word of the request name part of that document?

    Matched against whole TOKENS rather than the raw string, because a plain
    substring test makes "doc-0" find "doc-10" — "0" is inside "10". Numbers
    and very short words must stand alone; anything longer may match as a
    prefix, so "scope" still finds "scope-of-work".
    """
    if word.isdigit() or len(word) <= 3:
        return word in tokens
    return any(t.startswith(word) or word.startswith(t) for t in tokens)


class Library:
    """Every document given for one job."""

    def __init__(self, docs: Optional[List[Document]] = None):
        self.docs: List[Document] = docs or []

    # -- adding ------------------------------------------------------------
    def add_text(self, name: str, kind: str, read: Dict[str, Any]) -> Document:
        """Store an extracted PDF or workbook."""
        lines = read.get("lines") or []
        kept = lines[:MAX_LINES]
        doc = Document(
            id=uuid.uuid4().hex[:8], name=name or "document", kind=kind,
            added_at=_dt.datetime.now().isoformat(timespec="seconds"),
            lines=[getattr(l, "text", str(l)) for l in kept],
            places=[str(getattr(l, "page", "")) for l in kept],
            line_count=len(lines), pages=int(read.get("pages") or 0),
            sheets=list(read.get("sheets") or []),
            truncated=len(lines) > MAX_LINES,
        )
        self._put(doc)
        return doc

    def add_image(self, name: str, summary: str,
                  facts: Optional[List[str]] = None) -> Document:
        """Store what a model read off a drawing or screenshot."""
        doc = Document(
            id=uuid.uuid4().hex[:8], name=name or "image", kind=IMAGE,
            added_at=_dt.datetime.now().isoformat(timespec="seconds"),
            summary=(summary or "").strip(),
            facts=[str(f) for f in (facts or [])][:12],
        )
        self._put(doc)
        return doc

    def _put(self, doc: Document) -> None:
        # Re-uploading the same file replaces it rather than making a second
        # entry — otherwise "the scope PDF" becomes ambiguous the moment
        # somebody sends a corrected copy.
        self.docs = [d for d in self.docs
                     if _norm(d.name) != _norm(doc.name) or d.kind != doc.kind]
        self.docs.append(doc)
        if len(self.docs) > MAX_DOCS:
            self.docs = self.docs[-MAX_DOCS:]

    def remove(self, doc_id: str) -> bool:
        before = len(self.docs)
        self.docs = [d for d in self.docs if d.id != doc_id]
        return len(self.docs) != before

    # -- finding -----------------------------------------------------------
    def find(self, needle: str) -> Optional[Document]:
        """
        The document the user means, by whatever they called it.

        Exact id, then exact name, then every word of the request appearing in
        the name — so "the scope pdf", "scope-of-work.pdf" and "the scope"
        all land on the same file without a filename having to be typed.
        """
        if not needle:
            return None
        want = needle.strip()
        for d in self.docs:
            if d.id == want:
                return d
        low = _norm(want)
        for d in self.docs:
            if _norm(d.name) == low:
                return d
        words = [w for w in low.split()
                 if w not in ("the", "a", "an", "file", "document", "doc", "that")]
        if not words:
            return None
        scored: List[Tuple[int, Document]] = []
        for d in self.docs:
            tokens = (_norm(d.name) + " " + d.kind).split()
            hits = sum(1 for w in words if _hits(w, tokens))
            if hits:
                scored.append((hits, d))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        # A tie means the request did not distinguish between them — "doc-0"
        # against a shelf of doc-1…doc-24 matches "doc" and "pdf" on every one
        # of them. Returning whichever sorted first would hand back the wrong
        # document with no sign anything was ambiguous, so the caller is told
        # nothing was found and can name what it has instead.
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    def search(self, doc: Document, query: str = "",
               limit: int = DEFAULT_HITS) -> Dict[str, Any]:
        """
        The lines of one document that bear on a question.

        Never the whole document. A 497-line scope answers "what does it say
        about generators" in a dozen lines, and sending the other 485 would
        cost more than the answer is worth. With no query it returns the
        opening lines, which is what "what's in this file?" actually wants.
        """
        limit = max(1, min(int(limit or DEFAULT_HITS), MAX_HITS))
        if not doc.searchable:
            return {"lines": [], "matched": 0,
                    "note": "This is an image — what was read from it is its summary."}
        words = [w for w in _norm(query).split() if len(w) > 1]
        if not words:
            rows = [{"n": i + 1, "where": doc.places[i] if i < len(doc.places) else "",
                     "text": t} for i, t in enumerate(doc.lines[:limit])]
            return {"lines": rows, "matched": len(doc.lines),
                    "note": "opening lines — ask with a term to search"}
        hits = []
        for i, text in enumerate(doc.lines):
            low = _norm(text)
            score = sum(1 for w in words if w in low)
            if score:
                hits.append((score, i, text))
        hits.sort(key=lambda h: (-h[0], h[1]))
        rows = [{"n": i + 1, "where": doc.places[i] if i < len(doc.places) else "",
                 "text": t} for _, i, t in hits[:limit]]
        return {"lines": rows, "matched": len(hits)}

    # -- what the agent always sees ---------------------------------------
    def catalogue_block(self) -> str:
        """
        One line per document. This is the ONLY part that rides in every
        prompt — the contents are fetched on request, or a 497-line scope
        would cost more to carry than it ever cost to read.
        """
        if not self.docs:
            return ""
        out = ["", "DOCUMENTS YOU HAVE BEEN GIVEN FOR THIS JOB:"]
        for d in reversed(self.docs):
            out.append(f"  {d.label()}")
        out.append("  To use one, run read_document with its name and a term to "
                   "look for — that returns the matching lines. Do not claim "
                   "what a document says without reading it first.")
        return "\n".join(out)

    # -- persistence -------------------------------------------------------
    def to_json(self) -> Dict[str, Any]:
        return {"docs": [d.to_json() for d in self.docs]}

    @classmethod
    def from_json(cls, data: Any) -> Optional["Library"]:
        if not isinstance(data, dict) or not data.get("docs"):
            return None
        docs = []
        for raw in data["docs"]:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            fields = {k: raw.get(k) for k in Document.__dataclass_fields__ if k in raw}
            fields.setdefault("id", uuid.uuid4().hex[:8])
            fields.setdefault("kind", PDF)
            try:
                docs.append(Document(**fields))
            except TypeError:
                continue
        return cls(docs) if docs else None
