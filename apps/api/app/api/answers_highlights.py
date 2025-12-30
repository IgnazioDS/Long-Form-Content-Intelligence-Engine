from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import QueryVerifiedHighlightsResponse
from apps.api.app.security import require_api_key
from apps.api.app.services.verify import (
    coerce_claims_payload,
    coerce_citations_payload,
    coerce_highlight_claims_from_claims,
    coerce_highlight_claims_payload,
    normalize_verification_summary,
)
from packages.shared_db.models import Answer

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get(
    "/answers/{answer_id}/highlights",
    response_model=QueryVerifiedHighlightsResponse,
)
def get_answer_highlights(
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedHighlightsResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    raw_citations = answer_row.raw_citations
    if not isinstance(raw_citations, dict):
        raw_citations = {}
    raw_highlights = raw_citations.get("claims_highlights")
    raw_claims = raw_citations.get("claims")
    raw_summary = raw_citations.get("verification_summary")
    raw_ids = raw_citations.get("ids", [])
    citations_count = len(raw_ids) if isinstance(raw_ids, list) else 0
    citations = coerce_citations_payload(raw_citations.get("citations"))

    highlight_claims = coerce_highlight_claims_payload(raw_highlights)
    if highlight_claims:
        claims_out = highlight_claims
    else:
        base_claims = coerce_claims_payload(raw_claims)
        claims_out = coerce_highlight_claims_from_claims(base_claims)

    raw_claims_list = raw_claims if isinstance(raw_claims, list) and raw_claims else None
    raw_highlights_list = (
        raw_highlights if isinstance(raw_highlights, list) and raw_highlights else None
    )
    raw_claims_for_summary = raw_claims_list
    if raw_claims_for_summary is None and raw_highlights_list is not None:
        raw_claims_for_summary = raw_highlights_list

    verification_summary = normalize_verification_summary(
        answer_row.answer,
        raw_claims_for_summary,
        raw_summary,
        citations_count,
    )
    answer_style = verification_summary.answer_style

    return QueryVerifiedHighlightsResponse(
        answer=answer_row.answer,
        answer_style=answer_style,
        citations=citations,
        claims=claims_out,
        verification_summary=verification_summary,
    )
