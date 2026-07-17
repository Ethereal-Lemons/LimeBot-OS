"""Validate the structure and XML well-formedness of a DOCX package."""

from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


REQUIRED_PARTS = {"[Content_Types].xml", "word/document.xml"}


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    if not zipfile.is_zipfile(path):
        return ["file is not a ZIP/DOCX archive"]
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        errors.extend(f"missing required part: {part}" for part in REQUIRED_PARTS - names)
        for name in names:
            if not name.lower().endswith(".xml") and not name.lower().endswith(".rels"):
                continue
            try:
                ET.fromstring(archive.read(name))
            except (ET.ParseError, KeyError) as exc:
                errors.append(f"{name}: invalid XML ({exc})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    args = parser.parse_args()
    errors = validate(args.docx)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Valid DOCX package: {args.docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
