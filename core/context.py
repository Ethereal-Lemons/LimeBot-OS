from contextvars import ContextVar
from typing import Any, Dict


tool_context: ContextVar[Dict[str, Any]] = ContextVar("tool_context", default={})
