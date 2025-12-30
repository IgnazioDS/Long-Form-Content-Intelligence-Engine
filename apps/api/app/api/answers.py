from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import QueryVerifiedResponse
from apps.api.app.security import require_api_key
from apps.api.app.services.verify import coerce_claims_payload, normalize_verification_summary
from packages.shared_db.models import Answer

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/answers/{answer_id}", response_model=QueryVerifiedResponse)
def get_answer(
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    raw_citations = answer_row.raw_citations
    if not isinstance(raw_citations, dict):
        raw_citations = {}
    raw_claims = raw_citations.get("claims")
    raw_summary = raw_citations.get("verification_summary")
    raw_ids = raw_citations.get("ids", [])
    citations_count = len(raw_ids) if isinstance(raw_ids, list) else 0

    claims = coerce_claims_payload(raw_claims)
    verification_summary = normalize_verification_summary(
        answer_row.answer, raw_claims, raw_summary, citations_count
    )
    answer_style = verification_summary.answer_style

    return QueryVerifiedResponse(
        answer=answer_row.answer,
        answer_style=answer_style,
        citations=[],
        claims=claims,
        verification_summary=verification_summary,
    )
