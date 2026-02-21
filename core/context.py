from contextvars import ContextVar
from typing import Dict


tool_context: ContextVar[Dict[str, str]] = ContextVar("tool_context", default={})
