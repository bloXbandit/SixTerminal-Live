# Prompt evals

The system prompt is ~13k tokens of instruction, and every model call in
`tests/` is mocked. That is correct for the test suite — it makes it fast,
free and deterministic — but it means a prompt edit could not be evaluated at
all. You changed a paragraph, everything stayed green, and you found out in
production.

This closes that. It is the only part of the repo that calls a real model.

## Why this can be scored by machine

Most LLM evals need a human or a judge model, because the output is prose.
This agent's output is **structured commands against a schedule we already
have**, so the important claims are decidable by looking:

- did it reference an id that exists?
- does the tie run the way the dates say it must?
- did it edit at all, when the ask was a question?
- did it reach for one bulk rule, or emit twelve edits?
- did "the list above" resolve to the activities actually named earlier?

Only a handful of cases fall back to matching the chat message, and those are
treated as the weaker evidence they are.

## Running it

```bash
export ANTHROPIC_API_KEY=...          # or OPENAI_API_KEY

python -m evals.run --list                       # what exists, and the claim each defends
python -m evals.run --repeats 5                  # the whole suite
python -m evals.run --category direction         # one area, while iterating
python -m evals.run --case same-thing-for-the-list-above --repeats 10
```

Every case runs several times and is scored as a **pass rate**. The model is
stochastic; one sample cannot tell a regression from the dice landing
differently. A case is failing below 80%.

## The workflow a prompt change should use

```bash
python -m evals.run --repeats 5 --save-baseline before   # BEFORE editing
# ... edit SYSTEM_PROMPT ...
python -m evals.run --repeats 5 --compare before
```

`--compare` reports regressions and improvements against the stored run, with
a margin so ordinary variance is not called damage. It also names any case
that is in the baseline and is no longer being run — otherwise deleting an
awkward case would look like the average improving.

Exit code is non-zero on a regression, so this can gate a release.

## Cost

16 cases × 5 repeats = 80 calls, on schedules of 4–12 activities. The
fixtures are deliberately tiny: a 2,776-activity file would test the context
builder's summarising rather than the agent's judgement, and cost far more.

## Adding a case

A case exists because the prompt claims something. Put the claim in the
`claim` field — a failure should point at the paragraph to go and fix, not at
a vague sense that things got worse.

```python
_case(
    id="direction-right-on-a-plain-tie",
    category="direction",
    claim="THE PREDECESSOR HAPPENS FIRST",
    fixture="linked",
    instruction="Link Terminate Feeders and Megger Test.",
    checks=[emits("add_relation"), direction_sane(), relation("D1020", "D1030")],
)
```

The useful test of a new case: **delete the prompt paragraph it defends and it
should fail.** If it still passes, that paragraph is decoration, and the eval
has told you something worth knowing about the prompt.

`_case()` adds `ids_exist()` and `answers_at_all()` to every case — an
invented id or an empty turn is a failure of any request, whatever else the
case is testing.

## The harness is tested

`tests/test_eval_harness.py` runs in the normal suite, costs nothing, and
exercises every check twice: once against the answer it should accept, once
against the specific wrong answer it exists to catch. A harness whose checks
cannot fail is worse than no harness — it hands you confidence you have not
earned, which is the exact problem this was built to solve.

It also verifies that every id a case asserts on really exists in that case's
fixture. A check written against a typo'd id can never pass, and would read
as the agent failing forever.
