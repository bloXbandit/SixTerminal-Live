# Compost proposals — week ending 2026-07-20

Collected from STATE.md, trust.tsv, goal-ledger.tsv, dispatch.tsv (no `gh` available, so PR data not checked).
No `FAILED:` lines in STATE.md, no fails/demotions in trust.tsv, no FAIL rows in goal-ledger.tsv.
Two process incidents surfaced in dispatch.tsv worth composting. APPLY NOTHING — awaiting signature.

## Proposal 1 — skill fix: quorum voter watchdog stall repeated identically

Incidents (dispatch.tsv):
- 2026-07-14: "1 voter stalled twice on infra"
- 2026-07-15: "all 3 voters stalled on infra (600s no-progress watchdog), same recurring stall as 2026-07-14"

Same failure mode (600s no-progress watchdog) hit on consecutive days, the second time taking out all 3 voters instead of 1. This is the "same failure repeated" bar the compost skill requires before proposing a skill fix. Suggested fix: investigate root cause of the voter infra stall (resource contention? network stall?) rather than just re-running past the watchdog; consider whether the timeout itself needs tuning.

## Proposal 2 — missing standing goal: silent heartbeat-loop stoppage

Incident:
- STATE.md and dispatch.tsv both stop logging ticks after 2026-07-16.
- goal-ledger.tsv shows goal checks continuing to run and PASS every day through 2026-07-20 (`2026-07-17T07:38`, `2026-07-18T07:30`, `2026-07-19T07:31`, `2026-07-20T07:35`).

The heartbeat/quorum loop went silent for 4 days while a separate goal-check cron kept running, so nothing caught the gap — no FAILED line was ever written because nothing ran to write one. A shell predicate could catch this: "most recent STATE.md tick timestamp is within N hours of now." Propose adding this as a standing goal so a stalled heartbeat loop is itself detected rather than silently absent.
