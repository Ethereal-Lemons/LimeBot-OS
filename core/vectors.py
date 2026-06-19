import asyncio
import hashlib
import os
import re
import time as _time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from config import load_config


@dataclass(frozen=True)
class EmbeddingCandidate:
    model: str
    source: str


class VectorService:
    """
    Service for semantic search using LanceDB and litellm embeddings.
    Supports multiple LLM providers (Gemini, OpenAI, Anthropic, xAI,
    DeepSeek, NVIDIA, Qwen, Ollama) via litellm.
    """

    PROVIDER_EMBEDDING_MODELS = {
        "gemini": "gemini/gemini-embedding-001",
        "vertex_ai": "gemini/gemini-embedding-001",
        "openai": "text-embedding-3-small",
        "azure": "azure/text-embedding-3-small",
        "nvidia": "nvidia_nim/NV-Embed-v2",
        "deepseek": "gemini/gemini-embedding-001",
        "anthropic": "gemini/gemini-embedding-001",
        "xai": "gemini/gemini-embedding-001",
        "qwen": "qwen/text-embedding-v4",
        "ollama": "ollama/nomic-embed-text",
        "local": "ollama/nomic-embed-text",
        "openrouter": "openrouter/openai/text-embedding-3-small",
        "moonshot": "moonshot/moonshot-embed-v1",
    }

    PROVIDER_API_KEYS = {
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "vertex_ai": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "azure": ["OPENAI_API_KEY"],
        "nvidia": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
        "deepseek": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "anthropic": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "xai": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "qwen": ["DASHSCOPE_API_KEY"],
        "ollama": [],
        "local": [],
        "openrouter": ["OPENROUTER_API_KEY"],
        "moonshot": ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
    }

    LEGACY_MODEL_ALIASES = {
        # Some older configs/UI values store this without provider prefix.
        # LiteLLM requires either a provider-prefixed model OR custom_llm_provider.
        "text-embedding-v4": "qwen/text-embedding-v4",
        "nvidia/NV-Embed-v2": "nvidia_nim/NV-Embed-v2",
    }
    DISABLED_MODEL_VALUES = {"", "off", "none", "disabled", "false"}

    _KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b")
    _NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(
        self,
        db_path: str = "data/vectors",
        model: str = "gemini/gemini-embedding-001",
        candidate_models: Optional[List[EmbeddingCandidate]] = None,
    ):
        self.db_path = db_path
        self.db = None
        self.table = None
        self.table_name = "memories"
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._disabled = False
        self._failed = False
        self._config = None

        default_candidate = EmbeddingCandidate(
            model=self._normalize_model_id(model),
            source="default",
        )
        self.candidate_models = self._normalize_candidate_models(
            candidate_models or [default_candidate]
        )
        self.model = self.candidate_models[0].model
        self._active_candidate_model: Optional[str] = None
        self._failed_candidate_models: Dict[str, str] = {}

        # Keyed by a model+text SHA-256 so we never reuse vectors across
        # embedding spaces when the initial candidate falls through.
        self._emb_cache: Dict[str, Any] = {}
        self._EMB_CACHE_TTL = 300.0
        self._EMB_CACHE_MAX = 256

        Path(db_path).parent.mkdir(exist_ok=True, parents=True)

    async def _ensure_init(self):
        """Initialize connection to LanceDB."""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            try:
                import lancedb

                self.db = await asyncio.to_thread(lancedb.connect, self.db_path)
                table_names = await asyncio.to_thread(self.db.table_names)

                if self.table_name in table_names:
                    self.table = await asyncio.to_thread(
                        self.db.open_table, self.table_name
                    )
                else:
                    logger.debug(
                        f"Vector table '{self.table_name}' not found yet; "
                        "it will be created on first memory write."
                    )
                self._initialized = True

            except Exception as e:
                logger.error(f"Failed to initialize LanceDB: {e}")
                self._failed = True

    @classmethod
    def _is_disabled_model(cls, model: Optional[str]) -> bool:
        return str(model or "").strip().lower() in cls.DISABLED_MODEL_VALUES

    @classmethod
    def _normalize_model_id(cls, model: Optional[str]) -> str:
        normalized = str(model or "").strip()
        if cls._is_disabled_model(normalized):
            return "disabled"
        return cls.LEGACY_MODEL_ALIASES.get(normalized, normalized)

    @classmethod
    def _normalize_candidate_models(
        cls, candidate_models: List[EmbeddingCandidate]
    ) -> List[EmbeddingCandidate]:
        normalized: List[EmbeddingCandidate] = []
        seen: set[str] = set()

        for candidate in candidate_models:
            model = cls._normalize_model_id(candidate.model)
            if not model or model in seen:
                continue
            normalized.append(EmbeddingCandidate(model=model, source=candidate.source))
            seen.add(model)

        if not normalized:
            normalized.append(EmbeddingCandidate(model="disabled", source="fallback"))
        elif normalized[-1].model != "disabled":
            normalized.append(
                EmbeddingCandidate(model="disabled", source="lexical_fallback")
            )

        return normalized

    @classmethod
    def _get_provider_for_model(cls, model: Optional[str]) -> str:
        """Infer provider keyword from an embedding model string."""
        normalized = cls._normalize_model_id(model).lower()
        if cls._is_disabled_model(normalized):
            return "disabled"
        if "openrouter/" in normalized or normalized.startswith("openrouter/"):
            return "openrouter"
        if "moonshot/" in normalized or normalized.startswith("moonshot/"):
            return "moonshot"
        for provider in cls.PROVIDER_API_KEYS:
            if provider in normalized:
                return provider
        if normalized == "text-embedding-v4":
            return "qwen"
        if normalized.startswith("text-embedding-"):
            return "openai"
        return "gemini"

    def _get_provider(self) -> str:
        return self._get_provider_for_model(self.model)

    @classmethod
    def _resolve_api_key_for_model(cls, cfg, model: Optional[str]) -> Optional[str]:
        provider = cls._get_provider_for_model(model)
        if provider == "disabled":
            return None

        env_vars = cls.PROVIDER_API_KEYS.get(provider, [])
        for var in env_vars:
            value = os.getenv(var)
            if value:
                return value

        if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
            return os.getenv("GEMINI_API_KEY")

        if provider == "openai":
            return None

        llm_cfg = getattr(cfg, "llm", None)
        if provider not in ("gemini", "openai", "nvidia", "qwen") and llm_cfg:
            return getattr(llm_cfg, "api_key", None) or None

        return None

    def _resolve_api_key(self, cfg, model: Optional[str] = None) -> Optional[str]:
        """Resolve the correct API key for an embedding model/provider."""
        return self._resolve_api_key_for_model(cfg, model or self.model)

    @classmethod
    def _can_attempt_model(cls, model: Optional[str], cfg) -> bool:
        provider = cls._get_provider_for_model(model)
        if provider == "disabled":
            return False
        if provider in ("ollama", "local"):
            return True
        return cls._resolve_api_key_for_model(cfg, model) is not None

    @classmethod
    def _default_embedding_model_for_chat_model(
        cls, chat_model: Optional[str]
    ) -> Optional[str]:
        normalized = str(chat_model or "").strip().lower()
        if not normalized or normalized.startswith("openai-codex/"):
            return None
        if "openrouter/" in normalized or normalized.startswith("openrouter/"):
            return cls.PROVIDER_EMBEDDING_MODELS["openrouter"]
        if "moonshot/" in normalized or normalized.startswith("moonshot/"):
            return cls.PROVIDER_EMBEDDING_MODELS["moonshot"]
        for provider, embedding_model in cls.PROVIDER_EMBEDDING_MODELS.items():
            if provider in normalized:
                return embedding_model
        return None

    @classmethod
    def build_candidate_models(cls, config=None) -> List[EmbeddingCandidate]:
        candidates: List[EmbeddingCandidate] = []
        seen: set[str] = set()

        def add_candidate(model: Optional[str], source: str) -> None:
            normalized = cls._normalize_model_id(model)
            if not normalized or normalized in seen:
                return
            candidates.append(EmbeddingCandidate(model=normalized, source=source))
            seen.add(normalized)

        llm_cfg = getattr(config, "llm", None) if config is not None else None
        explicit_model = (
            str(getattr(llm_cfg, "embedding_model", "") or "").strip()
            if llm_cfg is not None
            else ""
        )
        if explicit_model:
            add_candidate(explicit_model, "explicit")
            if cls._is_disabled_model(explicit_model):
                return cls._normalize_candidate_models(candidates)

        chat_model = getattr(llm_cfg, "model", "") if llm_cfg is not None else ""
        default_model = cls._default_embedding_model_for_chat_model(chat_model)
        if default_model and (config is None or cls._can_attempt_model(default_model, config)):
            add_candidate(default_model, "chat_provider_default")

        gemini_model = cls.PROVIDER_EMBEDDING_MODELS["gemini"]
        if config is None or cls._can_attempt_model(gemini_model, config):
            add_candidate(gemini_model, "gemini_fallback")

        allow_local_fallback = bool(
            getattr(llm_cfg, "embedding_allow_local_fallback", False)
        )
        explicit_provider = cls._get_provider_for_model(explicit_model) if explicit_model else ""
        if allow_local_fallback or explicit_provider in ("ollama", "local"):
            add_candidate(cls.PROVIDER_EMBEDDING_MODELS["ollama"], "local_fallback")

        add_candidate("disabled", "lexical_fallback")
        return cls._normalize_candidate_models(candidates)

    @classmethod
    def _sanitize_error_message(cls, message: str) -> str:
        """Redact API-key-like secrets from provider/library exception text."""
        if not message:
            return message
        sanitized = cls._KEY_RE.sub("sk-***REDACTED***", message)
        sanitized = re.sub(
            r"(Incorrect API key provided:\s*)([^,\s]+)",
            r"\1***REDACTED***",
            sanitized,
            flags=re.IGNORECASE,
        )
        return sanitized

    @classmethod
    def _is_auth_error(cls, message: str) -> bool:
        lower = (message or "").lower()
        needles = (
            "invalid_api_key",
            "incorrect api key",
            "authenticationerror",
            "unauthorized",
            "error code: 401",
            "'status': 401",
            '"status": 401',
        )
        return any(n in lower for n in needles)

    @classmethod
    def _is_rate_limit_error(cls, message: str) -> bool:
        lower = (message or "").lower()
        needles = (
            "ratelimiterror",
            "rate limit",
            "rate_limit",
            "insufficient_quota",
            "exceeded your current quota",
            "error code: 429",
            "'status': 429",
            '"status": 429',
        )
        return any(n in lower for n in needles)

    @classmethod
    def _candidate_failure_reason(cls, message: str) -> Optional[str]:
        if cls._is_auth_error(message):
            return "auth"
        if cls._is_rate_limit_error(message):
            return "rate_limit"
        if any(
            err in message
            for err in (
                "DefaultCredentialsError",
                "APIConnectionError",
                "ServiceUnavailableError",
                "TimeoutError",
                "ConnectionError",
                "NotFoundError",
                "404",
                "GeminiException",
                "timed out",
                "timeout",
            )
        ):
            return "unavailable"
        return None

    def _get_candidate(self, model: Optional[str]) -> Optional[EmbeddingCandidate]:
        normalized = self._normalize_model_id(model)
        return next(
            (candidate for candidate in self.candidate_models if candidate.model == normalized),
            None,
        )

    def _iter_semantic_candidates(self, cfg) -> List[EmbeddingCandidate]:
        if self._active_candidate_model:
            if self._active_candidate_model in self._failed_candidate_models:
                return []
            active_candidate = self._get_candidate(self._active_candidate_model)
            if active_candidate and self._can_attempt_model(active_candidate.model, cfg):
                return [active_candidate]
            return []

        available: List[EmbeddingCandidate] = []
        for candidate in self.candidate_models:
            if self._is_disabled_model(candidate.model):
                continue
            if candidate.model in self._failed_candidate_models:
                continue
            if not self._can_attempt_model(candidate.model, cfg):
                continue
            available.append(candidate)
        return available

    def has_semantic_candidate(self) -> bool:
        if self._failed:
            return False
        if not self._config:
            self._config = load_config()
        if self._is_disabled_model(self.model) and not self._active_candidate_model:
            return False
        return bool(self._iter_semantic_candidates(self._config))

    def get_embedding_status(self) -> Dict[str, Any]:
        if not self._config:
            self._config = load_config()
        return {
            "active_model": self._active_candidate_model or self.model,
            "candidate_models": [candidate.model for candidate in self.candidate_models],
            "semantic_enabled": self.has_semantic_candidate(),
            "failed_candidates": sorted(self._failed_candidate_models.keys()),
            "fallback": "grep",
        }

    @staticmethod
    def _cache_key_for_model(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()

    def _build_embedding_kwargs(self, cfg, model: str) -> Dict[str, Any]:
        provider = self._get_provider_for_model(model)
        api_key = self._resolve_api_key(cfg, model=model)
        resolved_model = self.LEGACY_MODEL_ALIASES.get(model, model)
        if resolved_model.startswith("openai-codex/"):
            resolved_model = resolved_model.removeprefix("openai-codex/")

        kwargs = {
            "model": resolved_model,
            "input": [],
            "api_key": api_key,
        }
        if provider in ("ollama", "local"):
            kwargs["base_url"] = (
                getattr(cfg.llm, "base_url", None)
                or os.getenv("OLLAMA_HOST")
                or os.getenv("OLLAMA_BASE_URL")
            )
        if provider == "nvidia":
            kwargs["model"] = (
                resolved_model.removeprefix("nvidia_nim/").removeprefix("nvidia/")
            )
            kwargs["base_url"] = (
                getattr(cfg.llm, "base_url", None) or self._NVIDIA_BASE_URL
            )
            kwargs["custom_llm_provider"] = "nvidia_nim"
        if provider == "qwen":
            kwargs["model"] = model.removeprefix("qwen/")
            kwargs["base_url"] = (
                getattr(cfg.llm, "base_url", None)
                or os.getenv("DASHSCOPE_BASE_URL")
                or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            )
            kwargs["custom_llm_provider"] = "openai"
        return kwargs

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using litellm (supports multiple providers)."""
        if self._failed:
            return None

        if not self._config:
            self._config = load_config()
        cfg = self._config

        semantic_candidates = self._iter_semantic_candidates(cfg)
        if not semantic_candidates:
            self._disabled = True
            return None

        from litellm import embedding

        for candidate in semantic_candidates:
            cache_key = self._cache_key_for_model(candidate.model, text)
            hit = self._emb_cache.get(cache_key)
            if hit and (_time.monotonic() - hit["ts"]) < self._EMB_CACHE_TTL:
                self.model = candidate.model
                if self._active_candidate_model is None:
                    self._active_candidate_model = candidate.model
                return hit["vec"]

            try:
                kwargs = self._build_embedding_kwargs(cfg, candidate.model)
                kwargs["input"] = [text]
                response = await asyncio.to_thread(embedding, **kwargs)
                vec = response.data[0]["embedding"]

                if len(self._emb_cache) >= self._EMB_CACHE_MAX:
                    oldest = min(
                        self._emb_cache, key=lambda key: self._emb_cache[key]["ts"]
                    )
                    del self._emb_cache[oldest]
                self._emb_cache[cache_key] = {"vec": vec, "ts": _time.monotonic()}

                self._active_candidate_model = candidate.model
                self.model = candidate.model
                self._disabled = False
                return vec
            except Exception as e:
                msg = self._sanitize_error_message(str(e))
                failure_reason = self._candidate_failure_reason(msg)
                if not failure_reason:
                    logger.error(f"Embedding generation failed: {msg}")
                    raise

                self._failed_candidate_models[candidate.model] = failure_reason
                if self._active_candidate_model == candidate.model:
                    self._disabled = True
                    logger.warning(
                        f"Active embedding model '{candidate.model}' failed ({failure_reason}). "
                        "Disabling semantic vector search for this session and using keyword fallback."
                    )
                    return None

                logger.warning(
                    f"Embedding candidate '{candidate.model}' unavailable ({failure_reason}). "
                    "Trying the next candidate."
                )

        if not self._iter_semantic_candidates(cfg):
            self._disabled = True
        return None

    async def add_entry(
        self, text: str, category: str = "other", metadata: Dict[str, Any] = None
    ):
        """Add a text entry to the vector database."""
        if self._failed or not self.has_semantic_candidate():
            return

        await self._ensure_init()
        if self._failed:
            return

        try:
            vector = await self._get_embedding(text)
            if vector is None:
                return

            import uuid

            data = [
                {
                    "id": str(uuid.uuid4()),
                    "text": text,
                    "vector": vector,
                    "category": category,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": str(metadata or {}),
                }
            ]

            if self.table:
                await asyncio.to_thread(self.table.add, data)
            else:
                self.table = await asyncio.to_thread(
                    self.db.create_table, self.table_name, data=data
                )
                self._initialized = True

            logger.info(f"Added entry to vector memory: {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed to add vector entry: {e}")

    async def search_semantic(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search only the semantic vector store without lexical fallback."""
        if not self.has_semantic_candidate():
            return []

        await self._ensure_init()
        if not self.table:
            return []

        try:
            vector = await self._get_embedding(query)
            if vector is None:
                return []

            results = await asyncio.to_thread(
                lambda: self.table.search(vector).limit(limit).to_list()
            )

            normalized = []
            for row in results:
                if isinstance(row, dict):
                    normalized.append(row)
                    continue

                row_dict = {
                    key: getattr(row, key, None)
                    for key in [
                        "id",
                        "text",
                        "category",
                        "timestamp",
                        "metadata",
                        "score",
                    ]
                }
                if "score" not in row_dict and hasattr(row, "_distance"):
                    row_dict["score"] = 1.0 - (getattr(row, "_distance", 0) / 2.0)
                normalized.append(row_dict)

            return normalized
        except Exception as e:
            sanitized = self._sanitize_error_message(str(e))
            logger.error(f"Semantic vector search failed: {sanitized}")
            return []

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for similar text entries. Falls back to keyword search if needed."""
        if not self.has_semantic_candidate():
            logger.info("Semantic search unavailable, using keyword fallback.")
            return await self.search_grep(query, limit)

        semantic_results = await self.search_semantic(query, limit)
        if semantic_results:
            return semantic_results

        logger.info("Semantic search missed or failed, using keyword fallback.")
        return await self.search_grep(query, limit)

    @property
    def is_enabled(self) -> bool:
        if self._failed:
            return False
        return self.has_semantic_candidate()

    async def get_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent entries from the vector database."""
        if not self.has_semantic_candidate():
            return []
        await self._ensure_init()
        if not self.table:
            return []
        try:
            results = await asyncio.to_thread(lambda: self.table.to_arrow().to_pylist())
            return results[:limit]
        except Exception as e:
            logger.error(f"Failed to get all vector entries: {e}")
            return []

    async def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry from the vector database by ID."""
        await self._ensure_init()
        if not self.table:
            return False
        try:
            await asyncio.to_thread(self.table.delete, f"id = '{entry_id}'")
            return True
        except Exception as e:
            logger.error(f"Failed to delete vector entry {entry_id}: {e}")
            return False

    _grep_cache: Dict[str, Any] = {}
    _GREP_CACHE_TTL = 30.0

    async def search_grep(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fallback search using keyword/grep scan of memory files.
        Useful when embeddings are unavailable or for exact matches.
        """
        cache_key = f"{query.lower().strip()}:{limit}"
        cached = self._grep_cache.get(cache_key)
        if cached and (_time.monotonic() - cached["ts"]) < self._GREP_CACHE_TTL:
            return cached["results"]

        keywords = [keyword.lower() for keyword in query.split() if len(keyword) > 3]
        if not keywords:
            return []

        def _scan_files() -> List[Dict[str, Any]]:
            results = []

            memory_dir = Path("persona/memory")
            if not memory_dir.exists():
                memory_dir = Path(__file__).parent.parent / "persona" / "memory"

            if not memory_dir.exists():
                return []

            for file_path in memory_dir.glob("*.md"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                for line in content.splitlines():
                    line_lower = line.lower()
                    score = sum(1 for keyword in keywords if keyword in line_lower)

                    if score > 0:
                        results.append(
                            {
                                "text": line.strip(),
                                "score": score,
                                "source": file_path.name,
                                "timestamp": file_path.stem,
                            }
                        )

            results.sort(key=lambda item: item["score"], reverse=True)
            return results[:limit]

        try:
            final = await asyncio.to_thread(_scan_files)
            self._grep_cache[cache_key] = {"results": final, "ts": _time.monotonic()}
            return final
        except Exception as e:
            logger.error(f"Grep search failed: {e}")
            return []


_instance: Optional[VectorService] = None


def get_vector_service(config=None) -> VectorService:
    """
    Returns the singleton VectorService instance.

    Candidate resolution priority:
      1. config.llm.embedding_model (explicit override, tried first)
      2. Provider-compatible default for config.llm.model when credentials exist
      3. Gemini fallback when Gemini/Google credentials exist
      4. Optional local Ollama fallback when enabled
      5. Disabled lexical-only fallback
    """
    global _instance

    candidate_models = VectorService.build_candidate_models(config)
    model = candidate_models[0].model
    candidate_signature = tuple(
        (candidate.model, candidate.source) for candidate in candidate_models
    )

    if _instance is not None:
        instance_signature = tuple(
            (candidate.model, candidate.source)
            for candidate in getattr(_instance, "candidate_models", [])
        )
        if config and instance_signature != candidate_signature:
            logger.info(
                "Re-initializing Vector service with new embedding candidates: "
                f"{[candidate.model for candidate in candidate_models]}"
            )
            _instance = None
        else:
            return _instance

    if _instance is None:
        logger.info(
            "Vector service embedding candidates: "
            f"{[candidate.model for candidate in candidate_models]}"
        )
        _instance = VectorService(model=model, candidate_models=candidate_models)
        if config:
            _instance._config = config
    return _instance
