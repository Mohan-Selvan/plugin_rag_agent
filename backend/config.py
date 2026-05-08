"""Typed configuration loaded from YAML + .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# Strip blank-string overrides for env vars where the SDK treats "" as an
# explicit setting (e.g. OPENAI_BASE_URL="" makes httpx reject every request).
for _k in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_BASE_URL"):
    if os.environ.get(_k) == "":
        del os.environ[_k]


class AgentConfig(BaseModel):
    """Chat-agent LLM settings + system prompt."""
    model: str
    temperature: float = 0.2
    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str


class IngestionConfig(BaseModel):
    """LLM and chunking parameters used by the ingestion script."""
    model: str
    temperature: float = 0.0
    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    max_chunk_chars: int = 1500
    chunk_overlap_chars: int = 150
    restructure: bool = True


class KnowledgeBaseConfig(BaseModel):
    """Where to find the source markdowns."""
    data_folder: Path = Path("./data")


class EmbeddingsConfig(BaseModel):
    """Embeddings model used for both ingestion and query."""
    model: str


class VectorStoreConfig(BaseModel):
    """Qdrant collection + retrieval params + agent-facing tool metadata."""
    collection: str = "plugin_rag"
    top_k: int = 5
    score_threshold: float | None = None
    tool_name: str = "knowledge_base_search"
    tool_description: str


class WidgetConfig(BaseModel):
    """Branding and starter chips shown by the floating widget."""
    title: str = "Assistant"
    subtitle: str = ""
    greeting: str = "Hi! How can I help today?"
    primary_color: str = "#6366f1"
    secondary_color: str | None = None  # Gradient endpoint; defaults to a darker primary
    tertiary_color: str | None = None  # Accent (pulse ring, chip hover); defaults to primary
    position: str = "bottom-right"
    starter_questions: list[str] = []


class SessionsConfig(BaseModel):
    """Persistent SQLite session store settings."""
    sqlite_path: Path = Path("./storage/sessions.sqlite")
    history_window: int = 8
    ttl_hours: int = 72


class ApiConfig(BaseModel):
    """FastAPI bind + CORS settings."""
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]


class RateLimitConfig(BaseModel):
    """Per-IP rate limiting (slowapi)."""
    enabled: bool = True
    requests_per_minute: int = 12


class SuggestionsConfig(BaseModel):
    """Follow-up question chips emitted after each AI reply."""
    enabled: bool = True
    max_items: int = 3


class LoggingConfig(BaseModel):
    """Rotating file logger settings."""
    file: Path = Path("./storage/plugin_rag.log")
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


class AppConfig(BaseModel):
    """Top-level config container."""
    agent: AgentConfig
    ingestion: IngestionConfig
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    embeddings: EmbeddingsConfig
    vector_store: VectorStoreConfig
    widget: WidgetConfig = Field(default_factory=WidgetConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    suggestions: SuggestionsConfig = Field(default_factory=SuggestionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class RuntimeEnv(BaseModel):
    """Values pulled from the process environment (.env loaded above)."""
    qdrant_url: str = "http://localhost:6333"
    ollama_host: str = "http://localhost:11434"
    log_level: str = "INFO"
    trust_proxy: bool = False

    @classmethod
    def from_env(cls) -> "RuntimeEnv":
        return cls(qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
                   ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                   log_level=os.getenv("LOG_LEVEL", "INFO"),
                   trust_proxy=os.getenv("TRUST_PROXY", "false").strip().lower() in ("1", "true", "yes", "on"))


def _config_path() -> Path:
    return Path(os.getenv("PLUGIN_RAG_CONFIG", "./config/config.yaml"))


def load_config(path: Path | None = None) -> AppConfig:
    """Load and validate the YAML config; raises if the file is missing."""
    p = path or _config_path()
    if not p.exists(): raise FileNotFoundError(f"Config file not found at {p}")
    return AppConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached AppConfig."""
    return load_config()


@lru_cache(maxsize=1)
def get_env() -> RuntimeEnv:
    """Process-wide cached RuntimeEnv."""
    return RuntimeEnv.from_env()
