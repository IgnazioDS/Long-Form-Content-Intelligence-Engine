from __future__ import annotations

from typing import Iterable

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from packages.shared_db.settings import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(3))
def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    client = get_client()
    response = client.embeddings.create(model=settings.openai_embed_model, input=list(texts))
    return [item.embedding for item in response.data]
