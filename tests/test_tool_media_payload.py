import json
from pathlib import Path

from core.loop import AgentLoop


def test_compact_local_image_payload_is_converted_for_vision():
    attachment = Path("temp") / "jira_tool_payload_test.png"
    attachment.write_bytes(b"test-image")
    try:
        payload = {
            "text": "Retrieved 1 inline image attachment.",
            "images": [
                {
                    "name": "ticket.png",
                    "source": "Jira attachment on GDHD-1195",
                    "path": attachment.as_posix(),
                    "mime_type": "image/png",
                }
            ],
        }
        raw = (
            "<limebot-tool-payload>"
            f"{json.dumps(payload)}"
            "</limebot-tool-payload>"
        )

        cleaned, images = AgentLoop._extract_tool_media_payload(raw)

        assert cleaned == "Retrieved 1 inline image attachment."
        assert len(images) == 1
        assert images[0]["name"] == "ticket.png"
        assert images[0]["source"] == "Jira attachment on GDHD-1195"
        assert images[0]["url"].startswith("data:image/png;base64,")
    finally:
        attachment.unlink(missing_ok=True)
