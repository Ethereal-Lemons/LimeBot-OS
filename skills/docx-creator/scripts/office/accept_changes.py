"""Accept tracked insertions and deletions in a DOCX package."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", WORD_NS)
W = f"{{{WORD_NS}}}"


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Archive member escapes output directory: {member.filename}")
        archive.extract(member, destination)


def _accept(parent: ET.Element) -> None:
    for child in list(parent):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name == "del":
            parent.remove(child)
            continue
        if local_name == "ins":
            index = list(parent).index(child)
            parent.remove(child)
            for inserted in list(child):
                parent.insert(index, inserted)
                index += 1
            continue
        _accept(child)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_docx", type=Path)
    parser.add_argument("output_docx", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output_docx.exists() and not args.force:
        parser.error(f"Output file already exists: {args.output_docx} (use --force)")
    with tempfile.TemporaryDirectory(prefix="limebot-docx-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(args.input_docx) as archive:
            _safe_extract(archive, root)
        for xml_path in (root / "word").glob("*.xml"):
            tree = ET.parse(xml_path)
            _accept(tree.getroot())
            tree.write(xml_path, encoding="utf-8", xml_declaration=True)
        args.output_docx.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output_docx, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(path for path in root.rglob("*") if path.is_file()):
                archive.write(path, path.relative_to(root).as_posix())
    print(f"Accepted tracked changes: {args.output_docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
