#!/usr/bin/env python3
"""CLI wrapper for LimeBot's constrained review-only diff entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.review_entrypoint import (  # noqa: E402
    DEFAULT_MAX_DIFF_BYTES,
    DEFAULT_MAX_INPUT_BYTES,
    read_diff_input,
    run_review,
    write_review_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a unified diff and produce a redacted review artifact."
    )
    parser.add_argument(
        "--diff-file",
        help="Unified diff path. Omit to read stdin; no repository files are discovered.",
    )
    parser.add_argument("--output", default="limebot-review.json")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--max-diff-bytes", type=int, default=DEFAULT_MAX_DIFF_BYTES)
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--invoke-model", action="store_true")
    parser.add_argument("--model", help="Optional LimeBot model override")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    try:
        diff_text, input_truncated = read_diff_input(
            args.diff_file, max_input_bytes=args.max_input_bytes
        )
        artifact = await run_review(
            diff_text,
            max_diff_bytes=args.max_diff_bytes,
            input_truncated=input_truncated,
            invoke_model=args.invoke_model,
            model=args.model,
        )
        write_review_artifact(artifact, args.output, args.format)
    except Exception as exc:
        print(f"review-diff failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Review artifact written to {args.output} "
        f"({artifact['summary']['files']} files, mode={artifact['mode']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
