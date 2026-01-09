from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ai_provider: str = Field("openai", alias="AI_PROVIDER")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embed_model: str = Field("text-embedding-3-small", alias="OPENAI_EMBED_MODEL")
    database_url: str = Field(
        "postgresql+psycopg://postgres:postgres@localhost:5432/lfcie",
        alias="DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    debug: bool = Field(False, alias="DEBUG")
    max_chunks_per_query: int = Field(8, alias="MAX_CHUNKS_PER_QUERY")
    per_source_retrieval_limit: int = Field(10, alias="PER_SOURCE_RETRIEVAL_LIMIT")
    rerank_enabled: bool = Field(True, alias="RERANK_ENABLED")
    rerank_candidates: int = Field(30, alias="RERANK_CANDIDATES")
    rerank_snippet_chars: int = Field(900, alias="RERANK_SNIPPET_CHARS")
    mmr_enabled: bool = Field(True, alias="MMR_ENABLED")
    mmr_lambda: float = Field(0.7, alias="MMR_LAMBDA")
    mmr_candidates: int = Field(30, alias="MMR_CANDIDATES")
    chunk_char_target: int = Field(5000, alias="CHUNK_CHAR_TARGET")
    chunk_char_overlap: int = Field(800, alias="CHUNK_CHAR_OVERLAP")
    max_pdf_bytes: int = Field(25_000_000, alias="MAX_PDF_BYTES")
    max_pdf_pages: int = Field(300, alias="MAX_PDF_PAGES")
    max_url_bytes: int = Field(2_000_000, alias="MAX_URL_BYTES")
    embed_batch_size: int = Field(64, alias="EMBED_BATCH_SIZE")
    api_key: str = Field("", alias="API_KEY")
    require_api_key: bool = Field(False, alias="REQUIRE_API_KEY")
    rate_limit_backend: str = Field("memory", alias="RATE_LIMIT_BACKEND")
    rate_limit_rps: float = Field(0.0, alias="RATE_LIMIT_RPS")
    rate_limit_burst: int = Field(0, alias="RATE_LIMIT_BURST")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    metrics_enabled: bool = Field(True, alias="METRICS_ENABLED")
    metrics_path: str = Field("/metrics", alias="METRICS_PATH")
    otel_enabled: bool = Field(False, alias="OTEL_ENABLED")
    otel_service_name: str = Field(
        "long-form-content-intelligence-api", alias="OTEL_SERVICE_NAME"
    )
    otel_exporter_otlp_endpoint: str | None = Field(
        None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    cors_origins: str = Field(
        "http://localhost:3000,http://127.0.0.1:3000", alias="CORS_ORIGINS"
    )
    url_allowlist: str = Field("", alias="URL_ALLOWLIST")
    storage_root: str = Field("storage", alias="STORAGE_ROOT")
    embed_dim: int = Field(1536, alias="EMBED_DIM")

    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw:
            return []
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    def url_allowlist_hosts(self) -> set[str]:
        raw = self.url_allowlist.strip()
        if not raw:
            return set()
        return {host.strip().lower() for host in raw.split(",") if host.strip()}


settings = Settings()  # type: ignore[call-arg]


def _parse_worker_count(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def detect_max_workers() -> int:
    for env_var in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = _parse_worker_count(os.getenv(env_var))
        if value is not None:
            return value
    return 1
