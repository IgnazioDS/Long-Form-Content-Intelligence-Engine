from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections.abc import Iterable
from typing import Any, cast

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from packages.shared_db.observability.metrics import (
    record_llm_chat_error,
    record_llm_chat_request,
    record_llm_chat_tokens,
)
from packages.shared_db.settings import settings

_client: OpenAI | None = None
_CHUNK_ID_RE = re.compile(r"\[CHUNK ([0-9a-fA-F-]{36})\]")
_FAKE_INSUFFICIENT_HINTS = (
    "publication date",
    "published",
    "publication",
    "author",
    "authored",
    "company",
    "publisher",
)


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


def _fake_embedding(text: str, dim: int | None = None) -> list[float]:
    if dim is None:
        dim = settings.embed_dim
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


def _extract_question(payload: str) -> str:
    marker = "Question:"
    context_marker = "Context:"
    if marker not in payload:
        return ""
    remainder = payload.split(marker, 1)[1]
    if context_marker in remainder:
        return remainder.split(context_marker, 1)[0].strip()
    return remainder.strip()


def _should_fake_insufficient(question: str) -> bool:
    lowered = question.lower()
    return any(hint in lowered for hint in _FAKE_INSUFFICIENT_HINTS)


def _fake_chat(messages: list[dict[str, str]]) -> str:
    user_content = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            user_content = str(message.get("content", ""))
            break
    question = _extract_question(user_content)
    if question and _should_fake_insufficient(question):
        payload = {
            "answer": "insufficient evidence",
            "citations": [],
            "follow_ups": ["Ask about a specific section or provide more detail."],
        }
        return json.dumps(payload)
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
    payload = {"answer": answer, "citations": chunk_ids, "follow_ups": []}
    return json.dumps(payload)


def _model_label(provider: str) -> str:
    if provider == "openai":
        return settings.openai_model
    if provider == "fake":
        return "fake"
    return provider or "unknown"


def _extract_usage_tokens(usage: Any) -> tuple[int | None, int | None, int | None]:
    if usage is None:
        return None, None, None
    if isinstance(usage, dict):
        return (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
    return (
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        getattr(usage, "total_tokens", None),
    )


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(3))
def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    expected_dim = settings.embed_dim
    if expected_dim <= 0:
        raise ValueError("EMBED_DIM must be a positive integer.")
    provider = _provider()
    if provider == "fake":
        embeddings = _fake_embeddings(texts)
        if any(len(item) != expected_dim for item in embeddings):
            raise ValueError("Fake embeddings did not match EMBED_DIM.")
        return embeddings
    if provider != "openai":
        raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
    client = get_client()
    response = client.embeddings.create(model=settings.openai_embed_model, input=list(texts))
    embeddings = [item.embedding for item in response.data]
    for idx, embedding in enumerate(embeddings):
        if len(embedding) != expected_dim:
            raise ValueError(
                f"Embedding dimension mismatch at index {idx}: "
                f"expected {expected_dim}, got {len(embedding)}."
            )
    return embeddings


def chat(
    messages: list[dict[str, str]],
    response_format: dict | None = None,
    temperature: float = 0,
) -> str:
    provider = _provider()
    model = _model_label(provider)
    start = time.perf_counter()
    try:
        if provider == "fake":
            content = _fake_chat(messages)
            duration = time.perf_counter() - start
            record_llm_chat_request(provider, model, "ok", duration)
            return content
        if provider != "openai":
            raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
        client = get_client()
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=cast(Any, messages),
            temperature=temperature,
            response_format=cast(Any, response_format),
        )
        content = response.choices[0].message.content or ""
        duration = time.perf_counter() - start
        record_llm_chat_request(provider, model, "ok", duration)
        prompt_tokens, completion_tokens, total_tokens = _extract_usage_tokens(
            getattr(response, "usage", None)
        )
        record_llm_chat_tokens(
            provider,
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        return content
    except Exception as exc:
        duration = time.perf_counter() - start
        record_llm_chat_request(provider, model, "error", duration)
        record_llm_chat_error(provider, model, type(exc).__name__)
        raise
