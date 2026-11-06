"""Authenticated FastAPI routes for the local conversational-agent roster."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from core.paths import get_persona_paths
from core.profiles import (
    AgentProfilePolicy,
    ProfileError,
    ProfileRegistry,
    get_profile_registry,
)


def create_profile_router(
    config: Any,
    verify_auth: Callable[..., Any],
    get_agent: Callable[[], Any],
) -> APIRouter:
    """Build the profile API using the web channel's existing auth contract."""
    router = APIRouter(prefix="/api/profiles", tags=["profiles"])

    def registry() -> ProfileRegistry:
        agent = get_agent()
        configured_registry = getattr(agent, "profile_registry", None)
        return configured_registry or get_profile_registry(config)

    def default_model() -> str:
        llm = getattr(config, "llm", None)
        return str(getattr(llm, "model", None) or "gpt-3.5-turbo")

    def serialize_profile(profile: Any) -> dict[str, Any]:
        data = profile.to_dict()
        data["resolved_model"] = profile.model or default_model()
        return data

    def raise_profile_error(error: ProfileError) -> None:
        message = str(error)
        if message.startswith("unknown profile") or message.startswith(
            "profile is archived"
        ):
            code = status.HTTP_404_NOT_FOUND
        elif message.startswith("profile already exists"):
            code = status.HTTP_409_CONFLICT
        else:
            code = 422
        raise HTTPException(status_code=code, detail=message) from error

    def invalidate_prompt_cache() -> None:
        agent = get_agent()
        coordinator = getattr(agent, "prompt_coordinator", None)
        clear = getattr(coordinator, "clear_cache", None)
        if callable(clear):
            clear()

    @router.get("", dependencies=[Depends(verify_auth)])
    async def list_profiles(include_archived: bool = False):
        agent = get_agent()
        toolbox = getattr(agent, "toolbox", None)
        try:
            available_tools = sorted(
                {
                    str(tool.get("function", {}).get("name") or "")
                    for tool in toolbox.get_tool_definitions()
                    if tool.get("function", {}).get("name")
                }
            )
        except Exception:
            available_tools = []
        return {
            "profiles": [
                serialize_profile(profile)
                for profile in registry().list_profiles(
                    include_archived=include_archived
                )
            ],
            "default_model": default_model(),
            "available_tools": available_tools,
        }

    @router.post(
        "", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_auth)]
    )
    async def create_profile(data: dict[str, Any]):
        allowed = {"profile_id", "display_name", "model", "policy"}
        unknown = set(data) - allowed
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported profile fields: {', '.join(sorted(unknown))}",
            )
        if not isinstance(data.get("display_name"), str):
            raise HTTPException(
                status_code=422,
                detail="display_name is required",
            )
        if data.get("profile_id") is not None and not isinstance(
            data.get("profile_id"), str
        ):
            raise HTTPException(status_code=422, detail="profile_id must be a string")
        if data.get("model") is not None and not isinstance(data.get("model"), str):
            raise HTTPException(status_code=422, detail="model must be a string or null")
        if "policy" in data:
            try:
                policy = AgentProfilePolicy.from_dict(data["policy"])
            except ProfileError as error:
                raise_profile_error(error)
        else:
            policy = AgentProfilePolicy()
        try:
            profile = registry().create_profile(
                data["display_name"],
                profile_id=data.get("profile_id"),
                model=data.get("model"),
                policy=policy,
            )
        except ProfileError as error:
            raise_profile_error(error)
        return {"profile": serialize_profile(profile)}

    @router.patch("/{profile_id}", dependencies=[Depends(verify_auth)])
    async def update_profile(profile_id: str, data: dict[str, Any]):
        allowed = {"display_name", "model", "policy"}
        unknown = set(data) - allowed
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported profile fields: {', '.join(sorted(unknown))}",
            )
        changes = dict(data)
        if not changes:
            raise HTTPException(status_code=422, detail="at least one profile field is required")
        if changes.get("display_name") is not None and not isinstance(
            changes.get("display_name"), str
        ):
            raise HTTPException(status_code=422, detail="display_name must be a string")
        if changes.get("model") is not None and not isinstance(changes.get("model"), str):
            raise HTTPException(status_code=422, detail="model must be a string or null")
        if "policy" in changes:
            try:
                changes["policy"] = AgentProfilePolicy.from_dict(changes["policy"])
            except ProfileError as error:
                raise_profile_error(error)
        try:
            profile = registry().update_profile(profile_id, **changes)
        except ProfileError as error:
            raise_profile_error(error)
        return {"profile": serialize_profile(profile)}

    @router.post("/{profile_id}/archive", dependencies=[Depends(verify_auth)])
    async def archive_profile(profile_id: str):
        try:
            profile = registry().archive_profile(profile_id)
        except ProfileError as error:
            raise_profile_error(error)
        return {"profile": serialize_profile(profile)}

    @router.post("/{profile_id}/unarchive", dependencies=[Depends(verify_auth)])
    async def unarchive_profile(profile_id: str):
        try:
            profile = registry().unarchive_profile(profile_id)
        except ProfileError as error:
            raise_profile_error(error)
        return {"profile": serialize_profile(profile)}

    @router.delete("/{profile_id}", dependencies=[Depends(verify_auth)])
    async def delete_profile(profile_id: str):
        try:
            profile = registry().archive_profile(profile_id)
        except ProfileError as error:
            raise_profile_error(error)
        return {"profile": serialize_profile(profile)}

    @router.get("/{profile_id}/persona", dependencies=[Depends(verify_auth)])
    async def get_profile_persona(profile_id: str):
        try:
            profile = registry().get_profile(profile_id)
        except ProfileError as error:
            raise_profile_error(error)
        paths = get_persona_paths(profile.profile_id)

        def read_persona() -> dict[str, str]:
            fields = {
                "soul": paths.soul,
                "identity": paths.identity,
                "mood": paths.mood,
            }
            return {
                name: path.read_text(encoding="utf-8") if path.is_file() else ""
                for name, path in fields.items()
            }

        persona = await asyncio.to_thread(read_persona)
        return {"profile_id": profile.profile_id, **persona}

    @router.put("/{profile_id}/persona/{section}", dependencies=[Depends(verify_auth)])
    async def update_profile_persona(profile_id: str, section: str, data: dict[str, Any]):
        if section not in {"soul", "identity", "mood"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="unknown persona section",
            )
        content = data.get("content")
        if set(data) != {"content"} or not isinstance(content, str):
            raise HTTPException(
                status_code=422,
                detail="content must be a string",
            )
        if len(content) > 20_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="persona content exceeds 20000 characters",
            )
        try:
            profile = registry().get_profile(profile_id)
        except ProfileError as error:
            raise_profile_error(error)

        from core.prompt import (
            validate_and_save_identity,
            validate_and_save_mood,
            validate_and_save_soul,
        )

        paths = get_persona_paths(profile.profile_id)
        save = {
            "soul": validate_and_save_soul,
            "identity": validate_and_save_identity,
            "mood": validate_and_save_mood,
        }[section]
        saved = await asyncio.to_thread(save, content, paths)
        if not saved:
            raise HTTPException(
                status_code=422,
                detail=f"{section} content failed validation",
            )
        invalidate_prompt_cache()
        return {
            "status": "success",
            "profile_id": profile.profile_id,
            "section": section,
        }

    return router
