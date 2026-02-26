"""Configuration loader."""

import os
from types import SimpleNamespace
from dotenv import load_dotenv
from loguru import logger

from pathlib import Path


env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv()


_cached_config = None


def load_config(force_reload=False):
    """Load configuration from environment variables. Cached after first call."""
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    config = SimpleNamespace()

    config.discord = SimpleNamespace()
    config.discord.enabled = os.getenv("ENABLE_DISCORD", "true").lower() == "true"
    config.discord.token = os.getenv("DISCORD_TOKEN")
    config.discord.allow_from = [
        x for x in os.getenv("DISCORD_ALLOW_FROM", "").split(",") if x
    ]
    config.discord.allow_channels = [
        x for x in os.getenv("DISCORD_ALLOW_CHANNELS", "").split(",") if x
    ]
    config.discord.activity_type = os.getenv("DISCORD_ACTIVITY_TYPE", "playing")
    config.discord.activity_text = os.getenv("DISCORD_ACTIVITY_TEXT", "LimeBot")
    config.discord.status = os.getenv("DISCORD_STATUS", "online")
    config.web = SimpleNamespace()
    try:
        config.web.port = int(os.getenv("WEB_PORT") or os.getenv("PORT") or "8000")
    except ValueError:
        logger.warning("Invalid WEB_PORT/PORT in .env, defaulting to 8000.")
        config.web.port = 8000

    config.whatsapp = SimpleNamespace()
    config.whatsapp.enabled = os.getenv("ENABLE_WHATSAPP", "false").lower() == "true"
    config.whatsapp.bridge_url = os.getenv("WHATSAPP_BRIDGE_URL", "ws://localhost:3000")
    config.whatsapp.allow_from = [
        x for x in os.getenv("WHATSAPP_ALLOW_FROM", "").split(",") if x
    ]

    config.autonomous_mode = os.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
    config.allow_unsafe_commands = (
        os.getenv("ALLOW_UNSAFE_COMMANDS", "false").lower() == "true"
    )

    try:
        config.max_iterations = int(os.getenv("MAX_ITERATIONS", "30"))
    except ValueError:
        logger.warning("Invalid MAX_ITERATIONS in .env, defaulting to 30.")
        config.max_iterations = 30

    try:
        config.command_timeout = float(os.getenv("COMMAND_TIMEOUT", "300.0"))
    except ValueError:
        logger.warning("Invalid COMMAND_TIMEOUT in .env, defaulting to 300.0.")
        config.command_timeout = 300.0

    try:
        config.stall_timeout = float(os.getenv("STALL_TIMEOUT", "30"))
    except ValueError:
        logger.warning("Invalid STALL_TIMEOUT in .env, defaulting to 30.")
        config.stall_timeout = 30

    from core.llm_utils import get_api_key_for_model

    config.llm = SimpleNamespace()
    config.llm.model = os.getenv("LLM_MODEL") or "gemini/gemini-2.0-flash"
    config.llm.api_key = get_api_key_for_model(config.llm.model)
    config.llm.enable_dynamic_personality = (
        os.getenv("ENABLE_DYNAMIC_PERSONALITY", "false").lower() == "true"
    )

    config.llm.base_url = os.getenv("LLM_BASE_URL")

    is_google_model = config.llm.model and (
        "gemini" in config.llm.model or "vertex" in config.llm.model
    )

    if config.llm.base_url and not is_google_model:
        clean_url = config.llm.base_url.rstrip("/")
        if clean_url.endswith("/api"):
            clean_url = clean_url[:-4]

        config.llm.base_url = clean_url
        os.environ["OPENAI_API_BASE"] = clean_url
    elif is_google_model:
        config.llm.base_url = None
        if "OPENAI_API_BASE" in os.environ:
            del os.environ["OPENAI_API_BASE"]

    is_local_llm = (
        config.llm.model
        and ("ollama" in config.llm.model or "local" in config.llm.model)
    ) or config.llm.base_url
    if not config.llm.api_key and not is_local_llm:
        logger.warning("No valid LLM API Key found in environment.")

    config.whitelist = SimpleNamespace()
    config.whitelist.allowed_paths = []

    # 1. Load from .env ALLOWED_PATHS (comma-separated)
    raw_paths = os.getenv("ALLOWED_PATHS", "")
    for p in raw_paths.replace(";", ",").split(","):
        if p.strip() and p.strip() not in config.whitelist.allowed_paths:
            config.whitelist.allowed_paths.append(p.strip())

    # 2. Load from allowed_paths.txt (one path per line), creating it if missing
    paths_file = os.path.join(os.getcwd(), "allowed_paths.txt")
    if not os.path.exists(paths_file):
        try:
            with open(paths_file, "w", encoding="utf-8") as f:
                f.write("# Add your allowed workspace paths here, one per line.\n")
                f.write("# These directories will be accessible to LimeBot.\n")
                f.write("./persona\n")
                f.write("./logs\n")
        except Exception as e:
            logger.error(f"Error creating default allowed_paths.txt: {e}")

    if os.path.exists(paths_file):
        try:
            with open(paths_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if (
                        line
                        and not line.startswith("#")
                        and line not in config.whitelist.allowed_paths
                    ):
                        config.whitelist.allowed_paths.append(line)
        except Exception as e:
            logger.error(f"Error reading allowed_paths.txt: {e}")

    limebot_root = os.getcwd()
    if limebot_root not in config.whitelist.allowed_paths:
        config.whitelist.allowed_paths.append(limebot_root)

    config.whitelist.api_key = os.getenv("APP_API_KEY")
    config.personality_whitelist = [
        x.strip()
        for x in os.getenv("PERSONALITY_WHITELIST", "").split(",")
        if x.strip()
    ]

    if config.llm.api_key and is_google_model:
        os.environ.setdefault("GEMINI_API_KEY", config.llm.api_key)
        os.environ.setdefault("GOOGLE_API_KEY", config.llm.api_key)

    limebot_config_path = os.path.join(os.getcwd(), "limebot.json")
    if os.path.exists(limebot_config_path):
        import json

        try:
            with open(limebot_config_path, "r", encoding="utf-8") as f:
                dynamic_config = json.load(f)

                if "skills" in dynamic_config:
                    config.skills = SimpleNamespace(**dynamic_config["skills"])
                else:
                    config.skills = SimpleNamespace(disabled=[])

                if "allowed_paths" in dynamic_config and isinstance(
                    dynamic_config["allowed_paths"], list
                ):
                    for p in dynamic_config["allowed_paths"]:
                        if (
                            isinstance(p, str)
                            and p.strip()
                            and p.strip() not in config.whitelist.allowed_paths
                        ):
                            config.whitelist.allowed_paths.append(p.strip())

                if "llm" in dynamic_config and isinstance(dynamic_config["llm"], dict):
                    for k, v in dynamic_config["llm"].items():
                        setattr(config.llm, k, v)
                if "discord" in dynamic_config and isinstance(
                    dynamic_config["discord"], dict
                ):
                    for k, v in dynamic_config["discord"].items():
                        setattr(config.discord, k, v)
        except Exception as e:
            logger.error(f"Error loading limebot.json: {e}")
            config.skills = SimpleNamespace(disabled=[])
    else:
        config.skills = SimpleNamespace(disabled=[])

    _cached_config = config
    return config


def reload_config():
    """Force-reload config (call after .env changes)."""
    return load_config(force_reload=True)
