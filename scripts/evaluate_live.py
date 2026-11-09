"""Evaluate LimeBot on realistic tasks with the real model (opt-in, capped).

    python scripts/evaluate_live.py plan                 # what would run, worst-case spend
    python scripts/evaluate_live.py validate             # offline self-check of the cases
    python scripts/evaluate_live.py run --yes            # spends tokens
    python scripts/evaluate_live.py compare old.json new.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.live_eval import (  # noqa: E402
    DEFAULT_TOTAL_TOKEN_BUDGET,
    LiveCase,
    LivePlan,
    compare_reports,
    evaluate_checks,
    load_live_cases,
    run_live,
    validate_live_case,
    write_fixture_files,
)
from core.task_eval import SourceSnapshot  # noqa: E402

DEFAULT_CASES = ROOT / "evals" / "live"


def _select(cases: tuple[LiveCase, ...], wanted: list[str] | None) -> tuple[LiveCase, ...]:
    if not wanted:
        return cases
    chosen = tuple(case for case in cases if case.id in set(wanted))
    missing = set(wanted) - {case.id for case in chosen}
    if missing:
        raise ValueError(f"Unknown case id(s): {', '.join(sorted(missing))}")
    return chosen


def _load_environment() -> dict[str, str]:
    """The user's own provider settings, read from the checkout's .env."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv is a core dependency
        dotenv_values = None
    merged: dict[str, str] = {}
    env_file = ROOT / ".env"
    if dotenv_values and env_file.is_file():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v})
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


def _worst_case(cases: tuple[LiveCase, ...], repeats: int, budget: int) -> int:
    return min(sum(case.max_tokens for case in cases) * repeats, budget)


def cmd_plan(args: argparse.Namespace) -> int:
    cases = _select(load_live_cases(args.cases.resolve()), args.case)
    for case in cases:
        validate_live_case(case)
    print(f"{len(cases)} case(s) x {args.repeats} repeat(s):")
    for case in cases:
        print(
            f"  {case.id:<28} {case.category:<12} "
            f"<= {case.max_model_calls} calls, <= {case.max_tokens:,} tokens"
        )
    print(f"Worst-case spend: {_worst_case(cases, args.repeats, args.budget):,} tokens "
          f"(run-wide cap {args.budget:,}).")
    print("Nothing was run. Use `run --yes` to spend tokens.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Each case must fail untouched and pass with its reference solution."""
    cases = _select(load_live_cases(args.cases.resolve()), args.case)
    problems = 0
    for case in cases:
        validate_live_case(case)
        with tempfile.TemporaryDirectory(prefix="limebot-live-validate-") as name:
            project = Path(name)
            write_fixture_files(project, case.fixtures)
            write_fixture_files(project, case.hidden_fixtures)
            before = evaluate_checks(case, project, "")
            write_fixture_files(project, case.solution)
            after = evaluate_checks(case, project, " ".join(
                phrase for check in case.reply_checks for phrase in check.any_of[:1]
            ))
        starts_failing = not all(before.values())
        solvable = bool(after) and all(after.values())
        status = "ok" if starts_failing and solvable else "PROBLEM"
        if status != "ok":
            problems += 1
        print(f"  {case.id:<28} fails untouched: {starts_failing!s:<5} solvable: {solvable!s:<5} {status}")
        if not solvable:
            print(f"    still failing with the solution: {[k for k, v in after.items() if not v]}")
    print("All cases are valid." if not problems else f"{problems} case(s) need fixing.")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to spend tokens without --yes. Run `plan` to see the worst case.")
        return 2
    cases = _select(load_live_cases(args.cases.resolve()), args.case)
    env = _load_environment()
    if args.keep_replies:
        env["LIMEBOT_EVAL_KEEP_REPLIES"] = str(args.keep_replies.resolve())
    plan = LivePlan(
        cases=cases,
        snapshot=SourceSnapshot("live", args.snapshot.resolve()),
        repeats=args.repeats,
        model=args.model or "",
        total_token_budget=args.budget,
    )
    print(
        f"Running {len(cases)} case(s) x {args.repeats} with model "
        f"{plan.model or env.get('LLM_MODEL', '?')} "
        f"(cap {args.budget:,} tokens)..."
    )

    def progress(row, spent: int) -> None:
        mark = "PASS" if row.passed else "FAIL"
        extra = " (budget cap hit)" if row.budget_exceeded else ""
        print(
            f"  [{mark}] {row.case_id} #{row.repeat + 1}: {row.status}, "
            f"{row.model_calls} calls, {row.tokens_in + row.tokens_out:,} tokens, "
            f"{row.wall_time_ms / 1000:.0f}s{extra}   (total {spent:,})"
        )

    report = run_live(plan, env, progress=progress)
    summary = report["summary"]
    print(
        f"\nPassed {summary['passed']}/{summary['runs']} "
        f"({summary['pass_rate']:.0%}); false successes: {summary['false_successes']}; "
        f"avg calls: {summary['avg_model_calls']}; tokens spent: {report['tokens_spent']:,}"
    )
    print(f"Report: {report['report_path']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    result = compare_reports(before, after)
    print(json.dumps(result, indent=2))
    return 1 if args.fail_on_regression and result["regressions"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
        p.add_argument("--case", action="append", help="run only this case id (repeatable)")
        p.add_argument("--repeats", type=int, default=1)
        p.add_argument("--budget", type=int, default=DEFAULT_TOTAL_TOKEN_BUDGET,
                       help="run-wide token cap")

    plan = sub.add_parser("plan", help="show cases and the worst-case spend")
    common(plan)
    plan.set_defaults(func=cmd_plan)

    validate = sub.add_parser("validate", help="offline check that every case is solvable and not trivial")
    common(validate)
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="run the cases against the real model")
    common(run)
    run.add_argument("--snapshot", type=Path, default=ROOT, help="LimeBot checkout to test")
    run.add_argument("--model", help="override LLM_MODEL for this run")
    run.add_argument("--yes", action="store_true", help="confirm that tokens will be spent")
    run.add_argument("--keep-replies", type=Path, metavar="DIR",
                     help="also save each reply as a local text file, to see why a run failed")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser("compare", help="diff two saved live reports")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--fail-on-regression", action="store_true")
    compare.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
