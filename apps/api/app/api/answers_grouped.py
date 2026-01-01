from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.api._answers_hydration import hydrate_answer_payload
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
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedGroupedResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    hydrated = hydrate_answer_payload(answer_row, grouped=True, highlights=False)

    return QueryVerifiedGroupedResponse(
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        citation_groups=hydrated["citation_groups"],
        verification_summary=hydrated["verification_summary"],
    )


@router.get(
    "/answers/{answer_id}/grouped/highlights",
    response_model=QueryVerifiedGroupedHighlightsResponse,
)
def get_answer_grouped_highlights(
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedGroupedHighlightsResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    hydrated = hydrate_answer_payload(answer_row, grouped=True, highlights=True)

    return QueryVerifiedGroupedHighlightsResponse(
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        citation_groups=hydrated["citation_groups"],
        verification_summary=hydrated["verification_summary"],
    )
