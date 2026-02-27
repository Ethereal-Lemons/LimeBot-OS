"""
Discord Skill - Send messages to Discord channels via the LimeBot backend.

This script communicates with the LimeBot web backend via HTTP to send
messages to Discord channels. The actual Discord sending is handled by
the running Discord channel in the backend.

Usage:
    python main.py send <channel_id> "<message>"
    python main.py embed <channel_id> "<title>" "<description>" [color]
    python main.py file <channel_id> <file_path> ["caption"]
    python main.py list
    python main.py leave <guild_id>
    python main.py history <channel_id> [limit]
"""

import io
import json
import sys
import urllib.error
import urllib.request
from typing import Any


if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass

BACKEND_URL = "http://127.0.0.1:8000/api/skill/discord"
DEFAULT_COLOR = "#5865F2"
REQUEST_TIMEOUT = 30


MAX_MESSAGE_LENGTH = 2000
MAX_EMBED_TITLE_LENGTH = 256
MAX_EMBED_DESCRIPTION_LENGTH = 4096


def _validate_channel_id(channel_id: str) -> None:
    if not channel_id.isdigit():
        raise ValueError(f"Invalid channel_id '{channel_id}': must be numeric.")


def _validate_color(color: str) -> None:
    import re

    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"Invalid color '{color}': must be a hex color like #5865F2.")


def _validate_message(text: str, max_len: int, field: str = "message") -> None:
    if not text:
        raise ValueError(f"'{field}' cannot be empty.")
    if len(text) > max_len:
        raise ValueError(
            f"'{field}' exceeds Discord's {max_len}-character limit ({len(text)} chars)."
        )


def _post(endpoint: str, payload: dict[str, Any]) -> dict:
    """POST JSON to a backend endpoint. Returns parsed response or raises on error."""
    url = f"{BACKEND_URL}/{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
            detail_json = json.loads(detail)
            detail = detail_json.get("detail") or detail_json.get("error") or detail
        except Exception:
            detail = str(e)
        raise RuntimeError(f"HTTP {e.code} from backend: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Connection failed — is LimeBot running? ({e.reason})"
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Backend returned non-JSON response: {e}") from e


def send_message(channel_id: str, message: str) -> dict:
    """Send a plain text message to a Discord channel."""
    _validate_channel_id(channel_id)
    _validate_message(message, MAX_MESSAGE_LENGTH, "message")
    return _post("send", {"channel_id": channel_id, "message": message})


def send_embed(
    channel_id: str,
    title: str,
    description: str,
    color: str = DEFAULT_COLOR,
) -> dict:
    """Send an embed message to a Discord channel."""
    _validate_channel_id(channel_id)
    _validate_message(title, MAX_EMBED_TITLE_LENGTH, "title")
    _validate_message(description, MAX_EMBED_DESCRIPTION_LENGTH, "description")
    _validate_color(color)
    return _post(
        "embed",
        {
            "channel_id": channel_id,
            "title": title,
            "description": description,
            "color": color,
        },
    )


def send_file(channel_id: str, file_path: str, caption: str = "") -> dict:
    """Send a file to a Discord channel."""
    from pathlib import Path

    _validate_channel_id(channel_id)
    p = Path(file_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not p.is_file():
        raise ValueError(f"Path is not a file: {file_path}")
    return _post(
        "file",
        {
            "channel_id": channel_id,
            "file_path": str(p),
            "caption": caption,
        },
    )


def list_channels() -> dict:
    """List available Discord guilds and channels."""
    return _post("list", {})

def leave_guild(guild_id: str) -> dict:
    """Leave a Discord guild by ID."""
    if not guild_id.isdigit():
        raise ValueError(f"Invalid guild_id '{guild_id}': must be numeric.")
    return _post("leave", {"guild_id": guild_id})

def fetch_history(channel_id: str, limit: int = 20) -> dict:
    """Fetch recent messages from a Discord text channel."""
    _validate_channel_id(channel_id)
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    return _post("history", {"channel_id": channel_id, "limit": limit})


def _safe_print(text: str) -> None:
    """Print, falling back to ASCII replacement if the terminal can't handle Unicode."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def _print_channels(result: dict) -> None:
    guilds = result.get("guilds", [])
    if not guilds:
        _safe_print("No guilds found.")
        return
    _safe_print(f"Found {len(guilds)} guild(s):")
    for guild in guilds:
        _safe_print(f"\n  Guild: {guild.get('name', '?')} (ID: {guild.get('id', '?')})")
        for ch in guild.get("channels", []):
            _safe_print(f"    • #{ch.get('name', '?')} (ID: {ch.get('id', '?')})")


USAGE = """
Usage:
  python main.py send  <channel_id> "<message>"
  python main.py embed <channel_id> "<title>" "<description>" [color]
  python main.py file  <channel_id> <file_path> ["caption"]
  python main.py list
  python main.py leave <guild_id>
  python main.py history <channel_id> [limit]
""".strip()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    command = args[0].lower()

    try:
        if command == "send":
            if len(args) < 3:
                print(
                    "ERROR: 'send' requires <channel_id> and <message>", file=sys.stderr
                )
                sys.exit(1)
            result = send_message(args[1], args[2])

        elif command == "embed":
            if len(args) < 4:
                print(
                    "ERROR: 'embed' requires <channel_id>, <title>, and <description>",
                    file=sys.stderr,
                )
                sys.exit(1)
            color = args[4] if len(args) > 4 else DEFAULT_COLOR
            result = send_embed(args[1], args[2], args[3], color)

        elif command == "file":
            if len(args) < 3:
                print(
                    "ERROR: 'file' requires <channel_id> and <file_path>",
                    file=sys.stderr,
                )
                sys.exit(1)
            caption = args[3] if len(args) > 3 else ""
            result = send_file(args[1], args[2], caption)

        elif command == "list":
            result = list_channels()
            _print_channels(result)
            sys.exit(0)
        elif command == "leave":
            if len(args) < 2:
                print("ERROR: 'leave' requires <guild_id>", file=sys.stderr)
                sys.exit(1)
            result = leave_guild(args[1])

        elif command == "history":
            if len(args) < 2:
                print("ERROR: 'history' requires <channel_id>", file=sys.stderr)
                sys.exit(1)
            limit = 20
            if len(args) >= 3:
                try:
                    limit = int(args[2])
                except ValueError:
                    print("ERROR: 'limit' must be an integer", file=sys.stderr)
                    sys.exit(1)
            result = fetch_history(args[1], limit)
            if "messages" in result:
                _safe_print(f"Last {len(result['messages'])} message(s) from channel {result.get('channel_id')}:")
                for msg in result["messages"]:
                    author = msg.get("author", "?")
                    content = msg.get("content", "")
                    created = msg.get("created_at", "")
                    _safe_print(f"- [{created}] {author}: {content}")
                sys.exit(0)

        else:
            print(f"ERROR: Unknown command '{command}'", file=sys.stderr)
            print(USAGE, file=sys.stderr)
            sys.exit(1)

    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    _safe_print(f"OK: {json.dumps(result)}")


if __name__ == "__main__":
    main()
