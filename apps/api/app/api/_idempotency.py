from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.api.app.api._answers_hydration import hydrate_answer_payload
from apps.api.app.schemas import (
    QueryGroupedResponse,
    QueryResponse,
    QueryVerifiedGroupedHighlightsResponse,
    QueryVerifiedGroupedResponse,
    QueryVerifiedHighlightsResponse,
    QueryVerifiedResponse,
)
from apps.api.app.services.verify import (
    coerce_citation_groups_payload,
    coerce_citations_payload,
)
from packages.shared_db.models import Answer

QUERY_MODE_STANDARD = "query"
QUERY_MODE_GROUPED = "query_grouped"
QUERY_MODE_VERIFIED = "query_verified"
QUERY_MODE_VERIFIED_GROUPED = "query_verified_grouped"
QUERY_MODE_VERIFIED_HIGHLIGHTS = "query_verified_highlights"
QUERY_MODE_VERIFIED_GROUPED_HIGHLIGHTS = "query_verified_grouped_highlights"


def normalize_idempotency_key(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    return value or None


def attach_idempotency(
    raw_citations: dict[str, Any], *, key: str | None, mode: str
) -> dict[str, Any]:
    raw_citations["query_mode"] = mode
    if key:
        raw_citations["idempotency_key"] = key
    return raw_citations


def find_idempotent_answer(
    session: Session, *, key: str | None, mode: str
) -> Answer | None:
    if not key:
        return None
    return (
        session.query(Answer)
        .filter(
            Answer.raw_citations.isnot(None),
            Answer.raw_citations.contains({"idempotency_key": key, "query_mode": mode}),
        )
        .order_by(Answer.created_at.desc())
        .first()
    )


def build_query_response(answer_row: Answer) -> QueryResponse:
    raw_citations = (
        answer_row.raw_citations if isinstance(answer_row.raw_citations, dict) else {}
    )
    citations = coerce_citations_payload(raw_citations.get("citations"))
    return QueryResponse(
        answer_id=answer_row.id,
        query_id=answer_row.query_id,
        answer=answer_row.answer,
        citations=citations,
    )


def build_grouped_query_response(answer_row: Answer) -> QueryGroupedResponse:
    raw_citations = (
        answer_row.raw_citations if isinstance(answer_row.raw_citations, dict) else {}
    )
    citations = coerce_citations_payload(raw_citations.get("citations"))
    citation_groups = coerce_citation_groups_payload(
        raw_citations.get("citation_groups")
    )
    return QueryGroupedResponse(
        answer_id=answer_row.id,
        query_id=answer_row.query_id,
        answer=answer_row.answer,
        citations=citations,
        citation_groups=citation_groups,
    )


def build_verified_query_response(
    answer_row: Answer, *, grouped: bool, highlights: bool
) -> (
    QueryVerifiedResponse
    | QueryVerifiedGroupedResponse
    | QueryVerifiedHighlightsResponse
    | QueryVerifiedGroupedHighlightsResponse
):
    hydrated = hydrate_answer_payload(answer_row, grouped=grouped, highlights=highlights)
    if highlights and grouped:
        return QueryVerifiedGroupedHighlightsResponse(
            answer_id=answer_row.id,
            query_id=answer_row.query_id,
            answer=hydrated["answer_text"],
            answer_style=hydrated["answer_style"],
            citations=hydrated["citations"],
            claims=hydrated["claims"],
            citation_groups=hydrated["citation_groups"],
            verification_summary=hydrated["verification_summary"],
        )
    if highlights:
        return QueryVerifiedHighlightsResponse(
            answer_id=answer_row.id,
            query_id=answer_row.query_id,
            answer=hydrated["answer_text"],
            answer_style=hydrated["answer_style"],
            citations=hydrated["citations"],
            claims=hydrated["claims"],
            verification_summary=hydrated["verification_summary"],
        )
    if grouped:
        return QueryVerifiedGroupedResponse(
            answer_id=answer_row.id,
            query_id=answer_row.query_id,
            answer=hydrated["answer_text"],
            answer_style=hydrated["answer_style"],
            citations=hydrated["citations"],
            claims=hydrated["claims"],
            citation_groups=hydrated["citation_groups"],
            verification_summary=hydrated["verification_summary"],
        )
    return QueryVerifiedResponse(
        answer_id=answer_row.id,
        query_id=answer_row.query_id,
        answer=hydrated["answer_text"],
        answer_style=hydrated["answer_style"],
        citations=hydrated["citations"],
        claims=hydrated["claims"],
        verification_summary=hydrated["verification_summary"],
    )
