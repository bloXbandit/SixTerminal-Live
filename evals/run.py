"""
run.py — the command you run after editing the prompt.

    python -m evals.run --list
    python -m evals.run --repeats 5
    python -m evals.run --category direction --repeats 3
    python -m evals.run --save-baseline before-my-change
    python -m evals.run --compare before-my-change

Costs real API calls. Nothing here runs under pytest — the harness's own
tests use a fake model and live in tests/test_eval_harness.py.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals import cases as case_mod
from evals import keys as key_mod
from evals.harness import (PASS_BAR, compare, load_baseline, run_suite,
                           save_baseline)

_GREEN, _RED, _DIM, _OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _c(text, colour, use):
    return f"{colour}{text}{_OFF}" if use else text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("EVAL_MODEL", "claude"))
    ap.add_argument("--api-key", default=None,
                    help="defaults to ANTHROPIC_API_KEY / OPENAI_API_KEY")
    ap.add_argument("--repeats", type=int, default=5,
                    help="runs per case; a rate, not a coin flip (default 5)")
    ap.add_argument("--case", action="append", dest="ids")
    ap.add_argument("--category", action="append", dest="categories")
    ap.add_argument("--list", action="store_true", help="show the cases and exit")
    ap.add_argument("--save-baseline", metavar="NAME")
    ap.add_argument("--compare", metavar="NAME")
    ap.add_argument("--json", metavar="PATH", help="write the full report here")
    ap.add_argument("--no-colour", action="store_true")
    ap.add_argument("--check-key", action="store_true",
                    help="say whether a key can be found, and stop")
    args = ap.parse_args(argv)
    colour = not args.no_colour and sys.stdout.isatty()

    if args.check_key:
        st = key_mod.status()          # read the sources BEFORE loading them
        print(f"key file: {st['env_file']}"
              f"{'' if st['env_file_exists'] else '  (not present)'}")
        for provider, info in st["providers"].items():
            where = f" from {info['source']}, ends …{info['ends_with']}" if info["set"] else ""
            print(f"  {provider:<10} {'found' if info['set'] else 'not found'}{where}")
        if not any(i["set"] for i in st["providers"].values()):
            print("\nAdd one line to .env.local (gitignored):\n"
                  "  ANTHROPIC_API_KEY=sk-ant-...\n"
                  "or export it in your shell.")
            return 2
        return 0

    key_mod.load_into_env()

    picked = case_mod.select(args.ids, args.categories)
    if not picked:
        print("No cases matched.")
        return 2

    if args.list:
        for c in picked:
            print(f"{c.id:<44} {c.category:<16} {c.claim}")
        print(f"\n{len(picked)} cases in {len(set(c.category for c in picked))} categories")
        return 0

    api_key = key_mod.resolve(args.model, args.api_key)
    if not api_key:
        print(f"No API key for '{args.model}'. Run --check-key to see what is "
              f"found, or put one line in .env.local:\n"
              f"  ANTHROPIC_API_KEY=sk-ant-...")
        return 2
    args.api_key = api_key

    total_calls = len(picked) * args.repeats
    print(f"{len(picked)} cases x {args.repeats} repeats = {total_calls} model calls "
          f"({args.model})\n")

    def progress(s):
        mark = _c("PASS", _GREEN, colour) if s.ok else _c("FAIL", _RED, colour)
        line = f"  {mark}  {s.case_id:<44} {s.passes}/{s.runs}"
        if not s.ok and s.sample_failure:
            line += _c(f"   {s.sample_failure[:80]}", _DIM, colour)
        print(line, flush=True)

    report = run_suite(picked, args.model, args.api_key,
                       repeats=args.repeats, on_progress=progress)

    print(f"\n{report['passing']}/{report['total']} cases at or above "
          f"{PASS_BAR:.0%}, mean rate {report['overall']:.0%}")

    by_cat = {}
    for c in report["cases"]:
        by_cat.setdefault(c["category"], []).append(c["rate"])
    print("\nby category:")
    for cat in sorted(by_cat):
        rates = by_cat[cat]
        print(f"  {cat:<18} {sum(rates)/len(rates):.0%}  ({len(rates)} cases)")

    failing = [c for c in report["cases"] if c["rate"] < PASS_BAR]
    if failing:
        print("\nwhat is failing, and the instruction it belongs to:")
        for c in sorted(failing, key=lambda x: x["rate"]):
            print(f"\n  {_c(c['case_id'], _RED, colour)}  {c['passes']}/{c['runs']}")
            print(f"    claim: {c['claim']}")
            for name, n in sorted(c["failing_checks"].items(), key=lambda kv: -kv[1]):
                print(f"    {name} failed {n}x")
            if c["sample_failure"]:
                print(f"    e.g. {c['sample_failure'][:160]}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nfull report (with transcripts) -> {args.json}")

    exit_code = 0

    if args.compare:
        base = load_baseline(args.compare)
        if base is None:
            print(f"\nNo baseline called '{args.compare}'.")
            return 2
        diff = compare(report, base)
        print(f"\nagainst baseline '{args.compare}': "
              f"{diff['overall_was']:.0%} -> {diff['overall_now']:.0%} "
              f"({diff['overall_delta']:+.0%})")
        if diff["dropped_cases"]:
            print(_c(f"  {len(diff['dropped_cases'])} cases in the baseline are no "
                     f"longer run: {', '.join(diff['dropped_cases'])}", _RED, colour))
            exit_code = 1
        for r in diff["regressions"]:
            print(_c(f"  REGRESSED {r['case_id']}: {r['was']:.0%} -> {r['now']:.0%}",
                     _RED, colour))
            print(f"    claim: {r['claim']}")
            if r["sample"]:
                print(f"    e.g. {r['sample'][:160]}")
        for r in diff["improvements"]:
            print(_c(f"  improved  {r['case_id']}: {r['was']:.0%} -> {r['now']:.0%}",
                     _GREEN, colour))
        if diff["regressions"]:
            exit_code = 1
        elif not diff["improvements"] and not diff["dropped_cases"]:
            print("  no change beyond noise")

    if args.save_baseline:
        path = save_baseline(report, args.save_baseline)
        print(f"\nbaseline saved -> {path}")

    if failing and not args.compare:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
