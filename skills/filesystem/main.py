"""
Filesystem Skill - Safe file operations within ALLOWED_PATHS.

Usage:
    python main.py roots
    python main.py list   <path>
    python main.py read   <path>
    python main.py write  <path> <content>
    python main.py append <path> <content>
    python main.py mkdir  <path>
    python main.py delete <path>
    python main.py rename <source> <destination>
    python main.py copy   <source> <destination>
    python main.py stat   <path>
    python main.py find   <path> <pattern>
"""

import json

import shutil
import sys
from datetime import datetime
from pathlib import Path


def _find_env_file() -> Path | None:
    """Locate .env by checking CWD first, then walking up from this script."""
    cwd_env = Path(".env")
    if cwd_env.exists():
        return cwd_env

    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    return None


def load_allowed_paths() -> list[Path]:
    """Load and resolve ALLOWED_PATHS from the nearest .env file."""
    env_path = _find_env_file()
    if not env_path:
        _err("Could not find .env file for configuration.")
        return []

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ALLOWED_PATHS="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                paths = []
                for p in raw.split(","):
                    p = p.strip()
                    if p:
                        resolved = Path(p).resolve()
                        if resolved.exists():
                            paths.append(resolved)
                        else:
                            _err(
                                f"Warning: ALLOWED_PATHS entry does not exist and will be skipped: {p}"
                            )
                return paths
    except Exception as e:
        _err(f"Failed to load config: {e}")

    return []


ALLOWED_PATHS: list[Path] = load_allowed_paths()


def _resolve_safe(path_str: str) -> Path:
    """
    Resolve path and assert it sits inside an allowed root.
    Returns the resolved Path on success, exits with error on failure.

    Protections:
    - Resolves symlinks fully (prevents symlink escape)
    - Blocks path traversal (../../etc)
    - Blocks access outside ALLOWED_PATHS
    """
    if not ALLOWED_PATHS:
        _die("No accessible paths configured. Check ALLOWED_PATHS in .env")

    try:
        target = Path(path_str).resolve()
    except Exception as e:
        _die(f"Invalid path '{path_str}': {e}")

    for allowed in ALLOWED_PATHS:
        try:
            target.relative_to(allowed)
            return target
        except ValueError:
            continue

    _die(f"PERMISSION DENIED: '{path_str}' is not within any allowed path.")


def _resolve_safe_pair(src: str, dst: str) -> tuple[Path, Path]:
    """Resolve and validate both source and destination."""
    return _resolve_safe(src), _resolve_safe(dst)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _die(msg: str, code: int = 1) -> None:
    _err(msg)
    sys.exit(code)


def _ok(msg: str) -> None:
    print(msg)


def _json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_roots() -> None:
    """List all configured allowed root paths."""
    if not ALLOWED_PATHS:
        _die("No accessible paths configured. Check ALLOWED_PATHS in .env")

    results = []
    for p in ALLOWED_PATHS:
        info: dict = {"path": str(p), "exists": p.exists(), "name": p.name or str(p)}
        if p.exists() and p.is_dir():
            try:
                info["items"] = sum(1 for _ in p.iterdir())
            except PermissionError:
                info["items"] = -1
        results.append(info)

    _json(results)


def cmd_list(path: str) -> None:
    """List directory contents, sorted newest-first."""
    p = _resolve_safe(path)

    if not p.exists():
        _die(f"Path not found: {path}")
    if not p.is_dir():
        _die(f"Not a directory: {path}")

    results = []
    try:
        for item in p.iterdir():
            try:
                st = item.stat()
                results.append(
                    {
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                        "size": st.st_size if item.is_file() else 0,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                        "modified_ts": st.st_mtime,
                        "is_symlink": item.is_symlink(),
                    }
                )
            except OSError as e:
                results.append({"name": item.name, "error": str(e)})
    except PermissionError as e:
        _die(f"Permission denied listing directory: {e}")

    results.sort(key=lambda x: x.get("modified_ts", 0), reverse=True)

    for r in results:
        r.pop("modified_ts", None)

    _json(results)


def cmd_stat(path: str) -> None:
    """Return detailed metadata for a file or directory."""
    p = _resolve_safe(path)

    if not p.exists():
        _die(f"Path not found: {path}")

    try:
        st = p.stat()
        _json(
            {
                "path": str(p),
                "name": p.name,
                "type": "dir" if p.is_dir() else "file",
                "size": st.st_size,
                "created": datetime.fromtimestamp(st.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "is_symlink": p.is_symlink(),
                "suffix": p.suffix,
            }
        )
    except OSError as e:
        _die(f"Failed to stat '{path}': {e}")


def cmd_mkdir(path: str) -> None:
    """Create a directory (and any missing parents)."""
    p = _resolve_safe(path)

    try:
        p.mkdir(parents=True, exist_ok=True)
        _ok(f"SUCCESS: Created directory {p}")
    except OSError as e:
        _die(f"Failed to create directory: {e}")


def cmd_read(path: str) -> None:
    """Read a file's contents. Handles both text and binary files."""
    p = _resolve_safe(path)

    if not p.exists():
        _die(f"File not found: {path}")
    if not p.is_file():
        _die(f"Not a file: {path}")

    try:
        try:
            print(p.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            import base64

            encoded = base64.b64encode(p.read_bytes()).decode("ascii")
            _json({"encoding": "base64", "data": encoded})
    except OSError as e:
        _die(f"Failed to read file: {e}")


def cmd_write(path: str, content: str) -> None:
    """Write content to a file, creating parent directories as needed."""
    p = _resolve_safe(path)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _ok(f"SUCCESS: Wrote {len(content)} chars to {p}")
    except OSError as e:
        _die(f"Failed to write file: {e}")


def cmd_append(path: str, content: str) -> None:
    """Append content to a file."""
    p = _resolve_safe(path)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        _ok(f"SUCCESS: Appended {len(content)} chars to {p}")
    except OSError as e:
        _die(f"Failed to append to file: {e}")


def cmd_delete(path: str) -> None:
    """Delete a file or directory."""
    p = _resolve_safe(path)

    if not p.exists():
        _die(f"Path not found: {path}")

    if p in ALLOWED_PATHS:
        _die(f"PERMISSION DENIED: Cannot delete an allowed root path: {p}")

    try:
        if p.is_dir():
            shutil.rmtree(p)
            _ok(f"SUCCESS: Deleted directory {p}")
        else:
            p.unlink()
            _ok(f"SUCCESS: Deleted file {p}")
    except OSError as e:
        _die(f"Failed to delete: {e}")


def cmd_rename(source: str, destination: str) -> None:
    """Move or rename a file/directory. Both paths must be within ALLOWED_PATHS."""
    src, dst = _resolve_safe_pair(source, destination)

    if not src.exists():
        _die(f"Source not found: {source}")

    if dst.exists():
        _die(
            f"Destination already exists: {destination} — delete it first or choose a different name."
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _ok(f"SUCCESS: Moved '{src}' → '{dst}'")
    except OSError as e:
        _die(f"Failed to rename/move: {e}")


def cmd_copy(source: str, destination: str) -> None:
    """Copy a file or directory. Both paths must be within ALLOWED_PATHS."""
    src, dst = _resolve_safe_pair(source, destination)

    if not src.exists():
        _die(f"Source not found: {source}")
    if dst.exists():
        _die(f"Destination already exists: {destination} — delete it first.")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
            _ok(f"SUCCESS: Copied directory '{src}' → '{dst}'")
        else:
            shutil.copy2(str(src), str(dst))
            _ok(f"SUCCESS: Copied file '{src}' → '{dst}'")
    except OSError as e:
        _die(f"Failed to copy: {e}")


def cmd_find(path: str, pattern: str) -> None:
    """Recursively find files matching a glob pattern."""
    p = _resolve_safe(path)

    if not p.exists():
        _die(f"Path not found: {path}")
    if not p.is_dir():
        _die(f"Not a directory: {path}")

    try:
        matches = []
        for match in p.rglob(pattern):
            try:
                match.relative_to(p)
                matches.append(
                    {
                        "path": str(match),
                        "type": "dir" if match.is_dir() else "file",
                        "size": match.stat().st_size if match.is_file() else 0,
                    }
                )
            except ValueError:
                pass

        _json(matches)
    except OSError as e:
        _die(f"Find failed: {e}")


_USAGE = """
Usage: python main.py <command> [args]

Commands:
  roots                          List configured allowed paths
  list   <path>                  List directory contents
  read   <path>                  Read a file
  write  <path> <content>        Write content to a file
  append <path> <content>        Append content to a file
  mkdir  <path>                  Create a directory
  delete <path>                  Delete a file or directory
  rename <source> <dest>         Move/rename (both must be in allowed paths)
  copy   <source> <dest>         Copy file or directory
  stat   <path>                  Show file metadata
  find   <path> <pattern>        Recursive glob search (e.g. *.py)
""".strip()

_COMMANDS_NO_PATH = {"roots"}
_COMMANDS_TWO_PATHS = {"rename", "move", "copy"}
_COMMANDS_WITH_CONTENT = {"write", "append"}


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)

    command = args[0].lower()

    if command in _COMMANDS_NO_PATH:
        cmd_roots()

    elif command == "list":
        if len(args) < 2:
            _die("ERROR: 'list' requires <path>")
        cmd_list(args[1])

    elif command == "stat":
        if len(args) < 2:
            _die("ERROR: 'stat' requires <path>")
        cmd_stat(args[1])

    elif command == "read":
        if len(args) < 2:
            _die("ERROR: 'read' requires <path>")
        cmd_read(args[1])

    elif command == "write":
        if len(args) < 3:
            _die("ERROR: 'write' requires <path> and <content>")
        cmd_write(args[1], args[2])

    elif command == "append":
        if len(args) < 3:
            _die("ERROR: 'append' requires <path> and <content>")
        cmd_append(args[1], args[2])

    elif command == "mkdir":
        if len(args) < 2:
            _die("ERROR: 'mkdir' requires <path>")
        cmd_mkdir(args[1])

    elif command == "delete":
        if len(args) < 2:
            _die("ERROR: 'delete' requires <path>")
        cmd_delete(args[1])

    elif command in ("rename", "move"):
        if len(args) < 3:
            _die("ERROR: 'rename' requires <source> and <destination>")
        cmd_rename(args[1], args[2])

    elif command == "copy":
        if len(args) < 3:
            _die("ERROR: 'copy' requires <source> and <destination>")
        cmd_copy(args[1], args[2])

    elif command == "find":
        if len(args) < 3:
            _die("ERROR: 'find' requires <path> and <pattern>")
        cmd_find(args[1], args[2])

    else:
        _die(f"ERROR: Unknown command '{command}'\n\n{_USAGE}")


if __name__ == "__main__":
    main()
