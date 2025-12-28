from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.shared_db.openai_client import chat
from packages.shared_db.settings import settings

if TYPE_CHECKING:
    from apps.api.app.services.retrieval import RetrievedChunk


@dataclass
class RerankedChunk:
    chunk_id: str
    score: float
    reason: str | None = None


def _clean_snippet(text: str, max_len: int) -> str:
    cleaned = " ".join(text.split())
    if max_len <= 0 or not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _fake_score(question: str, chunk_id: str, snippet: str) -> float:
    payload = f"{question}|{chunk_id}|{snippet}"
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return (value / (1 << 64)) * 100.0


def _parse_scores(payload: Any, valid_ids: set[str]) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    scores = payload.get("scores")
    if not isinstance(scores, list):
        return {}
    parsed: dict[str, float] = {}
    for item in scores:
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("chunk_id")
        if not chunk_id or str(chunk_id) not in valid_ids:
            continue
        score = item.get("score")
        if not isinstance(score, (int, float)):
            continue
        normalized = max(0.0, min(float(score), 100.0))
        parsed[str(chunk_id)] = normalized
    return parsed


def _rerank_fake(
    question: str, chunks: list[RetrievedChunk], snippet_chars: int
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for chunk in chunks:
        snippet = _clean_snippet(chunk.text, snippet_chars)
        scores[str(chunk.chunk_id)] = _fake_score(question, str(chunk.chunk_id), snippet)
    return scores


def _rerank_openai(
    question: str, chunks: list[RetrievedChunk], snippet_chars: int
) -> dict[str, float]:
    parts: list[str] = []
    for chunk in chunks:
        snippet = _clean_snippet(chunk.text, snippet_chars)
        parts.append(f"[CHUNK {chunk.chunk_id}]\n{snippet}")

    user_prompt = (
        "Score how relevant each chunk is to the question.\n"
        "Return a JSON object with a 'scores' array of objects:\n"
        '{"chunk_id": "<uuid>", "score": 0-100}.\n'
        "Only include chunk_ids that appear below.\n\n"
        f"Question: {question}\n\n"
        "Chunks:\n"
        + "\n\n".join(parts)
    )
    content = chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a reranker. Score relevance for each chunk. "
                    "Respond with JSON only."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    valid_ids = {str(chunk.chunk_id) for chunk in chunks}
    return _parse_scores(payload, valid_ids)


def rerank_chunks(
    question: str,
    chunks: list[RetrievedChunk],
    snippet_chars: int,
    enabled: bool | None = None,
) -> list[RetrievedChunk]:
    if not chunks:
        return []

    pre_sorted = sorted(chunks, key=lambda item: item.score, reverse=True)
    rerank_enabled = settings.rerank_enabled if enabled is None else enabled
    if not rerank_enabled:
        return pre_sorted

    candidate_count = max(settings.rerank_candidates, 0)
    candidates = pre_sorted[:candidate_count]
    remainder = pre_sorted[candidate_count:]
    if not candidates:
        return pre_sorted

    provider = settings.ai_provider.strip().lower()

    if provider == "fake":
        score_map = _rerank_fake(question, candidates, snippet_chars)
    elif provider == "openai":
        score_map = _rerank_openai(question, candidates, snippet_chars)
    else:
        raise ValueError(f"Unsupported AI_PROVIDER: {settings.ai_provider}")

    if not score_map:
        return pre_sorted

    scored: list[tuple[float, RetrievedChunk]] = []
    for chunk in candidates:
        new_score = score_map.get(str(chunk.chunk_id))
        if new_score is not None:
            chunk.score = float(new_score)
        scored.append((chunk.score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored] + remainder
