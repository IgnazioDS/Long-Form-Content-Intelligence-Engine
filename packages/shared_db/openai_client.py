from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable
from typing import Any, cast

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from packages.shared_db.settings import settings

_client: OpenAI | None = None
_FAKE_EMBED_DIM = 1536
_CHUNK_ID_RE = re.compile(r"\[CHUNK ([0-9a-fA-F-]{36})\]")


def _provider() -> str:
    provider = settings.ai_provider.strip().lower()
    return provider or "openai"


def get_client() -> OpenAI:
    if _provider() != "openai":
        raise RuntimeError("OpenAI client requested while AI_PROVIDER is not openai")
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def _fake_embedding(text: str, dim: int = _FAKE_EMBED_DIM) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


def _fake_embeddings(texts: Iterable[str]) -> list[list[float]]:
    return [_fake_embedding(text) for text in texts]


def _extract_chunks(payload: str) -> tuple[list[str], list[str]]:
    matches = list(_CHUNK_ID_RE.finditer(payload))
    chunk_ids: list[str] = []
    chunk_texts: list[str] = []
    for index, match in enumerate(matches):
        chunk_ids.append(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        block = payload[start:end].strip()
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].startswith("Source:"):
            lines = lines[1:]
        chunk_texts.append(" ".join(lines).strip())
    return chunk_ids, chunk_texts


def _fake_chat(messages: list[dict[str, str]]) -> str:
    user_content = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            user_content = str(message.get("content", ""))
            break
    chunk_ids, chunk_texts = _extract_chunks(user_content)
    if not chunk_ids:
        payload = {
            "answer": "insufficient evidence",
            "citations": [],
            "follow_ups": ["Ask about a specific section or provide more detail."],
        }
        return json.dumps(payload)
    text = chunk_texts[0]
    if text:
        words = text.split()
        snippet = " ".join(words[:40])
        answer = snippet
    else:
        answer = "Based on the provided context, the document contains relevant information."
    payload = {"answer": answer, "citations": [chunk_ids[0]], "follow_ups": []}
    return json.dumps(payload)


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(3))
def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    provider = _provider()
    if provider == "fake":
        return _fake_embeddings(texts)
    if provider != "openai":
        raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
    client = get_client()
    response = client.embeddings.create(model=settings.openai_embed_model, input=list(texts))
    return [item.embedding for item in response.data]


def chat(
    messages: list[dict[str, str]],
    response_format: dict | None = None,
    temperature: float = 0,
) -> str:
    provider = _provider()
    if provider == "fake":
        return _fake_chat(messages)
    if provider != "openai":
        raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
    client = get_client()
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=cast(Any, messages),
        temperature=temperature,
        response_format=cast(Any, response_format),
    )
    return response.choices[0].message.content or ""
