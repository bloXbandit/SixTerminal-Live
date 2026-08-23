"""
test_eval_harness.py — the eval harness is code, so it gets tested like code.

A harness whose checks are wrong is worse than no harness: it hands you
confidence you have not earned, and you only find out in production, which is
the exact problem it was built to solve.

So every check is exercised twice — once against the answer it should accept,
once against the specific wrong answer it exists to catch. If a check cannot
fail, it is not testing anything, and a green eval run built on it means
nothing.

None of this costs an API call. The cases are meaningless against a fake model
— that is what `python -m evals.run` is for — but the SCORING, the checks and
the baseline comparison are all deterministic, and those are what a prompt
change is judged with.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals import cases as case_mod
from evals import checks as ck
from evals import fixtures
from evals.harness import (PASS_BAR, REGRESSION_MARGIN, compare, run_once,
                           run_suite, score)


def _fake(commands):
    """A model that always answers with these commands."""
    return lambda *a, **k: (list(commands), "")


def _chat(msg):
    return {"action": "chat", "message": msg}


def _tie(pred, succ):
    return {"action": "add_relation", "predecessor_id": pred,
            "successor_id": succ, "type": "fs"}


def _run(check, project, commands, chat=""):
    return check(project, commands, chat)


# ── every check accepts the right answer and rejects the wrong one ───────────

def test_ids_exist_passes_on_real_ids_and_fails_on_an_invented_one():
    p = fixtures.linked()
    ok, _ = _run(ck.ids_exist(), p, [_tie("D1000", "D1010")])
    assert ok
    bad, detail = _run(ck.ids_exist(), p, [_tie("D1000", "D9999")])
    assert not bad and "D9999" in detail


def test_direction_sane_catches_a_backwards_tie():
    p = fixtures.linked()
    assert _run(ck.direction_sane(), p, [_tie("D1000", "D1010")])[0]
    bad, detail = _run(ck.direction_sane(), p, [_tie("D1030", "D1000")])
    assert not bad and "backwards" in detail


def test_direction_sane_ignores_ties_it_cannot_judge():
    """An id it cannot resolve is ids_exist()'s problem, not this check's."""
    p = fixtures.linked()
    assert _run(ck.direction_sane(), p, [_tie("NOPE", "ALSO-NOPE")])[0]


def test_relations_within_same_folder_catches_a_cross_room_tie():
    p = fixtures.rooms()
    assert _run(ck.relations_within_same_folder(), p, [_tie("A1010", "A1020")])[0]
    bad, detail = _run(ck.relations_within_same_folder(), p, [_tie("A1010", "A1040")])
    assert not bad and "across folders" in detail


def test_edits_nothing_catches_an_answer_that_edited():
    p = fixtures.rooms()
    assert _run(ck.edits_nothing(), p, [_chat("42 of them.")])[0]
    assert not _run(ck.edits_nothing(), p, [_chat("ok"), _tie("A1010", "A1020")])[0]


def test_emits_and_no_action_are_opposites():
    p = fixtures.rooms()
    cmds = [_tie("A1010", "A1020")]
    assert _run(ck.emits("add_relation"), p, cmds)[0]
    assert not _run(ck.no_action("add_relation"), p, cmds)[0]
    assert _run(ck.no_action("update_duration"), p, cmds)[0]


def test_only_actions_catches_an_extra_edit_that_slipped_in():
    p = fixtures.rooms()
    cmds = [_tie("A1010", "A1020"),
            {"action": "delete_activity", "activity_id": "A1020"}]
    bad, detail = _run(ck.only_actions("add_relation"), p, cmds)
    assert not bad and "delete_activity" in detail


def test_command_count_at_most_catches_twelve_edits_where_one_rule_would_do():
    p = fixtures.crews()
    many = [{"action": "update_duration", "activity_id": f"C{3000 + i * 10}",
             "new_duration_days": 3} for i in range(12)]
    bad, detail = _run(ck.command_count_at_most(3), p, many)
    assert not bad and "12" in detail
    one = [{"action": "bulk_rules", "rules": []}]
    assert _run(ck.command_count_at_most(3), p, one)[0]


def test_command_count_ignores_the_chat_message():
    """Narration is not an edit and must not count against the budget."""
    p = fixtures.crews()
    assert _run(ck.command_count_at_most(1), p,
                [_chat("Done."), {"action": "bulk_rules", "rules": []}])[0]


def test_touches_only_catches_the_wrong_list():
    """The follow-up failure that is otherwise invisible: every command looks
    fine, they are just aimed at the wrong activities."""
    p = fixtures.rooms()
    right = [{"action": "update_duration", "activity_id": "A1010", "new_duration_days": 6},
             {"action": "update_duration", "activity_id": "A1030", "new_duration_days": 6}]
    assert _run(ck.touches_only("A1010", "A1030"), p, right)[0]
    wrong = right + [{"action": "update_duration", "activity_id": "A1050",
                      "new_duration_days": 6}]
    bad, detail = _run(ck.touches_only("A1010", "A1030"), p, wrong)
    assert not bad and "A1050" in detail


def test_touches_only_catches_a_missing_one():
    p = fixtures.rooms()
    bad, detail = _run(ck.touches_only("A1010", "A1030"), p,
                       [{"action": "update_duration", "activity_id": "A1010",
                         "new_duration_days": 6}])
    assert not bad and "A1030" in detail


def test_relation_checks_a_specific_tie():
    p = fixtures.linked()
    assert _run(ck.relation("D1020", "D1030"), p, [_tie("D1020", "D1030")])[0]
    assert not _run(ck.relation("D1020", "D1030"), p, [_tie("D1000", "D1030")])[0]
    assert _run(ck.no_relation("D1020", "D1010"), p, [_tie("D1020", "D1030")])[0]


def test_field_equals_catches_a_wrong_conversion():
    """Two weeks is ten working days, not fourteen."""
    p = fixtures.linked()
    good = [{"action": "update_duration", "activity_id": "D1030", "new_duration_days": 10}]
    bad = [{"action": "update_duration", "activity_id": "D1030", "new_duration_days": 14}]
    assert _run(ck.field_equals("update_duration", "new_duration_days", 10), p, good)[0]
    assert not _run(ck.field_equals("update_duration", "new_duration_days", 10), p, bad)[0]


def test_field_equals_fails_when_the_action_never_happened():
    p = fixtures.linked()
    ok, detail = _run(ck.field_equals("update_duration", "new_duration_days", 10),
                      p, [_chat("sure")])
    assert not ok and "no update_duration" in detail


def test_asks_a_question_reads_the_clarify_action():
    p = fixtures.rooms()
    asked = [{"action": "clarify", "question": "which room?"}]
    assert _run(ck.asks_a_question(True), p, asked)[0]
    assert not _run(ck.asks_a_question(False), p, asked)[0]
    assert _run(ck.asks_a_question(False), p, [_tie("A1010", "A1020")])[0]


def test_wbs_names_exist_catches_an_invented_folder():
    p = fixtures.rooms()
    good = [{"action": "add_activity", "wbs_name": "MV 105", "name": "x"}]
    bad = [{"action": "add_activity", "wbs_name": "MV 999", "name": "x"}]
    assert _run(ck.wbs_names_exist(), p, good)[0]
    assert not _run(ck.wbs_names_exist(), p, bad)[0]


def test_says_and_not_says_read_the_chat_message():
    p = fixtures.rooms()
    assert _run(ck.says("attach"), p, [], "I don't have it — attach it")[0]
    assert not _run(ck.not_says("attach"), p, [], "I don't have it — attach it")[0]


def test_mentions_an_id_wants_a_real_one():
    p = fixtures.linked()
    assert _run(ck.mentions_an_id(), p, [], "D1020 drives it")[0]
    assert not _run(ck.mentions_an_id(), p, [], "the terminations activity drives it")[0]


def test_answers_at_all_catches_an_empty_turn():
    """Without this, a model returning nothing passes every 'did not do X'
    check vacuously and the case scores green."""
    p = fixtures.rooms()
    assert not _run(ck.answers_at_all(), p, [], "")[0]
    assert _run(ck.answers_at_all(), p, [], "here you go")[0]
    assert _run(ck.answers_at_all(), p, [_tie("A1010", "A1020")], "")[0]


def test_a_check_that_raises_is_a_failure_not_a_crash():
    """One broken check must not take down a whole run."""
    def boom(project, commands, chat):
        raise RuntimeError("bad check")
    case = case_mod.Case(id="x", category="c", claim="q", fixture="rooms",
                         instruction="do it", checks=[boom])
    r = run_once(case, "claude", None, call=_fake([_chat("ok")]))
    assert not r.passed and "raised" in r.checks[0].detail


# ── the cases themselves are wired to their fixtures ─────────────────────────

def test_every_case_names_a_fixture_that_exists():
    for c in case_mod.CASES:
        assert c.fixture in fixtures.ALL, c.id


def test_every_case_fixture_builds():
    for name, build in fixtures.ALL.items():
        p = build()
        assert p.activities, name
        ids = [a.activity_id for a in p.activities]
        assert len(ids) == len(set(ids)), f"{name} has duplicate ids"


def test_every_id_a_case_asserts_on_really_exists():
    """
    A check written against a typo'd id can never pass, and would read as the
    agent failing forever. The ids inside a case's CHECKS must be real, even
    though an instruction may deliberately name one that is not.
    """
    import re
    for c in case_mod.CASES:
        real = {a.activity_id for a in c.project().activities}
        for check in c.checks:
            for token in re.findall(r"[A-Z][A-Z0-9.]{3,}", getattr(check, "__name__", "")):
                if token in ("PASS", "FAIL"):
                    continue
                assert token in real, f"{c.id}: check names unknown id {token}"


def test_case_ids_are_unique():
    ids = [c.id for c in case_mod.CASES]
    assert len(ids) == len(set(ids))


def test_selecting_by_category_and_id_works():
    assert case_mod.select(categories=["direction"])
    assert len(case_mod.select(ids=["direction-right-on-a-plain-tie"])) == 1
    assert case_mod.select(ids=["nope"]) == []


# ── scoring ──────────────────────────────────────────────────────────────────

def _case_expecting_a_tie():
    return case_mod.Case(
        id="t", category="c", claim="q", fixture="linked",
        instruction="tie them",
        checks=[ck.emits("add_relation"), ck.ids_exist()])


def test_a_case_is_scored_as_a_rate_not_a_coin_flip():
    """One sample cannot tell a regression from the dice landing differently."""
    case = _case_expecting_a_tie()
    good = run_once(case, "m", None, call=_fake([_tie("D1000", "D1010")]))
    bad = run_once(case, "m", None, call=_fake([_chat("no")]))
    s = score(case, [good, good, bad, good])
    assert s.runs == 4 and s.passes == 3 and s.rate == 0.75


def test_a_failing_check_is_counted_by_name_so_you_know_what_broke():
    case = _case_expecting_a_tie()
    bad = run_once(case, "m", None, call=_fake([_chat("no")]))
    s = score(case, [bad, bad])
    assert s.failing_checks["emits(add_relation)"] == 2
    assert s.sample_failure


def test_the_pass_bar_is_a_rate_not_perfection():
    case = _case_expecting_a_tie()
    good = run_once(case, "m", None, call=_fake([_tie("D1000", "D1010")]))
    bad = run_once(case, "m", None, call=_fake([_chat("no")]))
    assert score(case, [good] * 4 + [bad]).ok          # 0.8
    assert not score(case, [good] * 3 + [bad] * 2).ok  # 0.6


def test_a_model_error_is_recorded_not_swallowed():
    def boom(*a, **k):
        raise RuntimeError("no api key")
    case = _case_expecting_a_tie()
    r = run_once(case, "m", None, call=boom)
    assert not r.passed and "no api key" in r.error
    s = score(case, [r])
    assert s.errors == 1 and s.failing_checks["<error>"] == 1


def test_a_suite_run_reports_every_case_and_a_mean():
    suite = run_suite([_case_expecting_a_tie()], "m", None, repeats=2,
                      call=_fake([_tie("D1000", "D1010")]))
    assert suite["total"] == 1 and suite["passing"] == 1
    assert suite["overall"] == 1.0
    assert len(suite["transcripts"]) == 2


def test_transcripts_carry_what_was_actually_said():
    """A failure you cannot read is a failure you cannot fix."""
    suite = run_suite([_case_expecting_a_tie()], "m", None, repeats=1,
                      call=_fake([_chat("I'd rather not"), _tie("D1000", "D1010")]))
    t = suite["transcripts"][0]
    assert t["chat"] == "I'd rather not" and t["commands"]


# ── comparing runs ───────────────────────────────────────────────────────────

def _report(*rates):
    return {"overall": sum(rates) / len(rates), "cases": [
        {"case_id": f"c{i}", "claim": "q", "category": "x", "rate": r,
         "failing_checks": {}, "sample_failure": ""}
        for i, r in enumerate(rates)]}


def test_a_real_drop_is_called_a_regression():
    diff = compare(_report(0.2), _report(1.0))
    assert len(diff["regressions"]) == 1
    assert diff["regressions"][0]["was"] == 1.0


def test_noise_is_not_called_a_regression():
    """With five repeats one flipped run is 0.2 — that must not read as damage
    or every run cries wolf and the harness gets ignored."""
    assert compare(_report(0.8), _report(1.0))["regressions"] == []


def test_an_improvement_is_reported_too():
    diff = compare(_report(1.0), _report(0.2))
    assert len(diff["improvements"]) == 1


def test_a_case_that_stopped_being_run_is_called_out():
    """Otherwise deleting an awkward case looks like the average improving."""
    diff = compare(_report(1.0), {"overall": 0.5, "cases": [
        {"case_id": "c0", "claim": "q", "rate": 1.0},
        {"case_id": "gone", "claim": "q", "rate": 0.0}]})
    assert diff["dropped_cases"] == ["gone"]


def test_a_brand_new_case_is_not_a_regression():
    diff = compare(_report(0.0), {"overall": 1.0, "cases": []})
    assert diff["regressions"] == [] and diff["new_cases"] == ["c0"]


def test_saving_and_loading_a_baseline_round_trips(tmp_path, monkeypatch):
    import evals.harness as h
    monkeypatch.setattr(h, "BASELINE_DIR", str(tmp_path))
    report = run_suite([_case_expecting_a_tie()], "m", None, repeats=1,
                       call=_fake([_tie("D1000", "D1010")]))
    h.save_baseline(report, "before")
    back = h.load_baseline("before")
    assert back["overall"] == report["overall"]
    assert "transcripts" not in back, "baselines must stay diffable"


def test_a_missing_baseline_is_none_not_a_crash(tmp_path, monkeypatch):
    import evals.harness as h
    monkeypatch.setattr(h, "BASELINE_DIR", str(tmp_path))
    assert h.load_baseline("never-saved") is None
