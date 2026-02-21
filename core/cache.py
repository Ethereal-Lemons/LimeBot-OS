import time
import hashlib
import json
from collections import OrderedDict
from typing import Any, Optional


class ToolCache:
    """
    Simple LRU Cache for tool results with TTL support.
    """

    def __init__(self, max_size: int = 100):
        self.cache = OrderedDict()
        self.max_size = max_size

        self.ttls = {
            "default": 300,
            "read_file": 3600,
            "browser_extract": 300,
            "list_dir": 60,
            "memory_search": 60,
        }

    def _get_key(self, tool_name: str, args: dict) -> str:
        """Generate a stable cache key."""

        serialized_args = json.dumps(args, sort_keys=True)
        args_hash = hashlib.md5(serialized_args.encode()).hexdigest()
        return f"{tool_name}:{args_hash}"

    def get(self, tool_name: str, args: dict) -> Optional[Any]:
        """Retrieve a result if valid."""
        key = self._get_key(tool_name, args)
        if key not in self.cache:
            return None

        timestamp, result = self.cache[key]

        ttl = self.ttls.get(tool_name, self.ttls["default"])
        if time.time() - timestamp > ttl:
            del self.cache[key]
            return None

        self.cache.move_to_end(key)
        return result

    def set(self, tool_name: str, args: dict, result: Any):
        """Cache a result."""

        if isinstance(result, str):
            prefixes = (
                "Error:",
                "Failed:",
                "Action Blocked:",
                "ACTION CANCELLED:",
                "ACTION BLOCKED:",
            )
            if any(result.startswith(p) for p in prefixes):
                return

        key = self._get_key(tool_name, args)

        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)

        self.cache[key] = (time.time(), result)

    def clear(self):
        self.cache.clear()
