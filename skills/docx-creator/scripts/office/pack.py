"""Repack an edited DOCX directory and validate it first."""

from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from validate import validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_docx", type=Path)
    parser.add_argument("--original", type=Path, help="Retained for command compatibility.")
    parser.add_argument("--validate", dest="validate_output", default="true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = args.input_dir.resolve()
    if not root.is_dir():
        parser.error(f"Input directory does not exist: {root}")
    if args.output_docx.exists() and not args.force:
        parser.error(f"Output file already exists: {args.output_docx} (use --force)")

    required = {"[Content_Types].xml", "word/document.xml"}
    files = [path for path in root.rglob("*") if path.is_file()]
    relative_names = {path.relative_to(root).as_posix() for path in files}
    missing = required - relative_names
    if missing:
        parser.error("Missing required package parts: " + ", ".join(sorted(missing)))

    for path in files:
        if path.suffix.lower() in {".xml", ".rels"}:
            try:
                ET.parse(path)
            except ET.ParseError as exc:
                parser.error(f"Invalid XML in {path}: {exc}")

    args.output_docx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output_docx, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root).as_posix())

    if str(args.validate_output).lower() not in {"0", "false", "no", "off"}:
        errors = validate(args.output_docx)
        if errors:
            args.output_docx.unlink(missing_ok=True)
            for error in errors:
                print(f"ERROR: {error}")
            return 1
    print(f"Packed DOCX: {args.output_docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
