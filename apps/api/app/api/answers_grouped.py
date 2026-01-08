from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from apps.api.app.api._answers_hydration import (
    hydrate_answer_payload,
    log_verification_inconsistency,
)
from apps.api.app.deps import get_session
from apps.api.app.schemas import (
    QueryVerifiedGroupedHighlightsResponse,
    QueryVerifiedGroupedResponse,
)
from apps.api.app.security import require_api_key
from packages.shared_db.models import Answer

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/answers/{answer_id}/grouped", response_model=QueryVerifiedGroupedResponse)
def get_answer_grouped(
    answer_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
) -> QueryVerifiedGroupedResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    hydrated = hydrate_answer_payload(answer_row, grouped=True, highlights=False)

    response = QueryVerifiedGroupedResponse(
        answer_id=answer_row.id,
        query_id=answer_row.query_id,
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        citation_groups=hydrated["citation_groups"],
        verification_summary=hydrated["verification_summary"],
    )
    log_verification_inconsistency(
        answer_id=str(answer_id),
        path=request.url.path,
        answer_text=hydrated["answer_text"],
        claims=hydrated["consistency_claims"],
        verification_summary=hydrated["verification_summary"],
        citations_count=hydrated["citations_count"],
    )
    return response


@router.get(
    "/answers/{answer_id}/grouped/highlights",
    response_model=QueryVerifiedGroupedHighlightsResponse,
)
def get_answer_grouped_highlights(
    answer_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
) -> QueryVerifiedGroupedHighlightsResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    hydrated = hydrate_answer_payload(answer_row, grouped=True, highlights=True)

    response = QueryVerifiedGroupedHighlightsResponse(
        answer_id=answer_row.id,
        query_id=answer_row.query_id,
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        citation_groups=hydrated["citation_groups"],
        verification_summary=hydrated["verification_summary"],
    )
    log_verification_inconsistency(
        answer_id=str(answer_id),
        path=request.url.path,
        answer_text=hydrated["answer_text"],
        claims=hydrated["consistency_claims"],
        verification_summary=hydrated["verification_summary"],
        citations_count=hydrated["citations_count"],
    )
    return response
