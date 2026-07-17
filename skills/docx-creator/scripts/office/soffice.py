"""Run LibreOffice headlessly with a clear missing-dependency error."""

from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> int:
    arguments = sys.argv[1:]
    if not arguments or arguments == ["--help"]:
        print("Usage: soffice.py [LibreOffice arguments...]")
        return 0 if arguments == ["--help"] else 2
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        print(
            "LibreOffice is not installed or is not on PATH. "
            "Install it before converting or rendering DOCX files.",
            file=sys.stderr,
        )
        return 1
    command = [executable, *arguments]
    if "--headless" not in command:
        command.insert(1, "--headless")
    completed = subprocess.run(command, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
