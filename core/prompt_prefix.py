"""Keep the request prefix identical between turns so providers can cache it.

Providers that cache prompt prefixes (OpenAI-style automatic caching, and the
explicit schemes others use) reuse work only for the part of a request that is
byte-identical to an earlier one. LimeBot's system prompt mixes text that
almost never changes (persona, rules, tool catalog) with text that changes on
every message (matched skills, recalled memory, the clock). When all of it sits
in the first message, one changed character there invalidates the cache for the
whole conversation that follows.

The prompt builder therefore places ``PER_MESSAGE_BOUNDARY`` between the two
kinds of text. History keeps the full prompt unchanged; only the request that
goes upstream is rearranged: the system message keeps the stable head, and the
per-message block moves to just after the newest user message, where it can
change without touching anything before it.
"""

from __future__ import annotations

from typing import Any, Dict, List

PER_MESSAGE_BOUNDARY = "\n--- PER-MESSAGE CONTEXT ---\n"


def relocate_per_message_context(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return ``messages`` with the per-message system text moved after the last user turn.

    The input list and its messages are never modified. Requests that carry no
    boundary (subagents, summaries, setup prompts) or no user message come back
    unchanged.
    """
    if not messages:
        return messages
    first = messages[0]
    if not isinstance(first, dict) or first.get("role") != "system":
        return messages
    content = first.get("content")
    if not isinstance(content, str):
        return messages
    head, boundary, tail = content.partition(PER_MESSAGE_BOUNDARY)
    if not boundary or not tail.strip():
        return messages

    anchor = next(
        (
            index
            for index in range(len(messages) - 1, 0, -1)
            if isinstance(messages[index], dict) and messages[index].get("role") == "user"
        ),
        None,
    )
    if anchor is None:
        return messages

    relocated = list(messages)
    relocated[0] = {**first, "content": head}
    relocated.insert(
        anchor + 1,
        {"role": "system", "content": boundary.lstrip("\n") + tail},
    )
    return relocated
