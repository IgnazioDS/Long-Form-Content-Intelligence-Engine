from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.app.deps import get_session
from apps.api.app.schemas import (
    QueryVerifiedGroupedHighlightsResponse,
    QueryVerifiedGroupedResponse,
)
from apps.api.app.security import require_api_key
from apps.api.app.services.verify import (
    coerce_citation_groups_payload,
    coerce_claims_payload,
    coerce_highlight_claims_from_claims,
    coerce_highlight_claims_payload,
    normalize_verification_summary,
)
from packages.shared_db.models import Answer

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/answers/{answer_id}/grouped", response_model=QueryVerifiedGroupedResponse)
def get_answer_grouped(
    answer_id: uuid.UUID, session: Session = Depends(get_session)
) -> QueryVerifiedGroupedResponse:
    answer_row = session.get(Answer, answer_id)
    if not answer_row:
        raise HTTPException(status_code=404, detail="Answer not found")

    raw_citations = answer_row.raw_citations
    if not isinstance(raw_citations, dict):
        raw_citations = {}
    raw_claims = raw_citations.get("claims")
    raw_highlights = raw_citations.get("claims_highlights")
    raw_summary = raw_citations.get("verification_summary")
    raw_ids = raw_citations.get("ids", [])
    citations_count = len(raw_ids) if isinstance(raw_ids, list) else 0

    claims = coerce_claims_payload(raw_claims)
    raw_claims_for_summary = raw_claims
    claims_for_summary = claims if raw_claims is not None else None
    if raw_claims_for_summary is None and isinstance(raw_highlights, list):
        raw_claims_for_summary = raw_highlights
        claims_for_summary = None

    verification_summary = normalize_verification_summary(
        answer_row.answer,
        raw_claims_for_summary,
        raw_summary,
        citations_count,
        claims=claims_for_summary,
    )
    answer_style = verification_summary.answer_style
    citation_groups = coerce_citation_groups_payload(
        raw_citations.get("citation_groups")
    )

    return QueryVerifiedGroupedResponse(
        answer=answer_row.answer,
        answer_style=answer_style,
        citations=[],
        claims=claims,
        citation_groups=citation_groups,
        verification_summary=verification_summary,
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

    raw_citations = answer_row.raw_citations
    if not isinstance(raw_citations, dict):
        raw_citations = {}
    raw_highlights = raw_citations.get("claims_highlights")
    raw_claims = raw_citations.get("claims")
    raw_summary = raw_citations.get("verification_summary")
    raw_ids = raw_citations.get("ids", [])
    citations_count = len(raw_ids) if isinstance(raw_ids, list) else 0

    base_claims = coerce_claims_payload(raw_claims)
    highlight_claims = coerce_highlight_claims_payload(raw_highlights)
    if highlight_claims:
        claims_out = highlight_claims
    else:
        claims_out = coerce_highlight_claims_from_claims(base_claims)

    raw_claims_for_summary = raw_claims
    claims_for_summary = base_claims if raw_claims is not None else None
    if raw_claims_for_summary is None and isinstance(raw_highlights, list):
        raw_claims_for_summary = raw_highlights
        claims_for_summary = None

    verification_summary = normalize_verification_summary(
        answer_row.answer,
        raw_claims_for_summary,
        raw_summary,
        citations_count,
        claims=claims_for_summary,
    )
    answer_style = verification_summary.answer_style
    citation_groups = coerce_citation_groups_payload(
        raw_citations.get("citation_groups")
    )

    return QueryVerifiedGroupedHighlightsResponse(
        answer=answer_row.answer,
        answer_style=answer_style,
        citations=[],
        claims=claims_out,
        citation_groups=citation_groups,
        verification_summary=verification_summary,
    )
