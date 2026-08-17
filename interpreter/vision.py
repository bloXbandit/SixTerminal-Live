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
import json
import os
import re
from typing import Any, Dict, List, Optional

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

from .llm_interpreter import MODELS, DEFAULT_MODEL

_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
# The Anthropic API refuses images over 5MB; telling the user to snip tighter
# beats silently degrading a drawing until it is unreadable.
_MAX_BYTES = 5 * 1024 * 1024
_MAX_PDF_BYTES = 30 * 1024 * 1024

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
  "directives": ["only sequencing statements the sheet clearly supports, phrased in plain rule language like 'X follows Y in the same room' or 'X before Y' — empty list if the sheet shows layout but no order"]
}

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


def classify_image_intent(question: str) -> str:
    """
    'schedule' when the ask is about rows, dates or status; 'drawing' otherwise.

    A bare upload with no question is a drawing — that is the common case, and
    a wrong schedule read on a drawing returns an empty row list, which is a
    worse answer than simply reading the sheet.
    """
    q = (question or "").strip()
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
    data = json.loads(m.group(0))
    return {
        "sheet_number": data.get("sheet_number"),
        "sheet_title": data.get("sheet_title"),
        "discipline": data.get("discipline") or "other",
        "summary": (data.get("summary") or "").strip(),
        "rooms": [str(x) for x in (data.get("rooms") or [])][:40],
        "equipment": [str(x) for x in (data.get("equipment") or [])][:40],
        "facts": [str(x) for x in (data.get("facts") or [])][:25],
        "directives": [str(x) for x in (data.get("directives") or [])][:15],
    }


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
        model_key, api_key)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise RuntimeError("The model did not return readable rows for that image.")
    data = json.loads(m.group(0))
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


def _check(file_bytes: bytes, filename: str):
    ext = os.path.splitext(filename or "")[1].lower()
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
               model_key, api_key) -> str:
    """One image + one prompt to whichever provider is configured."""
    cfg = MODELS.get(model_key) or MODELS[DEFAULT_MODEL]
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
            model=cfg["model_id"], max_tokens=4096, system=system_prompt,
            messages=[{"role": "user", "content": [
                block, {"type": "text", "text": user_text}]}])
        return resp.content[0].text

    if provider == "openai":
        if is_pdf:
            raise RuntimeError("PDF pages need the Claude model — switch the "
                               "model in settings, or send a screenshot "
                               "instead.")
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OpenAI API key not set. Enter your key in the "
                               "settings panel.")
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("openai package not installed.")
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=cfg["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:{_IMAGE_TYPES[ext]};base64,{b64}"}},
                    {"type": "text", "text": user_text},
                ]}],
            max_completion_tokens=4096)
        return resp.choices[0].message.content

    raise RuntimeError(f"Unknown provider '{provider}'.")
