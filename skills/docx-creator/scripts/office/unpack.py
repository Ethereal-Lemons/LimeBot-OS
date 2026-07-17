"""Safely unpack a DOCX package for XML editing."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import zipfile


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Archive member escapes output directory: {member.filename}")
        archive.extract(member, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_docx", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--merge-runs", action="store_true", help="Accepted for compatibility; runs are preserved.")
    args = parser.parse_args()

    if not zipfile.is_zipfile(args.input_docx):
        parser.error(f"Not a valid DOCX/ZIP archive: {args.input_docx}")
    if args.output_dir.exists():
        if not args.force:
            parser.error(f"Output directory already exists: {args.output_dir} (use --force)")
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True)
    with zipfile.ZipFile(args.input_docx) as archive:
        _safe_extract(archive, args.output_dir)
    print(f"Unpacked {args.input_docx} to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
