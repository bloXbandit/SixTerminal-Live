# -*- coding: utf-8 -*-
"""
xml_audit.py — check the file before P6 does.

WHY
  P6 does not fail an import over a broken reference. It writes a line into
  the import log —

    Referenced business object Calendar ... cannot be found,
    ignoring field CalendarObjectId

  — and carries on. The file imports, a dialog says it worked, and an activity
  quietly has no calendar. Four bugs of exactly this shape turned up in one
  week of work on this exporter: resources dropped, labour zeroed, calendars
  replaced with canned ones, ObjectIds fabricated. Every one of them imported
  cleanly and was only visible by going and looking in P6 afterwards.

  So the file is checked here, against itself, before it leaves. Not a schema
  validation — that needs Oracle's XSD, which is not redistributable — but the
  class of error a schema would not catch anyway: a reference that is
  well-formed, correctly typed, and points at nothing.

WHAT IT CANNOT TELL YOU
  Whether P6 will accept the file. Element order, required fields and
  enumerated values are the schema's business, and until the XSD is to hand
  the only real proof is an import log. This narrows the gap; it does not
  close it.
"""

import collections
import re
from typing import Any, Dict, List, Optional

# References that are MEANT to point outside the file. These name things that
# must already exist in the target P6 database, which is why they are settable
# per environment through write_p6_xml's target_profile.
#
# An entry is either a field name — that field always points outside — or a
# (field, ObjectId) pair, which excuses that one value and no other. The pair
# form is the one to reach for: exporting against an existing resource makes
# ResourceObjectId = 4147 deliberate, but it does not make a DIFFERENT
# dangling ResourceObjectId acceptable, and a blanket field exemption would
# hide exactly the bug this module exists to catch.
EXTERNAL_REFS = {
    "ParentEPSObjectId",     # the EPS node the project is imported under
}

_TAG = re.compile(r"\{[^}]*\}")          # strip the P6 default namespace


def _walk(xml: str):
    """
    Every (element tag, declared ObjectId) and (element tag, reference tag,
    value) in the file.

    Parsed rather than pattern-matched. A first attempt read the file with a
    regex and quietly missed both a duplicate and a reference repeated two
    thousand times, which is a poor showing for a checker whose whole job is
    to notice what nobody else does. The file is well-formed XML; reading it
    as anything else is a choice, and it was the wrong one.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    parent_of = {id(c): _TAG.sub("", el.tag) for el in root.iter() for c in el}
    for el in root.iter():
        tag = _TAG.sub("", el.tag)
        parent = parent_of.get(id(el), "")
        declared = None
        refs = []
        for child in el:
            ctag = _TAG.sub("", child.tag)
            text = (child.text or "").strip()
            if not text.isdigit():
                continue
            if ctag == "ObjectId":
                declared = text
            elif ctag.endswith("ObjectId"):
                refs.append((ctag, text))
        yield parent, tag, declared, refs


def declared_ids(xml: str) -> Dict[str, set]:
    """Every ObjectId the file defines, grouped by the element defining it."""
    out: Dict[str, set] = collections.defaultdict(set)
    for _, tag, declared, _ in _walk(xml):
        if declared is not None:
            out[tag].add(declared)
    return dict(out)


def reference_problems(xml: str,
                       external: Optional[set] = None) -> List[Dict[str, Any]]:
    """
    Every reference in the file that points at nothing.

    Returns one entry per distinct (reference kind, value), each carrying how
    many times it occurs — a single bad calendar id repeated on two thousand
    activities is one problem, not two thousand.

    `external` holds the references that point outside the file on purpose,
    as field names or (field, ObjectId) pairs — see EXTERNAL_REFS.
    """
    external = EXTERNAL_REFS if external is None else set(external)
    known = set()
    for _, _, declared, _ in _walk(xml):
        if declared is not None:
            known.add(declared)

    counts: Dict[tuple, int] = collections.Counter()
    for _, _, _, refs in _walk(xml):
        for tag, val in refs:
            if tag in external or (tag, val) in external:
                continue
            if val not in known:
                counts[(tag, val)] += 1

    return [{"field": tag, "value": val, "occurrences": n,
             "why": f"no element in this file declares ObjectId {val}"}
            for (tag, val), n in sorted(counts.items(), key=lambda kv: -kv[1])]


def duplicate_ids(xml: str) -> List[Dict[str, Any]]:
    """
    One ObjectId declared twice by the same kind of element.

    P6 resolves a reference to whichever it loaded last, so a duplicate is not
    a loud failure but a silent reassignment — two activities on one calendar
    that only one of them was meant to have.
    """
    # Scoped to (parent, element). Two <Calendar> ObjectId 6590 under the same
    # <Project> is a genuine clash. A <Currency> restated inside
    # <DisplayCurrency> and again at the top level is not — that is the native
    # P6 layout, and flagging it would train everyone to ignore this check.
    seen: Dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for parent, tag, declared, _ in _walk(xml):
        if declared is not None:
            seen[(parent, tag)][declared] += 1
    return [{"element": tag, "parent": parent, "value": oid, "count": n,
             "why": f"{tag} ObjectId {oid} is declared {n} times under {parent or 'the root'}"}
            for (parent, tag), c in seen.items() for oid, n in c.items() if n > 1]


def audit(xml: str, external: Optional[set] = None) -> Dict[str, Any]:
    """Everything this module can check, in one pass."""
    refs = reference_problems(xml, external=external)
    dups = duplicate_ids(xml)
    return {
        "ok": not refs and not dups,
        "dangling_references": refs,
        "duplicate_object_ids": dups,
        "counts": {t: len(i) for t, i in sorted(declared_ids(xml).items())},
    }


def describe(result: Dict[str, Any], limit: int = 10) -> str:
    """The audit as something a person can act on."""
    if result["ok"]:
        return "Every reference in this file resolves."
    lines = []
    if result["dangling_references"]:
        n = len(result["dangling_references"])
        lines.append(f"{n} reference{'' if n == 1 else 's'} point at nothing. "
                     f"P6 will not fail the import — it logs each one and "
                     f"leaves the field empty:")
        lines += [f"  {p['field']} = {p['value']}  ({p['occurrences']} time"
                  f"{'' if p['occurrences'] == 1 else 's'})"
                  for p in result["dangling_references"][:limit]]
    if result["duplicate_object_ids"]:
        lines.append(f"{len(result['duplicate_object_ids'])} ObjectId(s) declared "
                     f"more than once — P6 keeps whichever it loaded last:")
        lines += [f"  {p['element']} {p['value']} x{p['count']}"
                  for p in result["duplicate_object_ids"][:limit]]
    return "\n".join(lines)


def audit_project(project, **write_kw) -> Dict[str, Any]:
    """
    Write the project as it would be exported, and audit that.

    The writer says which references it aimed outside the file on purpose —
    exporting against a resource P6 already holds points every assignment at
    an ObjectId this file will never declare. Those are read back here, so the
    audit reports that mode as intended rather than as one broken pointer per
    assignment.
    """
    import os
    import tempfile
    from .xml_writer import write_p6_xml
    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()
    try:
        write_p6_xml(project, tmp.name, **write_kw)
        deliberate = EXTERNAL_REFS | set(
            getattr(write_p6_xml, "last_external_refs", None) or ())
        with open(tmp.name, encoding="utf-8") as fh:
            return audit(fh.read(), external=deliberate)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
