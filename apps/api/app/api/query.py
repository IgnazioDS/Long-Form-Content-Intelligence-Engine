from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from apps.api.app.api._idempotency import (
    QUERY_MODE_GROUPED,
    QUERY_MODE_STANDARD,
    attach_idempotency,
    build_grouped_query_response,
    build_query_response,
    find_idempotent_answer,
    normalize_idempotency_key,
)
from apps.api.app.api.grouping import build_citation_groups
from apps.api.app.deps import get_session, settings
from apps.api.app.schemas import (
    CitationOut,
    QueryGroupedResponse,
    QueryRequest,
    QueryResponse,
)
from apps.api.app.security import require_api_key
from apps.api.app.services.rag import (
    build_snippet,
    compute_absolute_offsets,
    enforce_grounded_answer,
    generate_answer,
)
from apps.api.app.services.retrieval import retrieve_candidates
from packages.shared_db.models import Answer, Query
from packages.shared_db.openai_client import embed_texts

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/query", response_model=QueryResponse)
def query_rag(
    payload: QueryRequest,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QueryResponse:
    normalized_key = normalize_idempotency_key(idempotency_key)
    cached = find_idempotent_answer(
        session, key=normalized_key, mode=QUERY_MODE_STANDARD
    )
    if cached:
        return build_query_response(cached)

    query_embedding = embed_texts([payload.question])[0]

    candidates = retrieve_candidates(
        session=session,
        question=payload.question,
        query_embedding=query_embedding,
        source_ids=payload.source_ids,
        rerank=payload.rerank,
    )
    top_chunks = candidates[: settings.max_chunks_per_query]

    query_row = Query(question=payload.question)
    session.add(query_row)
    session.commit()
    session.refresh(query_row)

    logger.info(
        "query_received",
        extra={
            "query_id": str(query_row.id),
            "source_ids": [str(source_id) for source_id in (payload.source_ids or [])],
            "rerank": payload.rerank,
        },
    )

    answer_text, cited_ids = generate_answer(payload.question, top_chunks)
    answer_text, cited_ids = enforce_grounded_answer(answer_text, cited_ids)

    citations: list[CitationOut] = []
    chunk_lookup = {chunk.chunk_id: chunk for chunk in top_chunks}
    for chunk_id in cited_ids:
        chunk = chunk_lookup.get(chunk_id)
        if not chunk:
            continue
        snippet = build_snippet(chunk.text)
        absolute_start, absolute_end = compute_absolute_offsets(
            chunk, snippet.snippet_start, snippet.snippet_end
        )
        citations.append(
            CitationOut(
                chunk_id=chunk.chunk_id,
                source_id=chunk.source_id,
                source_title=chunk.source_title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=chunk.section_path,
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )

    citations_payload = [citation.model_dump(mode="json") for citation in citations]
    raw_citations = attach_idempotency(
        {"ids": [str(cid) for cid in cited_ids], "citations": citations_payload},
        key=normalized_key,
        mode=QUERY_MODE_STANDARD,
    )
    answer_row = Answer(query_id=query_row.id, answer=answer_text, raw_citations=raw_citations)
    session.add(answer_row)
    session.commit()

    logger.info(
        "query_completed",
        extra={
            "query_id": str(query_row.id),
            "citations_count": len(citations),
            "answer_length": len(answer_text),
        },
    )

    return QueryResponse(
        answer_id=answer_row.id,
        query_id=query_row.id,
        answer=answer_text,
        citations=citations,
    )


@router.post("/query/grouped", response_model=QueryGroupedResponse)
def query_rag_grouped(
    payload: QueryRequest,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QueryGroupedResponse:
    normalized_key = normalize_idempotency_key(idempotency_key)
    cached = find_idempotent_answer(
        session, key=normalized_key, mode=QUERY_MODE_GROUPED
    )
    if cached:
        return build_grouped_query_response(cached)

    query_embedding = embed_texts([payload.question])[0]
    per_source_limit = (
        settings.per_source_retrieval_limit if payload.source_ids else None
    )

    candidates = retrieve_candidates(
        session=session,
        question=payload.question,
        query_embedding=query_embedding,
        source_ids=payload.source_ids,
        rerank=payload.rerank,
        per_source_limit=per_source_limit,
    )
    top_chunks = candidates[: settings.max_chunks_per_query]

    query_row = Query(question=payload.question)
    session.add(query_row)
    session.commit()
    session.refresh(query_row)

    logger.info(
        "query_grouped_received",
        extra={
            "query_id": str(query_row.id),
            "source_ids": [str(source_id) for source_id in (payload.source_ids or [])],
            "rerank": payload.rerank,
        },
    )

    answer_text, cited_ids = generate_answer(payload.question, top_chunks)
    answer_text, cited_ids = enforce_grounded_answer(answer_text, cited_ids)

    citations: list[CitationOut] = []
    chunk_lookup = {chunk.chunk_id: chunk for chunk in top_chunks}
    for chunk_id in cited_ids:
        chunk = chunk_lookup.get(chunk_id)
        if not chunk:
            continue
        snippet = build_snippet(chunk.text)
        absolute_start, absolute_end = compute_absolute_offsets(
            chunk, snippet.snippet_start, snippet.snippet_end
        )
        citations.append(
            CitationOut(
                chunk_id=chunk.chunk_id,
                source_id=chunk.source_id,
                source_title=chunk.source_title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=chunk.section_path,
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )

    citation_groups = build_citation_groups(citations)
    citations_payload = [citation.model_dump(mode="json") for citation in citations]
    citation_groups_payload = [
        group.model_dump(mode="json") for group in citation_groups
    ]
    raw_citations = attach_idempotency(
        {
            "ids": [str(cid) for cid in cited_ids],
            "citations": citations_payload,
            "citation_groups": citation_groups_payload,
        },
        key=normalized_key,
        mode=QUERY_MODE_GROUPED,
    )
    answer_row = Answer(query_id=query_row.id, answer=answer_text, raw_citations=raw_citations)
    session.add(answer_row)
    session.commit()

    logger.info(
        "query_grouped_completed",
        extra={
            "query_id": str(query_row.id),
            "citations_count": len(citations),
            "answer_length": len(answer_text),
        },
    )

    return QueryGroupedResponse(
        answer_id=answer_row.id,
        query_id=query_row.id,
        answer=answer_text,
        citations=citations,
        citation_groups=citation_groups,
    )
