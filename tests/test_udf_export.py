"""
test_udf_export.py — every UDF value must reference a UDF type that is
actually declared, with an element matching that type.

P6 rejects an activity with an invalid-UDF-data-type error when either half
is wrong, and it names the activity, not the field — which is how this
surfaced as "severe error on activity 101962".

Two ways it was wrong:

  - COMMENTS was emitted with ObjectId 813 (as the reference export has it)
    but the value map handed it a second id from the generated range, so
    <TypeObjectId>900</TypeObjectId> pointed at a type that was never
    declared. Any activity carrying a COMMENTS value was rejected.

  - A field declared Double or Integer whose value would not parse as a
    number fell through to <Text>, under a numeric declaration.

The declarations and the values now come from one list, so they cannot drift.
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.xml_writer import (write_p6_xml, _udf_declarations,
                               normalize_udf_type, _VALID_UDF_TYPES)
from engine.schedule_model import (Project, Activity, WBSNode, Calendar, UDFType)


def _proj(udf_types=None):
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None)]
    p.udf_types = udf_types or []
    p.activities = []
    return p


def _act(p, uid, aid, udfs=None):
    a = Activity(uid=uid, activity_id=aid, name=aid, wbs_uid="w", calendar_uid="1",
                 activity_type="Task Dependent", status="Not Started",
                 planned_duration=40.0, remaining_duration=40.0,
                 planned_start="2026-02-02", planned_finish="2026-02-06",
                 udfs=udfs or {})
    p.activities.append(a)
    p.build_lookups()
    return a


def _xml(p):
    f = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    f.close()
    write_p6_xml(p, f.name, p6_version="21.12")
    return open(f.name, encoding="utf-8").read()


def _declared(raw):
    """{ObjectId: (DataType, Title)} for every UDFType block in the file."""
    return {o: (d, t) for d, o, t in re.findall(
        r"<UDFType>\s*<DataType>([^<]*)</DataType>.*?<ObjectId>(\d+)</ObjectId>"
        r".*?<Title>([^<]*)</Title>\s*</UDFType>", raw, re.S)}


def _values(raw):
    """(ObjectId, element tag, text) for every UDF value in the file."""
    return re.findall(
        r"<UDF>\s*<TypeObjectId>(\d+)</TypeObjectId>\s*<(\w+)>([^<]*)</\2>", raw)


# The value element P6 actually expects for each DataType — confirmed against
# Oracle's own generated schema classes (UDFAssignmentType). It is named for
# the VALUE, not the type: a field declared "Integer" carries its value in
# <IntegerValue>, never <Integer>. Writing (or checking for) the un-suffixed
# name is a second, independent way to fail the same "Invalid UDF data type"
# error — one this test suite's own helper was making, matching the writer's
# equally wrong element name, which is exactly why round-tripping through this
# tool's own tests never caught it.
_VALUE_ELEMENT = {"Text": "TextValue", "Integer": "IntegerValue",
                  "Double": "DoubleValue", "Cost": "CostValue",
                  "Indicator": "IndicatorValue",
                  "Start Date": "StartDateValue", "Finish Date": "FinishDateValue"}


def _check(raw):
    """Every value resolves to a declared type, in the element P6 expects."""
    decl = _declared(raw)
    for oid, tag, _val in _values(raw):
        assert oid in decl, f"TypeObjectId {oid} is not declared anywhere"
        dt = decl[oid][0]
        assert tag == _VALUE_ELEMENT.get(dt, "TextValue"), \
            f"<{tag}> under DataType {dt} — expected <{_VALUE_ELEMENT.get(dt)}>"
    return decl


# ── the reported failure ──────────────────────────────────────────────────────

def test_a_comments_value_resolves_to_the_declared_comments_type():
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "101962", "MDC1.PH2.U.2575.1220", {"COMMENTS": "needs rigging plan"})
    raw = _xml(p)
    _check(raw)
    oid = _values(raw)[0][0]
    assert oid == "813"


def test_comments_is_declared_exactly_once():
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "a1", "A1000", {"COMMENTS": "x"})
    titles = [t for _d, _o, t in re.findall(
        r"<UDFType>\s*<DataType>([^<]*)</DataType>.*?<ObjectId>(\d+)</ObjectId>"
        r".*?<Title>([^<]*)</Title>\s*</UDFType>", _xml(p), re.S)]
    assert titles.count("COMMENTS") == 1


def test_comments_is_still_declared_when_nothing_uses_it():
    """The reference export carries it, so a clean file keeps carrying it."""
    p = _proj()
    _act(p, "a1", "A1000")
    assert "COMMENTS" in _xml(p)


# ── fields added in the app ───────────────────────────────────────────────────

def test_a_field_that_only_exists_as_a_value_is_declared():
    p = _proj()
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    raw = _xml(p)
    decl = _check(raw)
    assert any(t == "Number of Electricians" for _d, t in decl.values())


def test_comments_and_a_new_field_get_different_object_ids():
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "a1", "A1000", {"COMMENTS": "x", "Number of Electricians": "6"})
    raw = _xml(p)
    _check(raw)
    oids = {oid for oid, _t, _v in _values(raw)}
    assert len(oids) == 2


def test_several_activities_share_one_declaration():
    p = _proj()
    for i in range(3):
        _act(p, f"a{i}", f"A100{i}", {"Number of Electricians": str(i + 1)})
    raw = _xml(p)
    _check(raw)
    assert len({oid for oid, _t, _v in _values(raw)}) == 1


# ── numeric fields ────────────────────────────────────────────────────────────

def test_an_integer_field_is_written_as_an_integer():
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="Integer")])
    _act(p, "a1", "A1000", {"Crew Size": "6"})
    raw = _xml(p)
    _check(raw)
    assert _values(raw)[0][1] == "IntegerValue"


def test_a_double_field_is_written_as_a_double():
    p = _proj([UDFType(uid="9", title="Tonnage", data_type="Double")])
    _act(p, "a1", "A1000", {"Tonnage": "12.5"})
    raw = _xml(p)
    _check(raw)
    assert _values(raw)[0][1] == "DoubleValue"


def test_a_numeric_field_holding_text_is_declared_as_text_not_mismatched():
    """Downgrading keeps the value; <Text> under Double is what P6 rejects."""
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="Integer")])
    _act(p, "a1", "A1000", {"Crew Size": "6"})
    _act(p, "a2", "A1010", {"Crew Size": "TBD"})
    raw = _xml(p)
    decl = _check(raw)
    assert any(d == "Text" and t == "Crew Size" for d, t in decl.values())
    assert {v for _o, _t, v in _values(raw)} == {"6", "TBD"}


def test_an_xer_style_data_type_is_translated():
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="FT_STATIC_TYPE_INT")])
    _act(p, "a1", "A1000", {"Crew Size": "4"})
    raw = _xml(p)
    _check(raw)
    assert _values(raw)[0][1] == "IntegerValue"


# ── nothing to write ──────────────────────────────────────────────────────────

def test_an_empty_value_is_not_written():
    p = _proj()
    _act(p, "a1", "A1000", {"Number of Electricians": ""})
    assert _values(_xml(p)) == []


def test_an_activity_with_no_udfs_emits_none():
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "a1", "A1000")
    assert _values(_xml(p)) == []


def test_the_declaration_list_is_stable_across_calls():
    """The oid map and the UDFType blocks are built from it separately."""
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "a1", "A1000", {"COMMENTS": "x", "Number of Electricians": "6"})
    assert _udf_declarations(p) == _udf_declarations(p)


# ── P6's internal type codes are not XML DataTypes ───────────────────────────
# Reported twice from real imports:
#   "Invalid UDF data type when importing Activity 101962"
#   "Invalid UDF data type when importing Activity 101953"
# The file declared <DataType>FT_TEXT</DataType> — P6's INTERNAL code, which
# arrives via an XER round trip or the API. The first fix mapped only the
# FT_STATIC_TYPE_* spellings and let anything else through untouched, so a
# plain FT_TEXT sailed out again and failed the same way. It is a whitelist
# now: an unrecognised code costs that field its type, never the whole import.

def test_the_internal_code_that_broke_the_import_is_translated():
    assert normalize_udf_type("FT_TEXT") == "Text"


def test_every_internal_code_maps_to_something_p6_accepts():
    for code in ("FT_TEXT", "FT_INT", "FT_FLOAT", "FT_MONEY", "FT_END_DATE",
                 "FT_START_DATE", "FT_STATIC_TYPE", "FT_STATIC_TYPE_TEXT",
                 "FT_STATIC_TYPE_DOUBLE", "FT_STATIC_TYPE_INT",
                 "FT_STATIC_TYPE_DATE"):
        assert normalize_udf_type(code) in _VALID_UDF_TYPES, code


def test_a_code_nobody_has_thought_of_yet_still_exports():
    """The whole point of a whitelist — the next unknown must not fail again."""
    for junk in ("FT_SOMETHING_NEW", "wibble", "", None, "12345"):
        assert normalize_udf_type(junk) in _VALID_UDF_TYPES


def test_a_type_that_is_already_valid_is_left_alone():
    for good in _VALID_UDF_TYPES:
        assert normalize_udf_type(good) == good


def test_casing_differences_are_tolerated_not_discarded():
    assert normalize_udf_type("text") == "Text"
    assert normalize_udf_type("INTEGER") == "Integer"


def test_an_internal_code_never_reaches_the_exported_file():
    p = _proj([UDFType(uid="901", title="Number of Electricians",
                       data_type="FT_TEXT")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    xml = _xml(p)
    assert "FT_TEXT" not in xml
    for dt in re.findall(r"<DataType>(.*?)</DataType>", xml):
        assert dt in _VALID_UDF_TYPES, dt


def test_the_value_still_points_at_a_declared_type_after_translating():
    p = _proj([UDFType(uid="901", title="Number of Electricians",
                       data_type="FT_TEXT")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    xml = _xml(p)
    declared = set(re.findall(r"<UDFType>.*?<ObjectId>(.*?)</ObjectId>.*?</UDFType>",
                              xml, re.S))
    for ref in set(re.findall(r"<TypeObjectId>(.*?)</TypeObjectId>", xml)):
        assert ref in declared, f"value references undeclared type {ref}"


def test_reading_a_file_normalises_the_type_in_memory():
    """So nothing downstream has to remember to translate it."""
    import tempfile as _tf
    from engine.xml_reader import load_xml
    p = _proj([UDFType(uid="901", title="Number of Electricians",
                       data_type="FT_TEXT")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    fh = _tf.NamedTemporaryFile(suffix=".xml", delete=False)
    fh.close()
    write_p6_xml(p, fh.name)
    back = load_xml(fh.name)
    os.unlink(fh.name)
    for t in back.udf_types:
        assert t.data_type in _VALID_UDF_TYPES, (t.title, t.data_type)


# ── an export that leaves UDFs out entirely ──────────────────────────────────
# P6 can refuse a whole import over its user-defined fields while the schedule
# itself is perfectly valid. 2,776 activities failing to import over 19 crew
# counts is a bad trade, so the fields can be dropped and the schedule kept.

def _xml_no_udfs(p):
    import tempfile as _tf
    f = _tf.NamedTemporaryFile(suffix=".xml", delete=False)
    f.close()
    write_p6_xml(p, f.name, p6_version="21.12", include_udfs=False)
    raw = open(f.name, encoding="utf-8").read()
    os.unlink(f.name)
    return raw


def test_omitting_udfs_removes_every_trace_of_them():
    p = _proj([UDFType(uid="901", title="Number of Electricians", data_type="Integer")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    raw = _xml_no_udfs(p)
    assert "<UDFType>" not in raw
    assert "<UDF>" not in raw
    assert "<TypeObjectId>" not in raw


def test_omitting_udfs_keeps_the_whole_schedule():
    p = _proj([UDFType(uid="901", title="Number of Electricians", data_type="Integer")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    _act(p, "a2", "A1010", {"Number of Electricians": "4"})
    raw = _xml_no_udfs(p)
    assert raw.count("<Activity>") == 2
    assert "A1000" in raw and "A1010" in raw


def test_the_default_still_includes_them():
    p = _proj([UDFType(uid="901", title="Number of Electricians", data_type="Integer")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    assert "<UDF>" in _xml(p)


def test_the_download_route_can_drop_them():
    import io as _io
    import server as _server
    p = _proj([UDFType(uid="901", title="Number of Electricians", data_type="Integer")])
    _act(p, "a1", "A1000", {"Number of Electricians": "6"})
    _server._projects.clear()
    sess = _server._make_session("t", "t.xml")
    sess["project"] = p
    _server._projects["t"] = sess
    _server._active_id[0] = "t"
    c = _server.app.test_client()
    with_udfs = c.get("/api/download").data.decode("utf-8")
    without = c.get("/api/download?udfs=0").data.decode("utf-8")
    assert "<UDF>" in with_udfs and "<UDF>" not in without
    assert without.count("<Activity>") == with_udfs.count("<Activity>")


# ── the real bug: schema-verified element names and enum literals ────────────
# Confirmed against Oracle's own generated JAXB schema classes (the
# UDFAssignmentType and UDFTypeType complex types, code-generated straight
# from the real p6apibo.xsd) rather than against another guess:
#
#   - the value element is named for the VALUE, not the type. A field
#     declared "Integer" carries its value in <IntegerValue>, never
#     <Integer>. Every value this writer had ever produced sat inside an
#     element P6's schema does not define at all — <Text>, <Integer>,
#     <Double> instead of <TextValue>, <IntegerValue>, <DoubleValue>. That
#     alone reproduces "Invalid UDF data type" regardless of whether the
#     DataType on the declaration is valid, which is why fixing only the
#     DataType did not clear the error that had already been reported twice.
#
#   - the DataType enum has no "Date" member, and the date members carry a
#     SPACE: "Start Date" / "Finish Date", not "StartDate" / "FinishDate".
#     The first fix used the un-spaced spellings, which are just as invalid
#     as the FT_TEXT they were written to replace.
#
# Both bugs were invisible to every prior test because the reader used the
# same wrong element names as the writer, so round-tripping through this
# tool's own tests always agreed with itself — just never with real P6.

def test_an_integer_value_sits_in_the_element_named_for_the_value():
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="Integer")])
    _act(p, "a1", "A1000", {"Crew Size": "6"})
    raw = _xml(p)
    assert "<IntegerValue>6</IntegerValue>" in raw
    assert "<Integer>" not in raw


def test_a_text_value_sits_in_textvalue_not_text():
    p = _proj([UDFType(uid="813", title="COMMENTS", data_type="Text")])
    _act(p, "a1", "A1000", {"COMMENTS": "needs rigging plan"})
    raw = _xml(p)
    assert "<TextValue>needs rigging plan</TextValue>" in raw
    assert "<Text>" not in raw


def test_a_double_value_sits_in_doublevalue_not_double():
    p = _proj([UDFType(uid="9", title="Tonnage", data_type="Double")])
    _act(p, "a1", "A1000", {"Tonnage": "12.5"})
    raw = _xml(p)
    assert "<DoubleValue>12.5</DoubleValue>" in raw
    assert "<Double>" not in raw


def test_date_is_not_a_real_datatype_member():
    """The enum has Start Date / Finish Date; plain "Date" does not exist."""
    assert "Date" not in _VALID_UDF_TYPES
    assert "Start Date" in _VALID_UDF_TYPES
    assert "Finish Date" in _VALID_UDF_TYPES


def test_the_date_members_carry_a_space():
    assert normalize_udf_type("StartDate") != "StartDate"   # not a real member
    assert normalize_udf_type("Start Date") == "Start Date"


def test_a_field_this_app_cannot_reliably_format_downgrades_to_text():
    """
    Indicator / Code / date UDFs need formatting (a fixed status word, a code
    list lookup, a real date) this app has no logic for. Rather than write an
    arbitrary string into an element whose shape the schema constrains,
    declaring the field Text keeps the data with no risk of a new mismatch.
    """
    p = _proj([UDFType(uid="9", title="Priority", data_type="Indicator")])
    _act(p, "a1", "A1000", {"Priority": "High"})
    raw = _xml(p)
    decl = _check(raw)                       # still internally consistent
    assert any(d == "Text" for d, _t in decl.values())
    assert "<TextValue>High</TextValue>" in raw


def test_every_real_datatype_member_gets_its_own_named_value_element():
    assert set(_VALUE_ELEMENT) == set(_VALID_UDF_TYPES) - {"Code"}
    assert _VALUE_ELEMENT == {
        "Text": "TextValue", "Integer": "IntegerValue", "Double": "DoubleValue",
        "Cost": "CostValue", "Indicator": "IndicatorValue",
        "Start Date": "StartDateValue", "Finish Date": "FinishDateValue"}


# ── the reader accepts a genuinely P6-authored file ───────────────────────────

def _real_p6_udf_block(oid, tag, value):
    """What an actual P6 export puts inside <Activity>, not this tool's."""
    return f"<UDF><TypeObjectId>{oid}</TypeObjectId><{tag}>{value}</{tag}></UDF>"


def test_a_genuinely_p6_authored_value_element_is_read():
    """
    Before this fix, a real P6 file's <IntegerValue> was invisible to this
    reader — it only ever recognised the un-suffixed names this tool's own
    (equally wrong) writer produced. A schedule round-tripped out of real P6
    and back into this tool would have silently lost every UDF value.
    """
    import tempfile as _tf
    from engine.xml_reader import load_xml
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6Professional/V19.12/API/BusinessObjects">
  <Project>
    <Id>P</Id><ObjectId>1</ObjectId>
    <UDFType><DataType>Integer</DataType><ObjectId>9</ObjectId>
      <SubjectArea>Activity</SubjectArea><Title>Crew Size</Title></UDFType>
    <Calendar><ObjectId>1</ObjectId><Name>Std</Name><IsDefault>1</IsDefault>
      <StandardWorkWeek><StandardWorkHours><DayOfWeek>Monday</DayOfWeek>
      <Hours>8</Hours></StandardWorkHours></StandardWorkWeek></Calendar>
    <WBS><ObjectId>1</ObjectId><Code>W</Code><Name>Work</Name><ProjectObjectId>1</ProjectObjectId></WBS>
    <Activity><ObjectId>1</ObjectId><Id>A1000</Id><Name>A1000</Name>
      <Type>Task Dependent</Type><Status>Not Started</Status>
      <CalendarObjectId>1</CalendarObjectId><WBSObjectId>1</WBSObjectId>
      {_real_p6_udf_block(9, "IntegerValue", 6)}
    </Activity>
  </Project>
</APIBusinessObjects>'''
    fh = _tf.NamedTemporaryFile(suffix=".xml", delete=False, mode="w")
    fh.write(xml)
    fh.close()
    proj = load_xml(fh.name)
    os.unlink(fh.name)
    act = next(a for a in proj.activities if a.activity_id == "A1000")
    assert act.udfs.get("Crew Size") == "6"


def test_this_tools_own_legacy_export_still_reads_back():
    """A file exported before this fix, still sitting on someone's disk."""
    import tempfile as _tf
    from engine.xml_reader import load_xml
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6Professional/V19.12/API/BusinessObjects">
  <Project>
    <Id>P</Id><ObjectId>1</ObjectId>
    <UDFType><DataType>Integer</DataType><ObjectId>9</ObjectId>
      <SubjectArea>Activity</SubjectArea><Title>Crew Size</Title></UDFType>
    <Calendar><ObjectId>1</ObjectId><Name>Std</Name><IsDefault>1</IsDefault>
      <StandardWorkWeek><StandardWorkHours><DayOfWeek>Monday</DayOfWeek>
      <Hours>8</Hours></StandardWorkHours></StandardWorkWeek></Calendar>
    <WBS><ObjectId>1</ObjectId><Code>W</Code><Name>Work</Name><ProjectObjectId>1</ProjectObjectId></WBS>
    <Activity><ObjectId>1</ObjectId><Id>A1000</Id><Name>A1000</Name>
      <Type>Task Dependent</Type><Status>Not Started</Status>
      <CalendarObjectId>1</CalendarObjectId><WBSObjectId>1</WBSObjectId>
      {_real_p6_udf_block(9, "Integer", 6)}
    </Activity>
  </Project>
</APIBusinessObjects>'''
    fh = _tf.NamedTemporaryFile(suffix=".xml", delete=False, mode="w")
    fh.write(xml)
    fh.close()
    proj = load_xml(fh.name)
    os.unlink(fh.name)
    act = next(a for a in proj.activities if a.activity_id == "A1000")
    assert act.udfs.get("Crew Size") == "6"


def test_a_full_export_round_trips_through_the_real_reader_correctly():
    """Write, then read back with the SAME reader a real P6 file would use."""
    import tempfile as _tf
    from engine.xml_reader import load_xml
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="Integer")])
    _act(p, "a1", "A1000", {"Crew Size": "6"})
    fh = _tf.NamedTemporaryFile(suffix=".xml", delete=False)
    fh.close()
    write_p6_xml(p, fh.name)
    back = load_xml(fh.name)
    os.unlink(fh.name)
    act = next(a for a in back.activities if a.activity_id == "A1000")
    assert act.udfs.get("Crew Size") == "6"


# ── the position <UDF> sits at within <Activity> ──────────────────────────────
# Verified against the real schema's ActivityType propOrder: every plain field
# is alphabetical, and <UDF> comes after the LAST of them (WBSObjectId, for
# everything this app emits) — not sorted into alphabetical position among the
# plain fields, which is where an earlier attempt at this fix put it (between
# <Type> and <UnitsPercentComplete>) and was rightly reverted as unverified.

def test_udf_comes_after_every_plain_activity_field():
    p = _proj([UDFType(uid="9", title="Crew Size", data_type="Integer")])
    _act(p, "a1", "A1000", {"Crew Size": "6"})
    raw = _xml(p)
    one = raw[raw.index("<Activity>"):raw.index("</Activity>") + len("</Activity>")]
    assert one.index("<UDF>") > one.index("<WBSObjectId>")


def test_the_plain_fields_are_still_alphabetical():
    p = _proj()
    _act(p, "a1", "A1000")
    raw = _xml(p)
    one = raw[raw.index("<Activity>"):raw.index("</Activity>") + len("</Activity>")]
    tags = re.findall(r"<(\w+)>", one)[1:-1]     # drop the Activity wrapper itself
    assert tags == sorted(tags)
