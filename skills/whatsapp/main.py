"""
WhatsApp skill - Send files to WhatsApp conversations.

This skill provides a tool for sending files directly to WhatsApp chats.
"""

import sys
import json
from pathlib import Path


project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def send_whatsapp_file(file_path: str, recipient: str, caption: str = None) -> dict:
    """
    Send a file to a WhatsApp conversation via the bot's internal API.

    Args:
        file_path: Absolute path to the file to send
        recipient: The WhatsApp JID (e.g., "1234567890@s.whatsapp.net"). If you are replying to a user, use their `chat_id` from the current conversation metadata. DO NOT ask the user for this ID.
        caption: Optional caption for the file

    Returns:
        dict with status and message
    """
    import urllib.request
    import urllib.parse
    import json
    import os

    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    if not path.is_file():
        return {"status": "error", "message": f"Not a file: {file_path}"}

    if "@" not in recipient:
        recipient = f"{recipient}@s.whatsapp.net"
        (
            f"[WhatsApp Skill] WARNING: No @ in recipient, defaulting to {recipient}. If delivery fails, use the full chat_id from conversation context."
        )

    forbidden_files = [
        "IDENTITY.md",
        "SOUL.md",
        "MEMORY.md",
        ".env",
        "limebot.json",
        "id_rsa",
        ".pem",
        ".key",
        "AGENTS.md",
    ]
    filename = path.name
    if filename in forbidden_files or filename.endswith(".env"):
        return {
            "status": "error",
            "message": f"Security Alert: Sending '{filename}' is forbidden.",
        }

    if "persona" in str(path.absolute()).lower() and filename.endswith(".md"):
        return {
            "status": "error",
            "message": "Security Alert: Sending persona files is forbidden.",
        }

    url = "http://localhost:8000/api/whatsapp/send_file"
    params = {
        "to": recipient,
        "file_path": str(path.absolute()),
        "caption": caption or "",
    }

    api_key = os.getenv("APP_API_KEY")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    query_string = urllib.parse.urlencode(params)
    full_url = f"{url}?{query_string}"

    try:
        req = urllib.request.Request(full_url, method="POST", headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data
    except Exception as e:
        return {"status": "error", "message": f"API call failed: {e}"}


def set_whatsapp_channel(channel):
    pass


def get_whatsapp_channel():
    pass


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python main.py <file_path> <recipient> [caption]", file=sys.stderr
        )
        sys.exit(1)

    file_path = sys.argv[1]
    recipient = sys.argv[2]
    caption = sys.argv[3] if len(sys.argv) > 3 else None

    result = send_whatsapp_file(file_path, recipient, caption)
    print(json.dumps(result, indent=2))
