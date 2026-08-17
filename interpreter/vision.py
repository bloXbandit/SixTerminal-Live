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


def _job_vocabulary(project) -> str:
    """The job's own room and folder names, so the reading speaks its language."""
    if project is None:
        return ""
    names: List[str] = []
    for w in getattr(project, "wbs_nodes", None) or []:
        n = (w.name or "").strip()
        if n and len(n) <= 40:
            names.append(n)
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    if not out:
        return ""
    return ("\n\nTHIS JOB'S OWN NAMING (rooms and folders from the schedule — "
            "use these spellings when the sheet shows the same places):\n"
            + ", ".join(out[:120]))


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
    ext = os.path.splitext(filename or "")[1].lower()
    is_pdf = ext == ".pdf"
    if not is_pdf and ext not in _IMAGE_TYPES:
        raise RuntimeError(f"'{ext or filename}' is not a readable drawing — "
                           f"send a PNG/JPG screenshot or a PDF sheet.")
    if is_pdf and len(file_bytes) > _MAX_PDF_BYTES:
        raise RuntimeError("That PDF is over 30MB. Split out the sheets that "
                           "matter and send those.")
    if not is_pdf and len(file_bytes) > _MAX_BYTES:
        raise RuntimeError("That image is over 5MB. A tighter snip of the same "
                           "sheet will read better anyway.")

    cfg = MODELS.get(model_key) or MODELS[DEFAULT_MODEL]
    provider = cfg["provider"]

    prompt = READ_PROMPT + _job_vocabulary(project)
    ask = ("\n\nThe user asked, about this sheet: " + question.strip()
           if (question or "").strip() else "")
    b64 = base64.standard_b64encode(file_bytes).decode()

    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("Anthropic API key not set. Enter your key in "
                               "the settings panel.")
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError("anthropic package not installed.")
        if is_pdf:
            block = {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf",
                                "data": b64}}
        else:
            block = {"type": "image",
                     "source": {"type": "base64",
                                "media_type": _IMAGE_TYPES[ext], "data": b64}}
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=cfg["model_id"], max_tokens=2048, system=prompt,
            messages=[{"role": "user", "content": [
                block, {"type": "text",
                        "text": "Read this sheet." + ask}]}])
        raw = resp.content[0].text
        return _parse_reading(raw)

    if provider == "openai":
        if is_pdf:
            raise RuntimeError("PDF drawings need the Claude model — switch the "
                               "model in settings, or send a screenshot of the "
                               "sheet instead.")
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
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:{_IMAGE_TYPES[ext]};base64,{b64}"}},
                    {"type": "text", "text": "Read this sheet." + ask},
                ]}],
            max_completion_tokens=2048)
        return _parse_reading(resp.choices[0].message.content)

    raise RuntimeError(f"Unknown provider '{provider}'.")
