"""
vision.py — read a drawing the way a foreman reads one.

A screenshot of a grounding plan, a snip of a one-line, a photo of a sheet on
a screen: the point is not OCR, it is the handful of facts on that sheet that
bear on SEQUENCE — what feeds what, what sits in which room, what has to be
in before what. Those facts come back structured, and none of them touch the
project until the user confirms each one into the project brain.

That confirmation step is deliberate. The user's words: context should
"inform, not lead". A drawing read by a model is a proposal; walked knowledge
gets to say no.

Images go to whichever provider is configured. PDFs are sent as documents,
which only the Anthropic API accepts — a PDF upload on an OpenAI model comes
back with a plain explanation instead of a guess.
"""

import base64
import datetime as _dt
import io
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

from .llm_interpreter import MODELS, DEFAULT_MODEL, resolve_model

_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
# The Anthropic API refuses images over 5MB; telling the user to snip tighter
# beats silently degrading a drawing until it is unreadable.
_MAX_BYTES = 5 * 1024 * 1024
_MAX_PDF_BYTES = 30 * 1024 * 1024

# ── PDFs on any model that can read one ──────────────────────────────────────
# A PDF used to be Anthropic-only: every other provider got a flat refusal
# telling the user to switch models. That was true once and stopped being
# true, and a hard-coded "only Claude can do this" is exactly the kind of
# claim that rots — the user had a perfectly capable OpenAI key and was told
# to go away.
#
# So there is no capability list to keep current. The PDF is offered to the
# provider in ITS native shape first, and if that is refused the pages are
# rendered to images locally and sent as images — which every vision model
# accepts. A provider that gains PDF support starts using it with no change
# here; one that never does still works.

# Rendering is optional: without it a PDF simply needs a provider that takes
# one natively, which is the behaviour that existed before.
#
# Imported on FIRST USE, not at module load. pypdfium2 pulls in a native
# library and Pillow is not small; loading both into the web worker at startup
# cost tens of megabytes on every deploy, for a path that only runs when a
# model refuses a PDF outright. On a single-worker instance holding a
# 2,776-activity schedule that is the difference between comfortable and
# being killed mid-request — which surfaces to the browser as an empty
# response and no explanation at all.
_RASTER: List[Any] = []


def _raster_available() -> bool:
    if not _RASTER:
        try:
            import pypdfium2
            from PIL import Image                    # noqa: F401
            _RASTER.append(pypdfium2)
        except ImportError:                          # pragma: no cover
            _RASTER.append(None)
    return _RASTER[0] is not None

# A sheet set is sent one sheet at a time, so a PDF here is normally a handful
# of pages. This is a guard against somebody attaching the whole 400-page
# issue, not a working limit.
_MAX_PDF_PAGES = 8
# 150 DPI equivalent. Drawing text is small; below this the sheet numbers and
# room tags stop being legible, which is the whole reason for reading it.
_RASTER_SCALE = 2.0


def rasterize_pdf(file_bytes: bytes, max_pages: int = _MAX_PDF_PAGES,
                  scale: float = _RASTER_SCALE) -> List[bytes]:
    """
    PDF pages as PNG bytes, so any model with eyes can read the sheet.

    Renders locally rather than asking the provider to. Pages that come back
    over the image limit are re-rendered smaller rather than being sent and
    refused — a drawing that is slightly softer still reads; one the API
    rejects does not.
    """
    if not _raster_available():
        raise RuntimeError(
            "This model cannot read PDFs directly, and PDF rendering is not "
            "installed here. Send a PNG/JPG screenshot of the sheet, or run "
            "`pip install pypdfium2 Pillow`.")
    try:
        doc = _RASTER[0].PdfDocument(file_bytes)
    except Exception as e:
        raise RuntimeError(f"That PDF could not be opened: {e}")
    out: List[bytes] = []
    for i in range(min(len(doc), max_pages)):
        at = scale
        for _ in range(4):                    # shrink until it fits, then stop
            buf = io.BytesIO()
            doc[i].render(scale=at).to_pil().save(buf, format="PNG")
            data = buf.getvalue()
            if len(data) <= _MAX_BYTES or at <= 0.5:
                out.append(data)
                break
            at /= 1.5
    if not out:
        raise RuntimeError("That PDF has no pages that could be rendered.")
    return out

READ_PROMPT = """You are a senior electrical construction scheduler reading one sheet of a drawing set (it may be a screenshot, a snip, or a photo of a screen).

Extract ONLY what bears on construction sequencing and schedule logic. Do not describe the drawing; read it like a scheduler deciding what has to happen before what.

Return ONLY a JSON object, no markdown, exactly this shape:
{
  "sheet_number": "e.g. E03-021AB, or null if not visible",
  "sheet_title": "the title block name, or null",
  "discipline": "electrical | mechanical | civil | structural | architectural | other",
  "summary": "2-3 sentences: what this sheet shows and why it matters to sequence",
  "rooms": ["room/area identifiers visible, using the job's own naming, e.g. MV 105"],
  "equipment": ["major equipment shown, e.g. GIS RMU, MV XFMR, PDU"],
  "facts": ["short factual observations that affect logic, one clause each"],
  "room_flow": {
    "family": "the room family the flow is over, e.g. MV — null if none",
    "order": ["the room NUMBERS in build order, e.g. [107, 105, 106]"],
    "why": "what on the sheet shows this order — the specific evidence",
    "confidence": "stated | implied | none"
  },
  "directives": ["sequencing statements the sheet supports, in one of the three shapes below — empty list if the sheet shows layout but no order"]
}

THE THREE SHAPES THAT BECOME ENFORCED RULES. A directive written any other way is kept as a note and enforces nothing, so use these words:
  1. "<work A> follows <work B> in the same room"   (also: "... in the same phase")
  2. "<FAMILY> rooms run sequential"                 (they are built in number order)
  3. "<FAMILY> rooms run 107, 105, 106"              (they are built in THIS order)
Shape 3 is the one a drawing set is uniquely good for — number order is rarely the build order.

room_flow — read the LAYOUT, not just the labels:
- A feed direction, a riser working up, a phased hand-over boundary, a numbered install sequence, a keyed plan, a construction-sequence note, or equipment fed from a common source all imply which room is reachable or energised first.
- Say WHY in the "why" field, citing what is actually on the sheet. "Rooms are numbered left to right" is not evidence of build order; "MV 107 feeds 105 and 106 from the utility entry" is.
- confidence "stated" only when the sheet says the order in words or numbers it. "implied" when you are reading it off the layout. "none" when you are not reading an order at all — then use an empty order list.
- Never turn plain room numbering into a flow. If the only thing you know is that the rooms exist, that is confidence "none".

Rules:
- Use the schedule's own vocabulary where the context below names rooms/equipment; match its spelling.
- Never invent a room, equipment tag, or sheet number you cannot actually see.
- A layout sheet with no sequence implication gets facts but an empty directives list.
- If the image is too blurry to read a field, use null or leave the list empty — say so in the summary.
"""


READ_SCHEDULE_PROMPT = """You are reading a SCHEDULE — a screenshot of a P6 / Excel / Primavera grid, a printed bar chart, or a status report. Not a drawing.

Pull out the activity ROWS exactly as printed. Do not interpret, infer or complete them.

Return ONLY a JSON object, no markdown:
{
  "source_title": "what this view is, if a header says — else null",
  "data_date": "YYYY-MM-DD if a data date / status date is shown, else null",
  "rows": [
    {
      "activity_id": "the id column exactly as printed, or null",
      "name": "the activity name exactly as printed, or null",
      "start": "YYYY-MM-DD or null",
      "finish": "YYYY-MM-DD or null",
      "actual_start": "YYYY-MM-DD if the start is marked actual, else null",
      "actual_finish": "YYYY-MM-DD if the finish is marked actual, else null",
      "percent_complete": 0-100 or null,
      "status": "Not Started | In Progress | Completed | null"
    }
  ],
  "notes": ["anything that affects how these rows should be read"]
}

Rules — these matter more than completeness:
- Transcribe. Never guess a date you cannot read; use null.
- A date printed with an A suffix, a filled bar, or in an "Actual" column is an ACTUAL date. A date in a Start/Finish column with no such marking is planned. Getting this wrong rewrites someone's history — when the marking is unclear, put the date in start/finish and leave the actual fields null.
- Infer status only from what is shown: 100% or a complete bar = Completed; an actual start with no actual finish = In Progress; nothing = Not Started. If no progress information is visible at all, use null — do NOT default to Not Started.
- Convert dates to YYYY-MM-DD. A two-digit year like 26 means 2026. If a date is ambiguous (03/04/26), prefer the format consistent with the other rows and say which you used in notes.
- Include every activity row you can read. Skip WBS/summary header rows.
- If the image is not a schedule at all, return rows: [] and say so in notes.
"""

# Which of the two readings a request wants. Asking "what's on this sheet?" and
# "make my dates match this" are different jobs on the same pixels, and guessing
# wrong wastes a model call and confuses the answer.
_SCHEDULE_WORDS = re.compile(
    r"(?i)\b(schedule|activit|dates?|actual|actualiz|status|statuse?s|"
    r"progress|percent|%|complete|completion|update|sync|match|compare|"
    r"track(?:ing)?|baseline|lookahead|three ?week|3 ?week|bar ?chart|"
    r"gantt|p6|primavera|xer)\b")
_DRAWING_WORDS = re.compile(
    r"(?i)\b(drawing|sheet|detail|riser|one[- ]line|single[- ]line|plan|"
    r"elevation|section|layout|spec|submittal|diagram|grounding|conduit)\b")


def classify_image_intent(question: str, recent: Optional[str] = None) -> str:
    """
    'schedule' when the ask is about rows, dates or status; 'drawing' otherwise.

    `recent` is what the user said just before uploading. People say what they
    want and THEN go find the file: "I want the activities and actuals to match
    the entries in the screenshot" … then a bare upload. Judging that upload on
    its own silence reads it as a drawing and answers a question nobody asked.
    The words in the box win when there are any; the previous turn is the
    fallback, not an override.

    A bare upload with nothing said either way is a drawing — the common case,
    and a wrong schedule read on a drawing returns no rows at all, which is a
    worse answer than simply reading the sheet.
    """
    q = (question or "").strip()
    if not q:
        q = (recent or "").strip()
        if not q:
            return "drawing"
    sched = len(_SCHEDULE_WORDS.findall(q))
    draw = len(_DRAWING_WORDS.findall(q))
    return "schedule" if sched > draw else "drawing"


def _uniq(values, cap):
    seen, out = set(), []
    for v in values:
        k = (v or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
        if len(out) >= cap:
            break
    return out


def _job_vocabulary(project) -> str:
    """
    The job's own folder names AND the words its activities are actually
    called, so a proposed rule is phrased in language the schedule can match.

    Without the activity words the model writes rules in drawing language —
    "Installation of utility switches precedes breakers" — which is a true
    statement about the sheet and a dead letter against the schedule: no
    activity is called that, so the rule binds nothing and the user gets a
    button that does nothing when clicked.
    """
    if project is None:
        return ""
    folders = _uniq(((w.name or "") for w in getattr(project, "wbs_nodes", None) or []
                     if len(w.name or "") <= 40), 100)
    acts = getattr(project, "activities", None) or []

    # The recurring WORK words, most common first — this is the vocabulary a
    # rule has to be written in to bind to anything.
    from collections import Counter
    stop = {"the", "and", "for", "with", "from", "into", "per", "all", "new"}
    counts = Counter()
    for a in acts:
        for w in re.findall(r"[A-Za-z][A-Za-z/&-]{2,}", a.name or ""):
            lw = w.lower()
            if lw not in stop:
                counts[w] += 1
    common = [w for w, n in counts.most_common(70) if n >= 2]
    samples = _uniq((a.name for a in acts), 40)

    if not (folders or common or samples):
        return ""
    parts = ["\n\nTHIS JOB'S OWN NAMING — a rule only works if its words appear "
             "in activity names, so phrase every 'directives' entry using words "
             "from these lists, not words from the drawing:"]
    if common:
        parts.append("Words used in activity names: " + ", ".join(common))
    if samples:
        parts.append("Example activity names: " + " | ".join(samples))
    if folders:
        parts.append("Folders / rooms: " + ", ".join(folders))
    parts.append(
        "So write \"Terminations follow Pull Wire in the same room\" (words that "
        "appear above), NOT \"Installation of utility switches precedes breakers\" "
        "(drawing language — matches no activity). If the sheet shows sequencing "
        "you cannot express in these words, put it in facts and leave directives "
        "empty rather than writing one that cannot bind.")
    return "\n".join(parts)


def _parse_reading(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("The model returned no JSON")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise ValueError("That sheet had more on it than one read could capture "
                         "cleanly. Try a tighter crop of the part that matters.")
    return {
        "sheet_number": data.get("sheet_number"),
        "sheet_title": data.get("sheet_title"),
        "discipline": data.get("discipline") or "other",
        "summary": (data.get("summary") or "").strip(),
        "rooms": [str(x) for x in (data.get("rooms") or [])][:40],
        "equipment": [str(x) for x in (data.get("equipment") or [])][:40],
        "facts": [str(x) for x in (data.get("facts") or [])][:25],
        "directives": [str(x) for x in (data.get("directives") or [])][:15],
        "room_flow": _parse_room_flow(data.get("room_flow")),
    }


def _parse_room_flow(raw: Any) -> Optional[Dict[str, Any]]:
    """
    An order read off the layout, or nothing.

    Held to the same bar as everything else here: a flow with no family, or
    fewer than two rooms, is not an order — and a model that answered
    "confidence: none" is telling us it was not reading one, so it is not
    quietly promoted into a rule.
    """
    if not isinstance(raw, dict):
        return None
    fam = str(raw.get("family") or "").strip()
    confidence = str(raw.get("confidence") or "").strip().lower()
    if not fam or confidence == "none":
        return None
    order, seen = [], set()
    for x in (raw.get("order") or [])[:60]:
        m = re.search(r"\d{1,4}", str(x))
        if not m:
            continue
        n = int(m.group(0))
        if n not in seen:
            seen.add(n)
            order.append(n)
    if len(order) < 2:
        return None
    return {"family": fam, "order": order,
            "why": str(raw.get("why") or "").strip(),
            "confidence": confidence if confidence in ("stated", "implied") else "implied"}


def flow_sentence(flow: Dict[str, Any]) -> str:
    """The room flow as a sentence the brain's parser will read as a rule."""
    return (f"{flow['family']} rooms run "
            + ", ".join(str(n) for n in flow["order"]))


def read_drawing(file_bytes: bytes, filename: str, project=None,
                 question: str = "", model_key: str = None,
                 api_key: str = None) -> Dict[str, Any]:
    """
    One drawing in, one structured reading out. Raises RuntimeError with a
    plain sentence when it cannot run — no key, wrong provider for a PDF,
    file too big — so the route can hand the reason straight to the user.
    """
    ext, is_pdf = _check(file_bytes, filename)
    return _parse_reading(_ask_model(
        file_bytes, ext, is_pdf, READ_PROMPT + _job_vocabulary(project),
        "Read this sheet." + (
            "\n\nThe user asked, about this sheet: " + question.strip()
            if (question or "").strip() else ""),
        model_key, api_key))


MAX_SHEETS = 12


def read_drawing_set(files: List[Tuple[bytes, str]], project=None,
                     question: str = "", model_key: str = None,
                     api_key: str = None) -> Dict[str, Any]:
    """
    A set of sheets read together, and what they say as a set.

    One sheet at a time is how a drawing gets read; it is not how a drawing
    SET gets understood. The riser says one thing, the floor plan another, and
    the sequencing note on the third confirms it — evidence that only counts
    once you can see it repeat. So each sheet is still read on its own (they
    are separate images; there is no way around a call each), and then the
    readings are merged with their provenance kept: which sheet said what, and
    what more than one sheet agrees on.

    Nothing is resolved by majority. Where two sheets state different orders
    for the same room family, BOTH are reported as a conflict — a drawing set
    that disagrees with itself is a finding, not a tie to break silently.

    One sheet failing does not lose the rest; its error is carried per-sheet.
    """
    if not files:
        raise RuntimeError("No sheets attached.")
    if len(files) > MAX_SHEETS:
        raise RuntimeError(
            f"{len(files)} sheets at once — read up to {MAX_SHEETS} in a batch. "
            f"Each sheet is a separate read, so a big set is best sent in "
            f"groups you can review as you go.")

    sheets: List[Dict[str, Any]] = []
    for blob, name in files:
        label = name
        try:
            reading = read_drawing(blob, name, project, question=question,
                                   model_key=model_key, api_key=api_key)
            label = (reading.get("sheet_number") or reading.get("sheet_title")
                     or name)
            sheets.append({"filename": name, "label": label,
                           "reading": reading, "error": None})
        except Exception as e:
            sheets.append({"filename": name, "label": label,
                           "reading": None, "error": str(e)})

    ok = [s for s in sheets if s["reading"]]
    if not ok:
        raise RuntimeError("None of those sheets could be read. "
                           + (sheets[0]["error"] or ""))

    def merged(key: str, cap: int) -> List[Dict[str, Any]]:
        """Deduped across sheets, remembering which sheets said it."""
        order: List[str] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for s in ok:
            for item in (s["reading"].get(key) or []):
                text = str(item).strip()
                norm = re.sub(r"\s+", " ", text.lower())
                if not norm:
                    continue
                if norm not in seen:
                    seen[norm] = {"text": text, "sources": []}
                    order.append(norm)
                if s["label"] not in seen[norm]["sources"]:
                    seen[norm]["sources"].append(s["label"])
        return [seen[n] for n in order[:cap]]

    # Room flows, grouped by family. Agreement across sheets is the strongest
    # evidence a drawing set can give; disagreement is worth more than either
    # side of it, so it is surfaced rather than resolved.
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for s in ok:
        flow = s["reading"].get("room_flow")
        if flow:
            by_family.setdefault(flow["family"].upper(), []).append(
                {**flow, "source": s["label"]})

    flows, conflicts = [], []
    for fam, found in by_family.items():
        distinct = {tuple(f["order"]) for f in found}
        if len(distinct) > 1:
            conflicts.append({
                "family": fam,
                "readings": [{"order": f["order"], "source": f["source"],
                              "why": f["why"], "confidence": f["confidence"]}
                             for f in found],
                "why": (f"{len(distinct)} different build orders for {fam} rooms "
                        f"across these sheets — the set does not agree with "
                        f"itself, so neither is proposed."),
            })
            continue
        best = max(found, key=lambda f: (f["confidence"] == "stated", len(f["order"])))
        flows.append({
            "family": fam,
            "order": best["order"],
            "why": best["why"],
            "confidence": best["confidence"],
            "sources": [f["source"] for f in found],
            "agreed_by": len(found),
            "sentence": flow_sentence(best),
        })

    return {
        "sheets": [{"filename": s["filename"], "label": s["label"],
                    "error": s["error"],
                    "summary": (s["reading"] or {}).get("summary", ""),
                    "discipline": (s["reading"] or {}).get("discipline", "")}
                   for s in sheets],
        "read_count": len(ok),
        "failed_count": len(sheets) - len(ok),
        "rooms": merged("rooms", 80),
        "equipment": merged("equipment", 80),
        "facts": merged("facts", 40),
        "directives": merged("directives", 30),
        "room_flows": flows,
        "conflicts": conflicts,
    }


def read_schedule(file_bytes: bytes, filename: str, project=None,
                  question: str = "", model_key: str = None,
                  api_key: str = None) -> Dict[str, Any]:
    """
    A screenshot of a SCHEDULE in, transcribed rows out.

    This is deliberately a transcription, not an interpretation: the rows come
    back as printed and every decision about what to do with them — which
    activity each matches, whether a date is worth changing — is made against
    the real schedule afterwards, where it can be shown to the user before
    anything moves.
    """
    ext, is_pdf = _check(file_bytes, filename)
    raw = _ask_model(
        file_bytes, ext, is_pdf, READ_SCHEDULE_PROMPT,
        "Transcribe the activity rows in this schedule." + (
            "\n\nThe user asked: " + question.strip()
            if (question or "").strip() else ""),
        # A drawing's reading is a handful of facts; a schedule screenshot can
        # run to a hundred-plus rows of structured JSON. 4096 tokens — the
        # default — was cutting that off mid-row on a real full-page capture,
        # which came back as a JSONDecodeError leaking a raw parser message
        # ("Expecting ',' delimiter…") straight to the user instead of a
        # readable answer.
        model_key, api_key, max_tokens=8192)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise RuntimeError("The model did not return readable rows for that image.")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        # Still too much table for one pass, even with the higher cap above —
        # tell the user what to do about it instead of surfacing where the
        # JSON parser gave up.
        raise RuntimeError(
            "That screenshot has more rows than one read can capture cleanly. "
            "Try a tighter crop with fewer rows, or split it into two "
            "screenshots and send them one at a time.")
    rows = []
    for r in (data.get("rows") or [])[:300]:
        if not isinstance(r, dict):
            continue
        rows.append({
            "activity_id": (r.get("activity_id") or None),
            "name": (r.get("name") or None),
            "start": _iso(r.get("start")),
            "finish": _iso(r.get("finish")),
            "actual_start": _iso(r.get("actual_start")),
            "actual_finish": _iso(r.get("actual_finish")),
            "percent_complete": r.get("percent_complete"),
            "status": (r.get("status") or None),
        })
    return {"source_title": data.get("source_title"),
            "data_date": _iso(data.get("data_date")),
            "rows": rows,
            "notes": [str(x) for x in (data.get("notes") or [])][:10]}


def _iso(v) -> Optional[str]:
    """Keep only a date we can actually trust; anything else becomes null."""
    s = str(v or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        return None
    return s


# What the bytes actually ARE, which is not always what the name says.
#
# A phone photo is the common case: iOS names a share "IMG_1234.jpeg" while
# the bytes are HEIC, and a filename-only check hands that to the model as
# image/jpeg. The provider then rejects it with something like "Invalid base64
# image_url", which reads as a bug in this app rather than "your photo is in a
# format the model cannot read".
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"%PDF-", ".pdf"),
)
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")


def sniff_format(file_bytes: bytes) -> Optional[str]:
    """The real extension for these bytes, or None if unrecognised."""
    head = file_bytes[:16] if file_bytes else b""
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP":
        return ".webp"
    # ISO-BMFF: "....ftyp<brand>" — HEIC and friends
    if file_bytes[4:8] == b"ftyp":
        brand = file_bytes[8:12]
        if brand in _HEIC_BRANDS:
            return ".heic"
    return None


def _check(file_bytes: bytes, filename: str):
    ext = os.path.splitext(filename or "")[1].lower()

    # An empty upload used to be encoded to an empty string and posted as
    # "data:image/jpeg;base64," — which is exactly the "Invalid base64" the
    # provider complains about, after the round trip and the token spend.
    if not file_bytes:
        raise RuntimeError("That file came through empty. Re-attach it, or "
                           "drag it in rather than pasting a link to it.")

    # The bytes win. A name is a hint; this is what the model will actually be
    # handed, and disagreeing with it is how a readable image gets refused.
    real = sniff_format(file_bytes)
    if real == ".heic":
        raise RuntimeError(
            "That is an iPhone HEIC photo, even though it is named "
            f"'{filename}' — no model reads HEIC. On the phone: Settings > "
            "Camera > Formats > Most Compatible, or open it and share as JPEG. "
            "A screenshot of the same thing also works.")
    if real:
        ext = real
    elif ext not in _IMAGE_TYPES and ext != ".pdf":
        raise RuntimeError(f"'{ext or filename}' is not a readable image — "
                           f"send a PNG/JPG screenshot or a PDF sheet.")

    is_pdf = ext == ".pdf"
    if not is_pdf and ext not in _IMAGE_TYPES:
        raise RuntimeError(f"'{ext or filename}' is not a readable image — "
                           f"send a PNG/JPG screenshot or a PDF sheet.")
    if is_pdf and len(file_bytes) > _MAX_PDF_BYTES:
        raise RuntimeError("That PDF is over 30MB. Split out the sheets that "
                           "matter and send those.")
    if not is_pdf and len(file_bytes) > _MAX_BYTES:
        raise RuntimeError("That image is over 5MB. A tighter snip of the same "
                           "sheet will read better anyway.")
    return ext, is_pdf


def _ask_model(file_bytes, ext, is_pdf, system_prompt, user_text,
               model_key, api_key, max_tokens: int = 4096) -> str:
    """One image + one prompt to whichever provider is configured."""
    cfg = resolve_model(model_key)
    provider = cfg["provider"]
    b64 = base64.standard_b64encode(file_bytes).decode()

    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("Anthropic API key not set. Enter your key in "
                               "the settings panel.")
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError("anthropic package not installed.")
        block = ({"type": "document",
                  "source": {"type": "base64", "media_type": "application/pdf",
                             "data": b64}} if is_pdf else
                 {"type": "image",
                  "source": {"type": "base64",
                             "media_type": _IMAGE_TYPES[ext], "data": b64}})
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=cfg["model_id"], max_tokens=max_tokens, system=system_prompt,
            messages=[{"role": "user", "content": [
                block, {"type": "text", "text": user_text}]}])
        return resp.content[0].text

    if provider == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OpenAI API key not set. Enter your key in the "
                               "settings panel.")
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("openai package not installed.")
        client = OpenAI(api_key=key)

        def _send(parts):
            resp = client.chat.completions.create(
                model=cfg["model_id"],
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": parts
                           + [{"type": "text", "text": user_text}]}],
                max_completion_tokens=max_tokens)
            return resp.choices[0].message.content

        if not is_pdf:
            return _send([{"type": "image_url", "image_url": {
                "url": f"data:{_IMAGE_TYPES[ext]};base64,{b64}"}}])

        # OpenAI takes a PDF directly on the models that support it. Try that
        # first — it keeps the real text layer, which reads far better than a
        # picture of the same page — and fall back to rendering only if this
        # particular model will not take one.
        try:
            return _send([{"type": "file", "file": {
                "filename": "sheet.pdf",
                "file_data": f"data:application/pdf;base64,{b64}"}}])
        except Exception as native_error:
            if not _raster_available():
                raise RuntimeError(
                    f"This model would not take the PDF directly "
                    f"({native_error}), and PDF rendering is not installed. "
                    f"Send a screenshot of the sheet instead.")
            pages = rasterize_pdf(file_bytes)
            return _send([{"type": "image_url", "image_url": {
                "url": "data:image/png;base64,"
                       + base64.standard_b64encode(p).decode()}}
                for p in pages])

    # An unknown provider is still worth trying: an OpenAI-compatible endpoint
    # is the common case, and refusing outright is how a working key ends up
    # being told the tool cannot help.
    raise RuntimeError(
        f"Unknown provider '{provider}'. Pick a Claude or GPT model in "
        f"settings, or enter the model id exactly as its provider names it.")
