"""Own system-prompt assembly and the short-lived stable-prompt cache."""

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Dict, Optional, Tuple

from loguru import logger

from core import prompt as prompt_module
from core.prompt_modes import build_ponytail_prompt_addition
from core.prompt_prefix import PER_MESSAGE_BOUNDARY
from core.voice_tool_policy import voice_prompt_addition
from core.paths import IDENTITY_FILE, SOUL_FILE, PersonaPaths
from core.profile_context import get_current_persona_paths, get_current_profile_id


class PromptCoordinator:
    """Build prompts while keeping turn-specific routing outside ``AgentLoop``."""

    def __init__(
        self,
        *,
        config: Any,
        model: Callable[[], str],
        allowed_paths: Callable[[], Any],
        skill_registry: Any,
        subagent_registry: Any,
        get_capability_turn_context: Callable[[Optional[str], str], Dict[str, Any]],
        capability_catalog_prompt: Callable[[], str],
        soul_file: Path = SOUL_FILE,
        identity_file: Path = IDENTITY_FILE,
        stable_prompt_ttl: float = 30.0,
    ):
        self.config = config
        self._model = model
        self._allowed_paths = allowed_paths
        self.skill_registry = skill_registry
        self.subagent_registry = subagent_registry
        self._get_capability_turn_context = get_capability_turn_context
        self._capability_catalog_prompt = capability_catalog_prompt
        self._soul_file = soul_file
        self._identity_file = identity_file
        self._default_persona_paths = PersonaPaths(soul_file.parent)
        self._stable_prompt_ttl = stable_prompt_ttl
        self._stable_prompt_cache: Dict[str, Tuple[str, float]] = {}

    def clear_cache(self) -> None:
        self._stable_prompt_cache.clear()

    def invalidate(self, sender_id: str, profile_id: Optional[str] = None) -> None:
        """Drop cached prompts for one sender after persona changes."""
        profile_id = str(profile_id or get_current_profile_id()).strip().lower()
        prefix = (
            f"{sender_id}:"
            if profile_id == "default"
            else f"profile:{profile_id}:{sender_id}:"
        )
        for key in [key for key in self._stable_prompt_cache if key.startswith(prefix)]:
            del self._stable_prompt_cache[key]

    def _persona_paths(self) -> PersonaPaths:
        context = get_current_persona_paths()
        if get_current_profile_id() == "default":
            return self._default_persona_paths
        return context

    async def get_stable_prompt(
        self, sender_id: str, channel: str, chat_id: str, sender_name: str = ""
    ) -> str:
        """Return the rarely-changing prompt, cached per sender and channel."""
        profile_id = get_current_profile_id()
        key = (
            f"{sender_id}:{channel}"
            if profile_id == "default"
            else f"profile:{profile_id}:{sender_id}:{channel}"
        )
        cached = self._stable_prompt_cache.get(key)
        now = monotonic()
        if cached and now < cached[1]:
            return cached[0]

        paths = self._persona_paths()
        try:
            soul = (
                await asyncio.to_thread(paths.soul.read_text, encoding="utf-8")
                if paths.soul.exists()
                else ""
            )
            identity_raw = (
                await asyncio.to_thread(
                    paths.identity.read_text, encoding="utf-8"
                )
                if paths.identity.exists()
                else ""
            )
        except Exception as error:
            logger.warning(f"Error reading persona files: {error}")
            soul = identity_raw = ""

        if not prompt_module.is_setup_complete(
            soul_content=soul,
            identity_content=identity_raw,
            persona_paths=paths,
        ):
            return prompt_module.get_setup_prompt(
                soul_content=soul,
                identity_content=identity_raw,
                persona_paths=paths,
            )

        stable = prompt_module.build_stable_system_prompt(
            sender_id=sender_id,
            channel=channel,
            chat_id=chat_id,
            model=self._model(),
            allowed_paths=self._allowed_paths(),
            skill_registry=self.skill_registry,
            config=self.config,
            soul=soul,
            identity_raw=identity_raw,
            sender_name=sender_name,
            persona_paths=paths,
        )
        self._stable_prompt_cache[key] = (stable, now + self._stable_prompt_ttl)
        return stable

    async def build_full_prompt(
        self,
        *,
        sender_id: str,
        channel: str,
        chat_id: str,
        recalled_context: str = "",
        sender_name: str = "",
        current_message: str = "",
        forced_skill_name: Optional[str] = None,
        ponytail_mode: str = "off",
        session_key: Optional[str] = None,
        stable_prompt_getter: Optional[Callable[..., Any]] = None,
    ) -> str:
        """Combine stable identity with capability, skill, and volatile context."""
        getter = stable_prompt_getter or self.get_stable_prompt
        stable = await getter(sender_id, channel, chat_id, sender_name)
        capability_context = self._get_capability_turn_context(
            session_key, current_message
        )
        routing_text = capability_context.get("routing_text") or current_message

        if forced_skill_name:
            skills_docs = self.skill_registry.get_forced_prompt_addition(
                forced_skill_name
            )
        else:
            skills_docs = self.skill_registry.get_relevant_prompt_additions(
                routing_text
            )

        capability_docs = (
            self._capability_catalog_prompt()
            if prompt_module.is_setup_complete(
                persona_paths=self._persona_paths()
            )
            else ""
        )
        subagent_docs = self.subagent_registry.get_prompt_additions(
            current_message
        )

        ponytail_docs = build_ponytail_prompt_addition(ponytail_mode)
        include_private_memory = prompt_module.should_load_private_context(
            sender_id, channel, self.config
        )
        volatile = prompt_module.get_volatile_prompt_suffix(
            recalled_context,
            include_private_memory=include_private_memory,
            current_message=current_message,
            persona_paths=self._persona_paths(),
        )
        # Everything before the boundary is identical from one message to the
        # next, so providers can cache it together with the history that follows.
        # Everything after it is rebuilt per message (see core/prompt_prefix.py).
        return (
            stable
            + capability_docs
            + (subagent_docs + "\n" if subagent_docs else "")
            + PER_MESSAGE_BOUNDARY
            + (skills_docs + "\n" if skills_docs else "")
            + (ponytail_docs + "\n" if ponytail_docs else "")
            + volatile
            + voice_prompt_addition(session_key)
        )
