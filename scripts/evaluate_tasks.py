"""Run the built-in offline task suite against two LimeBot source snapshots."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.task_eval import EvaluationPlan, SourceSnapshot, evaluate, task_case_from_dict


def _cases_from_directory(path: Path):
    if not path.is_dir():
        raise ValueError("The cases path must be a directory")
    files = sorted(path.glob("*.json"))
    if not files:
        raise ValueError("No JSON evaluation cases were found")
    cases = []
    for item in files:
        value = json.loads(item.read_text(encoding="utf-8"))
        cases.append(task_case_from_dict(value))
    return tuple(cases)


async def _run(args: argparse.Namespace) -> int:
    plan = EvaluationPlan(
        cases=_cases_from_directory(args.cases.resolve()),
        baseline=SourceSnapshot(args.baseline_id, args.baseline.resolve()),
        candidate=SourceSnapshot(args.candidate_id, args.candidate.resolve()),
        repeats=args.repeats,
        timeout_seconds=args.timeout,
    )
    report = await evaluate(plan)
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "report_path": report.report_path,
                "recommendation": report.recommendation,
                "reason_codes": report.reason_codes,
                "aggregate": report.aggregate,
                "runs": [
                    {
                        "case_id": row.case_id,
                        "variant_id": row.variant_id,
                        "repeat": row.repeat,
                        "status": row.status,
                        "verification": row.verification,
                        "assertions": row.assertions,
                        "tool_names": row.tool_names,
                        "tool_failures": row.tool_failures,
                        "failure_categories": row.failure_categories,
                        "model_calls": row.model_calls,
                        "model_time_ms": row.model_time_ms,
                        "tool_calls": row.tool_calls,
                        "tool_time_ms": row.tool_time_ms,
                        "wall_time_ms": row.wall_time_ms,
                    }
                    for row in report.runs
                ],
            },
            indent=2,
        )
    )
    return 1 if args.fail_on_regression and report.recommendation == "keep_baseline" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    compare = subcommands.add_parser("compare", help="compare two code snapshots offline")
    compare.add_argument("--cases", type=Path, default=ROOT / "evals" / "cases")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--baseline-id", default="baseline")
    compare.add_argument("--candidate-id", default="candidate")
    compare.add_argument("--repeats", type=int, default=1)
    compare.add_argument("--timeout", type=int, default=90)
    compare.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
