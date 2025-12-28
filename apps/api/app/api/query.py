from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session, settings
from apps.api.app.security import require_api_key
from apps.api.app.schemas import CitationOut, QueryRequest, QueryResponse
from apps.api.app.services.rag import build_snippet, generate_answer
from apps.api.app.services.retrieval import retrieve_candidates
from packages.shared_db.models import Answer, Query
from packages.shared_db.openai_client import embed_texts

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/query", response_model=QueryResponse)
def query_rag(payload: QueryRequest, session: Session = Depends(get_session)) -> QueryResponse:
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

    citations: list[CitationOut] = []
    chunk_lookup = {chunk.chunk_id: chunk for chunk in top_chunks}
    for chunk_id in cited_ids:
        chunk = chunk_lookup.get(chunk_id)
        if not chunk:
            continue
        citations.append(
            CitationOut(
                chunk_id=chunk.chunk_id,
                source_id=chunk.source_id,
                source_title=chunk.source_title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                snippet=build_snippet(chunk.text),
            )
        )

    answer_row = Answer(
        query_id=query_row.id,
        answer=answer_text,
        raw_citations={"ids": [str(cid) for cid in cited_ids]},
    )
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

    return QueryResponse(answer=answer_text, citations=citations)
