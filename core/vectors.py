import os
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import load_config
from datetime import datetime


class VectorService:
    """
    Service for semantic search using LanceDB and litellm embeddings.
    Supports multiple LLM providers (Gemini, OpenAI, Anthropic, xAI,
    DeepSeek, NVIDIA, Ollama) via litellm.
    """

    PROVIDER_EMBEDDING_MODELS = {
        "gemini": "gemini/gemini-embedding-001",
        "vertex_ai": "gemini/gemini-embedding-001",
        "openai": "text-embedding-3-small",
        "azure": "azure/text-embedding-3-small",
        "nvidia": "nvidia_nim/nvidia/NV-Embed-v2",
        "deepseek": "gemini/gemini-embedding-001",
        "anthropic": "gemini/gemini-embedding-001",
        "xai": "gemini/gemini-embedding-001",
        "ollama": "ollama/nomic-embed-text",
        "local": "ollama/nomic-embed-text",
    }

    PROVIDER_API_KEYS = {
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "vertex_ai": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "azure": ["OPENAI_API_KEY"],
        "nvidia": ["NVIDIA_API_KEY"],
        "deepseek": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "anthropic": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "xai": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "ollama": [],
        "local": [],
    }

    def __init__(
        self, db_path: str = "data/vectors", model: str = "gemini/gemini-embedding-001"
    ):
        self.db_path = db_path
        self.model = model
        self.db = None
        self.table = None
        self.table_name = "memories"
        self._init_lock = asyncio.Lock()
        self._disabled = False
        self._failed = False
        self._config = None

        Path(db_path).parent.mkdir(exist_ok=True, parents=True)

    async def _ensure_init(self):
        """Initialize connection to LanceDB."""
        if self.table:
            return

        async with self._init_lock:
            if self.table:
                return

            try:
                import lancedb
                self.db = await asyncio.to_thread(lancedb.connect, self.db_path)

                if self.table_name in self.db.table_names():
                    self.table = self.db.open_table(self.table_name)
                else:
                    from loguru import logger
                    logger.info(f"Creating new vector table: {self.table_name}")

            except Exception as e:
                from loguru import logger
                logger.error(f"Failed to initialize LanceDB: {e}")
                self._failed = True

    def _get_provider(self) -> str:
        """Infer provider keyword from the current embedding model string."""
        model = self.model.lower()
        for provider in self.PROVIDER_API_KEYS:
            if provider in model:
                return provider
        return "gemini"

    def _resolve_api_key(self, cfg) -> Optional[str]:
        """Resolve the correct API key based on the active embedding model/provider."""
        provider = self._get_provider()
        env_vars = self.PROVIDER_API_KEYS.get(provider, [])

        for var in env_vars:
            val = os.getenv(var)
            if val:
                return val

        # If it's a Gemini or OpenAI provider, we should only return the key if it's the expected one
        # avoid falling back to unrelated model keys
        if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
             return os.getenv("GEMINI_API_KEY")
        if provider == "openai" and os.getenv("OPENAI_API_KEY"):
             return os.getenv("OPENAI_API_KEY")

        # Only fallback if providers are not explicitly restricted
        if provider not in ("gemini", "openai", "nvidia"):
            return cfg.llm.api_key or None
        
        return None

    async def _get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using litellm (supports multiple providers)."""
        if self._disabled:
            return None

        try:
            if not self._config:
                self._config = load_config()
            cfg = self._config
            api_key = self._resolve_api_key(cfg)

            provider = self._get_provider()
            if provider not in ("ollama", "local") and not api_key:
                from loguru import logger
                logger.warning(
                    f"Skipping embedding: No API key found for model '{self.model}'"
                )
                return None

            kwargs = dict(
                model=self.model,
                input=[text],
                api_key=api_key,
            )
            if provider in ("ollama", "local") and cfg.llm.base_url:
                kwargs["base_url"] = cfg.llm.base_url

            from litellm import embedding
            response = await asyncio.to_thread(embedding, **kwargs)
            return response.data[0]["embedding"]

        except Exception as e:
            msg = str(e)
            if any(
                err in msg
                for err in (
                    "DefaultCredentialsError",
                    "APIConnectionError",
                    "NotFoundError",
                    "404",
                    "GeminiException",
                )
            ):
                if not self._disabled:
                    from loguru import logger
                    logger.warning(
                        f"Embedding failed (Credentials/Model): {msg}. "
                        "Disabling vector search for this session."
                    )
                    self._disabled = True
                return None

            from loguru import logger
            logger.error(f"Embedding generation failed: {e}")
            raise

    async def add_entry(
        self, text: str, category: str = "other", metadata: Dict[str, Any] = None
    ):
        """Add a text entry to the vector database."""
        if self._failed:
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

            from loguru import logger
            logger.info(f"Added entry to vector memory: {text[:50]}...")
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to add vector entry: {e}")

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for similar text entries. Falls back to keyword search if vectors disabled."""
        if not self.is_enabled:
            from loguru import logger
            logger.info("Semantic search disabled, using keyword fallback.")
            return await self.search_grep(query, limit)

        await self._ensure_init()

        if not self.table:
            return await self.search_grep(query, limit)

        try:
            vector = await self._get_embedding(query)
            if vector is None:
                from loguru import logger
                logger.warning("Embedding failed for query, using keyword fallback.")
                return await self.search_grep(query, limit)

            results = await asyncio.to_thread(
                lambda: self.table.search(vector).limit(limit).to_list()
            )

            normalized = []
            for r in results:
                if isinstance(r, dict):
                    normalized.append(r)
                else:
                    row_dict = {
                        k: getattr(r, k, None)
                        for k in [
                            "id",
                            "text",
                            "category",
                            "timestamp",
                            "metadata",
                            "score",
                        ]
                    }
                    if "score" not in row_dict and hasattr(r, "_distance"):
                        row_dict["score"] = 1.0 - (getattr(r, "_distance", 0) / 2.0)
                    normalized.append(row_dict)
            return normalized
        except Exception as e:
            from loguru import logger
            logger.error(f"Vector search failed: {e}. Falling back to keyword search.")
            return await self.search_grep(query, limit)

    @property
    def is_enabled(self) -> bool:
        if self._disabled or self._failed:
            return False
        if not self._config:
            self._config = load_config()
        return self._resolve_api_key(
            self._config
        ) is not None or self._get_provider() in ("ollama", "local")

    async def get_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent entries from the vector database."""
        if not self.is_enabled:
            return []
        await self._ensure_init()
        if not self.table:
            return []
        try:
            results = await asyncio.to_thread(lambda: self.table.to_arrow().to_pylist())
            return results[:limit]
        except Exception as e:
            from loguru import logger
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
            from loguru import logger
            logger.error(f"Failed to delete vector entry {entry_id}: {e}")
            return False

    _grep_cache: Dict[str, Any] = {}
    _GREP_CACHE_TTL = 30.0

    async def search_grep(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fallback search using keyword/grep scan of memory files.
        Useful when embeddings are unavailable or for exact matches.
        """
        import time as _time

        cache_key = f"{query.lower().strip()}:{limit}"
        cached = self._grep_cache.get(cache_key)
        if cached and (_time.monotonic() - cached["ts"]) < self._GREP_CACHE_TTL:
            return cached["results"]

        results = []
        keywords = [k.lower() for k in query.split() if len(k) > 3]
        if not keywords:
            return []

        MEMORY_DIR = Path("persona/memory")
        if not MEMORY_DIR.exists():
            MEMORY_DIR = Path(__file__).parent.parent / "persona" / "memory"

        if not MEMORY_DIR.exists():
            return []

        try:
            for file_path in MEMORY_DIR.glob("*.md"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                for line in content.splitlines():
                    line_lower = line.lower()
                    score = sum(1 for k in keywords if k in line_lower)

                    if score > 0:
                        results.append(
                            {
                                "text": line.strip(),
                                "score": score,
                                "source": file_path.name,
                                "timestamp": file_path.stem,
                            }
                        )

            results.sort(key=lambda x: x["score"], reverse=True)
            final = results[:limit]

            self._grep_cache[cache_key] = {"results": final, "ts": _time.monotonic()}
            return final

        except Exception as e:
            from loguru import logger
            logger.error(f"Grep search failed: {e}")
            return []


_instance: Optional[VectorService] = None


def get_vector_service(config=None) -> VectorService:
    """
    Returns the singleton VectorService instance.

    Model resolution priority:
      1. config.llm.embedding_model  (explicit override — always wins)
      2. Auto-detect from config.llm.model (chat model provider → matching embedding model)
      3. Default: gemini/gemini-embedding-001
    """
    global _instance

    model = VectorService.PROVIDER_EMBEDDING_MODELS["gemini"]
    if config and hasattr(config, "llm"):
        if hasattr(config.llm, "embedding_model") and config.llm.embedding_model:
            model = config.llm.embedding_model
        else:
            chat_model = (config.llm.model or "").lower()
            for (
                provider,
                embedding_model,
            ) in VectorService.PROVIDER_EMBEDDING_MODELS.items():
                if provider in chat_model:
                    model = embedding_model
                    break

    if _instance is not None:
        if config and _instance.model != model:
            from loguru import logger
            logger.info(f"Re-initializing Vector service with new model: {model}")
            _instance = None
        else:
            return _instance

    if _instance is None:
        from loguru import logger
        logger.info(f"Vector service using embedding model: {model}")
        _instance = VectorService(model=model)
        if config:
            _instance._config = config
    return _instance
