"""
test_scope_document.py — a scope of work becoming sequencing understanding.

A 497-line MEP scope across nine multi-column pages is the wrong thing to hand
a vision model: it would cost a fortune, take minutes, and leave you unable to
answer "did it read all of them?". So it is read deterministically — every
line, exactly, free, with a count you can check — and then distilled into the
much smaller thing that actually matters: which systems this job carries, how
far each is taken, and in which phase.

What is defended here is the whole bargain. The extraction is COMPLETE (the
laziness guard). The distillation is SMALL (the token guard — 497 rows never
reach a prompt). The graph only opines on work the document actually covered
(the guessing guard). And it sits below anything the user typed, so a misread
line in a 497-row PDF can never override somebody who walked the job.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine import project_brain as pb
from engine import scope_graph as sg
from engine import scope_reader
from engine.logic_advisor import _Ctx, implied_lag, score_tie
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


# ── a real PDF, built here so the test owns its own input ────────────────────

def _make_pdf(lines, per_page=60):
    """
    A genuine multi-page PDF with a text layer, written by the same renderer
    the app uses. Not a fixture file: the point is to prove the reader copes
    with a document of real size and shape, and a checked-in binary could not
    be varied.
    """
    pypdfium2 = pytest.importorskip("pypdfium2")
    from PIL import Image  # noqa: F401  (pypdfium2 renders through Pillow)
    import textwrap

    # pypdfium2 does not author PDFs, so build one by hand — a tiny writer is
    # far less machinery than pulling in a full PDF library for a test.
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    pages, objs = [], []
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    for chunk in chunks:
        content = "BT /F1 9 Tf 36 750 Td 11 TL\n"
        for text in chunk:
            content += f"({esc(text[:110])}) Tj T*\n"
        content += "ET"
        pages.append(content)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]

    def obj(num, body: bytes):
        offsets.append(out.tell())
        out.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    n_pages = len(pages)
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n_pages))
    obj(1, b"<</Type/Catalog/Pages 2 0 R>>")
    obj(2, f"<</Type/Pages/Kids[{kids}]/Count {n_pages}"
           f"/MediaBox[0 0 612 792]>>".encode())
    for i, content in enumerate(pages):
        pid, cid = 3 + i * 2, 4 + i * 2
        obj(pid, f"<</Type/Page/Parent 2 0 R/Contents {cid} 0 R"
                 f"/Resources<</Font<</F1 {3 + n_pages * 2} 0 R>>>>>>".encode())
        data = content.encode("latin-1", "replace")
        obj(cid, f"<</Length {len(data)}>>\nstream\n".encode() + data
            + b"\nendstream")
    obj(3 + n_pages * 2, b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    start = out.tell()
    total = 3 + n_pages * 2 + 1
    out.write(f"xref\n0 {total}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<</Size {total}/Root 1 0 R>>\nstartxref\n"
              f"{start}\n%%EOF".encode())
    return out.getvalue()


_SYSTEMS = ["generator", "MV switchgear", "transformer", "UPS", "PDU",
            "busway", "chiller", "cooling tower", "CRAH unit", "pump"]
_VERBS = ["Furnish and install", "Deliver", "Set", "Terminate feeders at",
          "Megger test", "Commission"]


def _big_scope(n=497):
    """497 lines that look like a real MEP scope, across several phases."""
    out = []
    for i in range(n):
        system = _SYSTEMS[i % len(_SYSTEMS)]
        verb = _VERBS[(i // len(_SYSTEMS)) % len(_VERBS)]
        phase = (i % 3) + 1
        out.append(f"{i+1:03d}  {verb} ({(i % 6) + 1}) {system}, Phase {phase}"
                   f"   Qty {(i % 9) + 1} EA   Unit LS")
    return out


# ── the extraction is complete and free ──────────────────────────────────────

def test_every_line_of_a_497_line_document_is_read():
    """The laziness guard. A count you can check is the whole point — "did it
    miss anything?" has to be answerable."""
    pdf = _make_pdf(_big_scope(497), per_page=60)
    rep = scope_reader.read_scope(pdf)
    assert rep["line_count"] == 497, f"read {rep['line_count']} of 497"
    assert rep["pages"] == 9


def test_the_lines_come_back_in_document_order_with_their_page():
    pdf = _make_pdf(_big_scope(120), per_page=40)
    rep = scope_reader.read_scope(pdf)
    assert [l.n for l in rep["lines"]] == list(range(1, 121))
    assert rep["lines"][0].page == 1 and rep["lines"][-1].page == 3


def test_a_page_window_can_be_taken_without_rereading_everything():
    pdf = _make_pdf(_big_scope(120), per_page=40)
    rep = scope_reader.read_scope(pdf)
    page2 = scope_reader.page_window(rep, 2, 2)
    assert page2 and all(l.page == 2 for l in page2)


def test_page_furniture_is_not_mistaken_for_scope():
    pdf = _make_pdf(["Page 3 of 9", "Confidential - do not distribute",
                     "Rev B", "Furnish and install (2) generators, Phase 1"])
    rep = scope_reader.read_scope(pdf)
    assert rep["line_count"] == 1
    assert "generators" in rep["lines"][0].text


def test_a_bare_number_or_code_is_not_a_scope_line():
    pdf = _make_pdf(["12", "A-1", "4 EA",
                     "Furnish and install (2) generators, Phase 1"])
    assert scope_reader.read_scope(pdf)["line_count"] == 1


def test_a_scan_with_no_text_says_so_rather_than_returning_nothing():
    """Silently returning zero lines would read as "your document is empty"."""
    pdf = _make_pdf([])
    with pytest.raises(RuntimeError) as e:
        scope_reader.read_scope(pdf)
    assert "scan" in str(e.value).lower()


def test_something_that_is_not_a_pdf_is_a_plain_sentence():
    with pytest.raises(RuntimeError) as e:
        scope_reader.read_scope(b"not a pdf")
    assert "could not be opened" in str(e.value)


# ── classification: the same reader for scope lines and activity names ───────

def test_a_scope_line_and_the_activity_that_delivers_it_land_together():
    """If these disagreed, nothing could ever be matched up."""
    a = sg.classify("Terminate generator feeders at MV switchgear, Phase 2")
    b = sg.classify("Generator Terminations (Gen 318)")
    assert a[0] == b[0] == "generator"
    assert a[1] == b[1] == "connect"


def test_the_latest_stage_a_line_mentions_is_the_one_it_delivers():
    """"Furnish, install and terminate" ends at terminate."""
    assert sg.classify("Furnish, install and terminate (4) generators")[1] == "connect"


def test_boilerplate_classifies_as_nothing_rather_than_being_guessed_at():
    assert sg.classify("Quantities are approximate. Unit price LS.") == (None, None)


def test_a_system_with_no_verb_makes_no_sequencing_claim():
    assert sg.classify("(4) 2500kW generators")[1] is None


# ── distillation: 497 rows become a few dozen nodes ──────────────────────────

def test_a_497_line_document_distils_to_a_handful_of_nodes():
    """The token guard. What reaches a prompt has to be small."""
    graph = sg.build(_big_scope(497))
    assert graph.line_count == 497
    assert len(graph.nodes) < 80, f"{len(graph.nodes)} nodes is too many to carry"
    assert graph.classified > 300, "most lines should have been understood"


def test_the_prompt_block_stays_small_however_big_the_document():
    graph = sg.build(_big_scope(497))
    block = graph.context_block()
    assert len(block) < 2500, f"{len(block)} chars would crowd the prompt"
    assert "497" not in block, "the rows themselves must not reach the prompt"


def test_the_document_establishes_which_systems_and_phases_are_real():
    graph = sg.build([
        "Furnish and install (4) generators, Phase 2",
        "Deliver chillers to site, Phase 2",
    ])
    assert set(graph.systems()) == {"generator", "chiller"}
    assert graph.phases() == [2]


def test_commissioning_that_names_a_phase_rather_than_equipment_still_lands():
    """It is the node everything else in the phase points at — requiring a
    system would drop the target and leave every edge with nowhere to go."""
    graph = sg.build(["Phase 2 commissioning of the standby power system"])
    assert any(n.stage == "commission" for n in graph.nodes.values())


# ── the arrow diagram ────────────────────────────────────────────────────────

def _gen_scope():
    return sg.build([
        "Furnish and install (4) 2500kW generators, Phase 2",
        "Terminate generator feeders at MV switchgear, Phase 2",
        "Megger test generator feeders, Phase 2",
        "Phase 2 commissioning of standby power system",
        "Deliver chillers to site, Phase 2",
        "Set chillers on housekeeping pads, Phase 2",
        "Install (6) CRAH units, Phase 3",
    ])


def test_a_systems_stages_run_in_order():
    whys = [w for _, _, w in _gen_scope().edges()]
    assert any("install before connect" in w for w in whys)
    assert any("connect before test" in w for w in whys)


def test_work_feeds_its_own_phases_commissioning():
    whys = [w for _, _, w in _gen_scope().edges()]
    assert any("feeds commissioning in phase 2" in w for w in whys)


# ── the verdicts, on the questions actually asked of it ──────────────────────

def _v(graph, pred, succ, pw="", sw=""):
    return graph.verdict(pred, succ, pw, sw)


def test_generator_terminations_feed_commissioning():
    g = _gen_scope()
    assert _v(g, "Generator Terminations (Gen 318)", "Phase 2 Commissioning Start",
              "Phase 2 / MV", "Phase 2 / Milestones") == "supports"


def test_commissioning_before_the_work_that_feeds_it_is_backwards():
    g = _gen_scope()
    assert _v(g, "Phase 2 Commissioning Start", "Generator Terminations (Gen 318)",
              "Phase 2 / Milestones", "Phase 2 / MV") == "violates"


def test_a_phase_hands_off_to_its_own_commissioning_not_another_phases():
    """The example this was built for: phase 2 generators to phase 2 Cx."""
    g = _gen_scope()
    assert _v(g, "Generator Terminations", "Phase 3 Commissioning Start",
              "Phase 2 / MV", "Phase 3 / Milestones") is None


def test_stage_order_within_one_system_is_enforced_both_ways():
    g = _gen_scope()
    assert _v(g, "Set Generator (Gen 318)", "Generator Terminations",
              "Phase 2", "Phase 2") == "supports"
    assert _v(g, "Generator Terminations", "Set Generator (Gen 318)",
              "Phase 2", "Phase 2") == "violates"


def test_it_says_nothing_about_a_system_the_document_never_covered():
    """The guessing guard. Opining on work nobody contracted would be
    inventing sequence, not reading it."""
    g = _gen_scope()
    assert _v(g, "Install Sprinkler Mains", "Test Sprinkler Mains",
              "Phase 2", "Phase 2") is None


def test_it_says_nothing_when_neither_side_names_a_stage():
    g = _gen_scope()
    assert _v(g, "Mobilisation", "Site Fencing") is None


def test_an_empty_graph_has_no_opinions():
    g = sg.build([])
    assert _v(g, "Set Generator", "Generator Terminations") is None
    assert g.context_block() == ""


# ── it reaches the ranking, in the right place in the pecking order ──────────

def _project():
    p = Project(uid="1", name="DC", id="SCOPE-TEST", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Phase 2", code="P2")]
    p.activities = [
        Activity(uid="a1", activity_id="A1000", name="Set Generator (Gen 318)",
                 wbs_uid="w", calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-02",
                 planned_finish="2026-02-06"),
        Activity(uid="a2", activity_id="A1010", name="Generator Terminations",
                 wbs_uid="w", calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0,
                 remaining_duration=40.0, planned_start="2026-02-09",
                 planned_finish="2026-02-13"),
    ]
    p.relations = []
    p.build_lookups()
    return p


def _score(p, pred_id, succ_id, scope=None, directives=None):
    ctx = _Ctx(p, directives, None, scope)
    a = p.get_activity(activity_id=pred_id)
    b = p.get_activity(activity_id=succ_id)
    return score_tie(ctx, a, b, implied_lag(p, a, b))


def test_the_scope_document_lifts_a_tie_it_supports():
    p = _project()
    plain, _ = _score(p, "A1000", "A1010")
    told, why = _score(p, "A1000", "A1010", scope=_gen_scope())
    assert told >= plain
    assert any("scope" in w for w in why)


def test_a_tie_running_backwards_to_the_scope_is_never_proposed():
    p = _project()
    got, why = _score(p, "A1010", "A1000", scope=_gen_scope())
    assert got == 0.0
    assert any("scope document" in w for w in why)


def test_a_taught_rule_outranks_the_document():
    """A rule came from somebody who walked the job; this came from parsing a
    PDF. Where they disagree the rule wins and the clash is the user's to see."""
    p = _project()
    rule = pb.parse_directive("Set Generator follows Generator Terminations")
    got, why = _score(p, "A1000", "A1010", scope=_gen_scope(), directives=[rule])
    assert got == 0.0
    assert any("contradicts what you said" in w for w in why)


def test_the_document_never_stacks_on_top_of_a_rule_that_already_spoke():
    p = _project()
    rule = pb.parse_directive("Generator Terminations follows Set Generator")
    _, why = _score(p, "A1000", "A1010", scope=_gen_scope(), directives=[rule])
    assert any("you said" in w for w in why)
    assert not any("scope" in w for w in why), "both spoke — that is double counting"


def test_a_project_with_no_scope_document_ranks_exactly_as_before():
    p = _project()
    assert _score(p, "A1000", "A1010", scope=None) == _score(p, "A1000", "A1010")


# ── it persists with the job ─────────────────────────────────────────────────

def test_the_graph_survives_being_saved_and_reloaded():
    b = pb.Brain("k")
    b.scope = _gen_scope()
    back = pb.Brain.from_json(b.to_json())
    assert back.scope is not None
    assert set(back.scope.systems()) == set(b.scope.systems())
    assert back.scope.verdict("Set Generator", "Generator Terminations",
                              "Phase 2", "Phase 2") == "supports"


def test_a_scope_alone_is_worth_saving():
    b = pb.Brain("k")
    b.scope = _gen_scope()
    assert not b.is_empty()


def test_a_corrupt_saved_graph_is_dropped_not_crashed_on():
    back = pb.Brain.from_json({"key": "k", "scope": {"nodes": [
        {"system": "generator", "stage": "not-a-real-stage"}]}})
    assert back.scope is None or not back.scope.nodes


def test_the_agent_is_told_what_the_document_established():
    p = _project()
    b = pb.Brain("k")
    b.scope = _gen_scope()
    block = b.context_block(p)
    assert "SCOPE OF WORK" in block
    assert "generator" in block
    assert "weaker than anything you were told" in block


# ── through the route ────────────────────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _upload(c, lines, name="scope.pdf"):
    return c.post("/api/scope", data={"file": (io.BytesIO(_make_pdf(lines)), name)},
                  content_type="multipart/form-data")


_LINES = ["Furnish and install (4) 2500kW generators, Phase 2",
          "Terminate generator feeders at MV switchgear, Phase 2",
          "Phase 2 commissioning of standby power system"]


def test_uploading_a_scope_document_reports_what_it_found():
    body = _upload(_client(), _LINES).get_json()
    assert body["success"]
    assert body["line_count"] == 3
    assert "generator" in body["systems"] and body["phases"] == [2]


def test_the_upload_changes_no_activity_or_relation():
    c = _client()
    before = [(a.activity_id, a.name) for a in server._projects["t"]["project"].activities]
    _upload(c, _LINES)
    p = server._projects["t"]["project"]
    assert [(a.activity_id, a.name) for a in p.activities] == before
    assert p.relations == []


def test_the_upload_lands_in_the_brain_and_can_be_read_back():
    c = _client()
    _upload(c, _LINES)
    got = c.get("/api/scope").get_json()["scope"]
    assert got and "generator" in got["systems"]


def test_the_agent_is_told_about_the_upload_and_that_nothing_changed():
    c = _client()
    _upload(c, _LINES)
    from interpreter.llm_interpreter import _build_conversation
    seen = _build_conversation(server._projects["t"]["chat_history"])
    assert "Scope of work read" in seen
    assert "Nothing has been changed in the schedule" in seen


def test_a_document_with_no_scope_in_it_is_refused_with_the_count():
    resp = _upload(_client(), ["Quantities are approximate and subject to change",
                               "All prices are lump sum unless noted otherwise"])
    assert resp.status_code == 400
    assert "none of them named a system" in resp.get_json()["error"]


def test_a_non_pdf_is_refused():
    c = _client()
    resp = c.post("/api/scope", data={"file": (io.BytesIO(b"x"), "scope.docx")},
                  content_type="multipart/form-data")
    assert resp.status_code == 400 and "PDF" in resp.get_json()["error"]


def test_nothing_attached_is_refused():
    assert _client().post("/api/scope", data={},
                          content_type="multipart/form-data").status_code == 400


def test_a_scope_can_be_cleared():
    c = _client()
    _upload(c, _LINES)
    c.delete("/api/scope")
    assert c.get("/api/scope").get_json()["scope"] is None


def test_the_uploaded_scope_changes_what_the_ranker_proposes():
    """End to end: upload, then the tie it supports outranks the reverse."""
    c = _client()
    _upload(c, _LINES)
    p = server._projects["t"]["project"]
    graph = server._brain_for(p).scope
    forward, _ = _score(p, "A1000", "A1010", scope=graph)
    backward, _ = _score(p, "A1010", "A1000", scope=graph)
    assert forward > backward
