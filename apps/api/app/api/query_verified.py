from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.api.grouping import build_citation_groups
from apps.api.app.deps import get_session, settings
from apps.api.app.schemas import (
    CitationOut,
    QueryRequest,
    QueryVerifiedGroupedResponse,
    QueryVerifiedResponse,
)
from apps.api.app.security import require_api_key
from apps.api.app.services.rag import build_snippet, compute_absolute_offsets, generate_answer
from apps.api.app.services.retrieval import retrieve_candidates
from apps.api.app.services.verify import (
    build_verified_response,
    summarize_claims,
    verify_answer,
)
from packages.shared_db.models import Answer, Query
from packages.shared_db.openai_client import embed_texts

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/query/verified", response_model=QueryVerifiedResponse)
def query_verified(
    payload: QueryRequest, session: Session = Depends(get_session)
) -> QueryVerifiedResponse:
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
        "query_verified_received",
        extra={
            "query_id": str(query_row.id),
            "source_ids": [str(source_id) for source_id in (payload.source_ids or [])],
            "rerank": payload.rerank,
        },
    )

    answer_text, cited_ids = generate_answer(payload.question, top_chunks)

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
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )

    claims = verify_answer(payload.question, answer_text, top_chunks, cited_ids)
    verification_summary = summarize_claims(claims, answer_text, len(citations))
    answer_text, answer_style, verification_summary = build_verified_response(
        question=payload.question,
        answer_text=answer_text,
        claims=claims,
        citations=citations,
        verification_summary=verification_summary,
    )
    raw_claims = [claim.model_dump(mode="json") for claim in claims]
    summary_payload = verification_summary.model_dump(mode="json")

    answer_row = Answer(
        query_id=query_row.id,
        answer=answer_text,
        raw_citations={
            "ids": [str(cid) for cid in cited_ids],
            "claims": raw_claims,
            "verification_summary": summary_payload,
        },
    )
    session.add(answer_row)
    session.commit()

    logger.info(
        "query_verified_completed",
        extra={
            "query_id": str(query_row.id),
            "citations_count": len(citations),
            "claims_count": len(claims),
            "answer_length": len(answer_text),
        },
    )

    return QueryVerifiedResponse(
        answer=answer_text,
        answer_style=answer_style,
        citations=citations,
        claims=claims,
        verification_summary=verification_summary,
    )


@router.post("/query/verified/grouped", response_model=QueryVerifiedGroupedResponse)
def query_verified_grouped(
    payload: QueryRequest, session: Session = Depends(get_session)
) -> QueryVerifiedGroupedResponse:
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
        "query_verified_grouped_received",
        extra={
            "query_id": str(query_row.id),
            "source_ids": [str(source_id) for source_id in (payload.source_ids or [])],
            "rerank": payload.rerank,
        },
    )

    answer_text, cited_ids = generate_answer(payload.question, top_chunks)

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
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )

    citation_groups = build_citation_groups(citations)
    claims = verify_answer(payload.question, answer_text, top_chunks, cited_ids)
    verification_summary = summarize_claims(claims, answer_text, len(citations))
    answer_text, answer_style, verification_summary = build_verified_response(
        question=payload.question,
        answer_text=answer_text,
        claims=claims,
        citations=citations,
        verification_summary=verification_summary,
        citation_groups=citation_groups,
    )
    raw_claims = [claim.model_dump(mode="json") for claim in claims]
    summary_payload = verification_summary.model_dump(mode="json")

    answer_row = Answer(
        query_id=query_row.id,
        answer=answer_text,
        raw_citations={
            "ids": [str(cid) for cid in cited_ids],
            "claims": raw_claims,
            "verification_summary": summary_payload,
        },
    )
    session.add(answer_row)
    session.commit()

    logger.info(
        "query_verified_grouped_completed",
        extra={
            "query_id": str(query_row.id),
            "citations_count": len(citations),
            "claims_count": len(claims),
            "answer_length": len(answer_text),
        },
    )

    return QueryVerifiedGroupedResponse(
        answer=answer_text,
        answer_style=answer_style,
        citations=citations,
        claims=claims,
        citation_groups=citation_groups,
        verification_summary=verification_summary,
    )
