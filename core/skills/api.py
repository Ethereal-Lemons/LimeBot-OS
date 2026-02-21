"""
Skill API - Provides a generic API endpoint for skills that need backend access.

Skills can create an api.py file with a `handle` function to receive
backend calls without modifying the core web.py file.
"""

from typing import Dict, Any, Callable
from .registry import SkillRegistry


class SkillAPI:
    """
    Generic API router for skill backend handlers.

    Allows skills to expose API endpoints without modifying core code.
    Skills just need to create an api.py with a handle() function.
    """

    def __init__(self, registry: SkillRegistry, bus: Any = None, channels: list = None):
        """
        Initialize the Skill API.

        Args:
            registry: The skill registry
            bus: Message bus for channel communication (Discord, etc.)
            channels: List of active channel instances
        """
        self.registry = registry
        self.bus = bus
        self.channels = channels or []

    async def handle_request(
        self, skill_name: str, action: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Route a request to the appropriate skill handler.

        Args:
            skill_name: Name of the skill
            action: Action to perform (e.g., "send", "create")
            data: Request data/parameters

        Returns:
            Response dictionary from the skill handler
        """

        skill = self.registry.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found"}

        if not self.registry.has_api_handler(skill_name):
            return {"error": f"Skill '{skill_name}' does not have an API handler"}

        handler = self.registry.get_api_handler(skill_name)

        try:
            context = {
                "bus": self.bus,
                "skill": skill,
                "config": self.registry.config.get("skills", {})
                .get("entries", {})
                .get(skill_name, {}),
                "channels": self.channels,
            }

            if callable(handler):
                result = await self._call_handler(handler, action, data, context)
            elif hasattr(handler, "handle"):
                result = await self._call_handler(handler.handle, action, data, context)
            else:
                return {"error": f"Invalid handler for skill '{skill_name}'"}

            return result

        except Exception as e:
            return {"error": f"Skill handler error: {str(e)}"}

    async def _call_handler(
        self, handler: Callable, action: str, data: Dict, context: Dict
    ) -> Dict[str, Any]:
        """Call a handler, handling both sync and async functions."""
        import inspect

        result = handler(action, data, context)

        if inspect.iscoroutine(result):
            result = await result

        return result if isinstance(result, dict) else {"result": result}

    def get_available_skills_with_api(self) -> Dict[str, str]:
        """
        Get a list of skills that have API handlers.

        Returns:
            Dictionary of skill_name -> description
        """
        return {
            name: skill["description"]
            for name, skill in self.registry.skills.items()
            if self.registry.has_api_handler(name)
        }
