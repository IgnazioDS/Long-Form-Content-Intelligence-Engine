from __future__ import annotations

from dataclasses import dataclass
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
    text: str
    score: float


def _apply_source_filter(stmt: Select, source_ids: list[UUID] | None) -> Select:
    if not source_ids:
        return stmt
    return stmt.where(Chunk.source_id.in_(source_ids))


def retrieve_candidates(
    session: Session,
    question: str,
    query_embedding: list[float],
    source_ids: list[UUID] | None,
) -> list[RetrievedChunk]:
    vector_distance = Chunk.embedding.cosine_distance(query_embedding)
    vector_stmt = (
        select(
            Chunk.id,
            Chunk.source_id,
            Source.title,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.text,
            vector_distance.label("distance"),
        )
        .join(Source, Source.id == Chunk.source_id)
    )
    vector_stmt = _apply_source_filter(vector_stmt, source_ids)
    vector_stmt = vector_stmt.order_by(vector_distance).limit(30)

    ts_query = func.plainto_tsquery("english", question)
    rank = func.ts_rank(Chunk.tsv, ts_query)
    fts_stmt = (
        select(
            Chunk.id,
            Chunk.source_id,
            Source.title,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.text,
            rank.label("rank"),
        )
        .join(Source, Source.id == Chunk.source_id)
        .where(Chunk.tsv.op("@@")(ts_query))
    )
    fts_stmt = _apply_source_filter(fts_stmt, source_ids)
    fts_stmt = fts_stmt.order_by(rank.desc()).limit(30)

    results: dict[UUID, RetrievedChunk] = {}

    for row in session.execute(vector_stmt):
        score = 1 - float(row.distance)
        results[row.id] = RetrievedChunk(
            chunk_id=row.id,
            source_id=row.source_id,
            source_title=row.title,
            page_start=row.page_start,
            page_end=row.page_end,
            text=row.text,
            score=score,
        )

    for row in session.execute(fts_stmt):
        score = float(row.rank)
        existing = results.get(row.id)
        if existing:
            if score > existing.score:
                existing.score = score
            continue
        results[row.id] = RetrievedChunk(
            chunk_id=row.id,
            source_id=row.source_id,
            source_title=row.title,
            page_start=row.page_start,
            page_end=row.page_end,
            text=row.text,
            score=score,
        )

    sorted_chunks = sorted(results.values(), key=lambda item: item.score, reverse=True)
    return rerank_chunks(question, sorted_chunks, settings.rerank_snippet_chars)
