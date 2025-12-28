from __future__ import annotations

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
    api_key: str = Field("", alias="API_KEY")
    rate_limit_rps: float = Field(0.0, alias="RATE_LIMIT_RPS")
    rate_limit_burst: int = Field(0, alias="RATE_LIMIT_BURST")
    log_level: str = Field("INFO", alias="LOG_LEVEL")


settings = Settings()  # type: ignore[call-arg]
