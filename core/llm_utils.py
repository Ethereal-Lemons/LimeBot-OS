import httpx
import logging
import os
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)
QWEN_COMPAT_BASE_URLS = [
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
]

# Compatibility aliases for provider model IDs that were renamed or removed.
MODEL_ALIASES = {
    "xai/grok-4.1-fast-reasoning": "xai/grok-4-fast-reasoning",
    "xai/grok-4.1-fast-non-reasoning": "xai/grok-4",
    "grok-4.1-fast-reasoning": "xai/grok-4-fast-reasoning",
    "grok-4.1-fast-non-reasoning": "xai/grok-4",
}


async def fetch_openai_compatible_models(
    api_key: str, base_url: str, provider_name: str, prefix_id: bool = True
) -> List[Dict[str, Any]]:
    """
    Fetch models from an OpenAI-compatible API endpoint.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL for the API (e.g., "https://api.openai.com/v1").
        provider_name: The name of the provider (e.g., "openai", "nvidia", "xai").
        prefix_id: Whether to prefix the model ID with the provider name (e.g., "nvidia/model-name").

    Returns:
        A list of model dictionaries suitable for the UI.
    """
    if not api_key:
        return []

    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                models = []
                for item in data.get("data", []):
                    model_id = item.get("id")
                    if not model_id:
                        continue

                    non_text_keywords = [
                        "embed",
                        "audio",
                        "dall-e",
                        "tts",
                        "stt",
                        "whisper",
                        "moderation",
                        "stable-diffusion",
                        "flux",
                        "rerank",
                        "bge-",
                        "gte-",
                        "clip",
                        "siglip",
                    ]
                    if any(kw in model_id.lower() for kw in non_text_keywords):
                        continue

                    display_name = model_id.split("/")[-1].replace("-", " ").title()
                    final_id = f"{provider_name}/{model_id}" if prefix_id else model_id

                    models.append(
                        {
                            "id": final_id,
                            "name": display_name,
                            "provider": provider_name,
                        }
                    )

                logger.info(f"Fetched {len(models)} models from {provider_name}")
                return models
            else:
                logger.warning(
                    f"Failed to fetch {provider_name} models: {response.status_code} - {response.text}"
                )
                return []
    except Exception as e:
        logger.error(f"Error fetching {provider_name} models: {e}")
        return []


async def fetch_anthropic_models(api_key: str) -> List[Dict[str, Any]]:
    """
    Fetch models from Anthropic API.
    Note: Anthropic's 'models' endpoint might behave differently or require specific headers.
    As of early 2026, standard listing might be limited, but we'll try the standard endpoint.
    """
    if not api_key:
        return []

    url = "https://api.anthropic.com/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                models = []
                for item in data.get("data", []):
                    model_id = item.get("id")
                    display_name = item.get("display_name", model_id)

                    models.append(
                        {
                            "id": f"anthropic/{model_id}",
                            "name": display_name,
                            "provider": "anthropic",
                        }
                    )
                return models
            else:
                logger.warning(
                    f"Anthropic API list failed ({response.status_code}), using static list."
                )
                return []
    except Exception as e:
        logger.error(f"Error fetching Anthropic models: {e}")
        return []


def get_api_key_for_model(model: str) -> Optional[str]:
    """
    Resolve the correct API key from environment variables based on the model name.
    """
    if not model:
        return None

    if model.startswith("gemini/") or model.startswith("google/"):
        return os.getenv("GEMINI_API_KEY")
    elif model.startswith("openai/"):
        return os.getenv("OPENAI_API_KEY")
    elif model.startswith("anthropic/"):
        return os.getenv("ANTHROPIC_API_KEY")
    elif model.startswith("xai/"):
        return os.getenv("XAI_API_KEY")
    elif model.startswith("deepseek/"):
        return os.getenv("DEEPSEEK_API_KEY")
    elif model.startswith("qwen/") or model.startswith("qwen-"):
        return os.getenv("DASHSCOPE_API_KEY")
    elif model.startswith("nvidia/"):
        return os.getenv("NVIDIA_API_KEY")

    # Fallback to any available key in a specific order
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("NVIDIA_API_KEY")
    )


def resolve_provider_config(model: str, default_base_url: Optional[str] = None) -> dict:
    """
    Resolve model, base_url, api_key, and custom_llm_provider for LiteLLM.
    """

    from config import load_config
    cfg = load_config()

    normalized_model = (model or "").strip()
    if normalized_model and "/" not in normalized_model and normalized_model.startswith("qwen-"):
        normalized_model = f"qwen/{normalized_model}"
    normalized_model = MODEL_ALIASES.get(normalized_model, normalized_model)

    api_key = get_api_key_for_model(normalized_model)
    base_url = default_base_url
    custom_llm_provider = None
    target_model = normalized_model

    if hasattr(cfg.llm, "proxy_url") and cfg.llm.proxy_url:
        base_url = cfg.llm.proxy_url
    
    if normalized_model.startswith("nvidia/"):
        base_url = "https://integrate.api.nvidia.com/v1"
        target_model = normalized_model.removeprefix("nvidia/")
        custom_llm_provider = "openai"
    elif normalized_model.startswith("xai/"):
        base_url = "https://api.x.ai/v1"
        target_model = normalized_model.removeprefix("xai/")
        custom_llm_provider = "openai"
    elif normalized_model.startswith("qwen/"):
        if not base_url:
            base_url = os.getenv("DASHSCOPE_BASE_URL") or QWEN_COMPAT_BASE_URLS[0]
        target_model = normalized_model.removeprefix("qwen/")
        custom_llm_provider = "openai"
    elif normalized_model.startswith("gemini/"):
        target_model = normalized_model.removeprefix("gemini/")
        custom_llm_provider = "gemini"
    elif normalized_model.startswith("openai/"):
        target_model = normalized_model.removeprefix("openai/")
    elif normalized_model.startswith("anthropic/"):
        target_model = normalized_model.removeprefix("anthropic/")
    elif normalized_model.startswith("deepseek/"):
        target_model = normalized_model.removeprefix("deepseek/")

    return {
        "model": target_model,
        "base_url": base_url,
        "api_key": api_key,
        "custom_llm_provider": custom_llm_provider,
    }
