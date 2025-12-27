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
    chunk_char_target: int = Field(5000, alias="CHUNK_CHAR_TARGET")
    chunk_char_overlap: int = Field(800, alias="CHUNK_CHAR_OVERLAP")


settings = Settings()
