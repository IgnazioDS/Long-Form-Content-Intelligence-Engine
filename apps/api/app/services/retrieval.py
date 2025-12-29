from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from apps.api.app.services.reranker import rerank_chunks
from packages.shared_db.models import Chunk, Source
from packages.shared_db.settings import settings


@dataclass
class RetrievedChunk:
    chunk_id: UUID
    source_id: UUID
    source_title: str | None
    page_start: int | None
    page_end: int | None
    char_start: int | None
    char_end: int | None
    text: str
    score: float


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _apply_source_filter(stmt: Select, source_ids: list[UUID] | None) -> Select:
    if not source_ids:
        return stmt
    return stmt.where(Chunk.source_id.in_(source_ids))


def _upsert_candidate(
    results: dict[UUID, RetrievedChunk],
    row: Any,
    score: float,
) -> None:
    chunk_id = row.id
    existing = results.get(chunk_id)
    if existing:
        if score > existing.score:
            existing.score = score
        return
    results[chunk_id] = RetrievedChunk(
        chunk_id=chunk_id,
        source_id=row.source_id,
        source_title=row.title,
        page_start=row.page_start,
        page_end=row.page_end,
        char_start=row.char_start,
        char_end=row.char_end,
        text=row.text,
        score=score,
    )


def _tokenize(text: str) -> set[str]:
    return {match.group(0) for match in _TOKEN_RE.finditer(text.casefold())}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left.intersection(right))
    union = len(left.union(right))
    return overlap / max(1, union)


def _apply_mmr(
    chunks: list[RetrievedChunk],
    diversity_lambda: float,
    max_candidates: int,
) -> list[RetrievedChunk]:
    if len(chunks) <= 1:
        return chunks

    candidate_count = min(max_candidates, len(chunks))
    if candidate_count <= 1:
        return chunks

    candidates = chunks[:candidate_count]
    remainder = chunks[candidate_count:]

    scores = [chunk.score for chunk in candidates]
    min_score = min(scores)
    max_score = max(scores)

    def norm(score: float) -> float:
        if max_score == min_score:
            return 1.0
        return (score - min_score) / (max_score - min_score)

    token_sets = [_tokenize(chunk.text) for chunk in candidates]
    selected: list[int] = []
    remaining = set(range(candidate_count))

    first = max(remaining, key=lambda idx: candidates[idx].score)
    selected.append(first)
    remaining.remove(first)

    while remaining:
        best_idx = None
        best_score = None
        for idx in remaining:
            relevance = norm(candidates[idx].score)
            max_sim = 0.0
            for sel in selected:
                sim = _jaccard(token_sets[idx], token_sets[sel])
                if sim > max_sim:
                    max_sim = sim
            mmr_score = diversity_lambda * relevance - (1 - diversity_lambda) * max_sim
            if best_score is None or mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    reordered = [candidates[idx] for idx in selected]
    return reordered + remainder


def retrieve_candidates(
    session: Session,
    question: str,
    query_embedding: list[float],
    source_ids: list[UUID] | None,
    rerank: bool | None = None,
    per_source_limit: int | None = None,
) -> list[RetrievedChunk]:
    vector_distance = Chunk.embedding.cosine_distance(query_embedding)
    results: dict[UUID, RetrievedChunk] = {}
    ts_query = func.plainto_tsquery("english", question)
    rank = func.ts_rank(Chunk.tsv, ts_query)

    per_source = per_source_limit if per_source_limit and per_source_limit > 0 else None
    if source_ids and per_source:
        for source_id in source_ids:
            vector_stmt = (
                select(
                    Chunk.id,
                    Chunk.source_id,
                    Source.title,
                    Chunk.page_start,
                    Chunk.page_end,
                    Chunk.char_start,
                    Chunk.char_end,
                    Chunk.text,
                    vector_distance.label("distance"),
                )
                .join(Source, Source.id == Chunk.source_id)
                .where(Chunk.source_id == source_id)
                .order_by(vector_distance)
                .limit(per_source)
            )
            fts_stmt = (
                select(
                    Chunk.id,
                    Chunk.source_id,
                    Source.title,
                    Chunk.page_start,
                    Chunk.page_end,
                    Chunk.char_start,
                    Chunk.char_end,
                    Chunk.text,
                    rank.label("rank"),
                )
                .join(Source, Source.id == Chunk.source_id)
                .where(Chunk.tsv.op("@@")(ts_query))
                .where(Chunk.source_id == source_id)
                .order_by(rank.desc())
                .limit(per_source)
            )

            for row in session.execute(vector_stmt):
                score = 1 - float(row.distance)
                _upsert_candidate(results, row, score)

            for row in session.execute(fts_stmt):
                score = float(row.rank)
                _upsert_candidate(results, row, score)
    else:
        vector_stmt = (
            select(
                Chunk.id,
                Chunk.source_id,
                Source.title,
                Chunk.page_start,
                Chunk.page_end,
                Chunk.char_start,
                Chunk.char_end,
                Chunk.text,
                vector_distance.label("distance"),
            )
            .join(Source, Source.id == Chunk.source_id)
        )
        vector_stmt = _apply_source_filter(vector_stmt, source_ids)
        vector_stmt = vector_stmt.order_by(vector_distance).limit(30)

        fts_stmt = (
            select(
                Chunk.id,
                Chunk.source_id,
                Source.title,
                Chunk.page_start,
                Chunk.page_end,
                Chunk.char_start,
                Chunk.char_end,
                Chunk.text,
                rank.label("rank"),
            )
            .join(Source, Source.id == Chunk.source_id)
            .where(Chunk.tsv.op("@@")(ts_query))
        )
        fts_stmt = _apply_source_filter(fts_stmt, source_ids)
        fts_stmt = fts_stmt.order_by(rank.desc()).limit(30)

        for row in session.execute(vector_stmt):
            score = 1 - float(row.distance)
            _upsert_candidate(results, row, score)

        for row in session.execute(fts_stmt):
            score = float(row.rank)
            _upsert_candidate(results, row, score)

    sorted_chunks = sorted(results.values(), key=lambda item: item.score, reverse=True)
    rerank_enabled = settings.rerank_enabled if rerank is None else rerank
    if rerank_enabled:
        sorted_chunks = rerank_chunks(
            question,
            sorted_chunks,
            settings.rerank_snippet_chars,
            enabled=rerank_enabled,
        )
    if settings.mmr_enabled:
        diversity_lambda = max(0.0, min(1.0, settings.mmr_lambda))
        candidate_count = max(0, settings.mmr_candidates)
        if candidate_count:
            sorted_chunks = _apply_mmr(sorted_chunks, diversity_lambda, candidate_count)
    return sorted_chunks
