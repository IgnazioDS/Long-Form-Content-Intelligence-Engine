from __future__ import annotations

from typing import Literal, TypedDict, overload

from apps.api.app.schemas import (
    AnswerStyle,
    CitationGroupOut,
    CitationOut,
    ClaimHighlightOut,
    ClaimOut,
    VerificationSummaryOut,
)
from apps.api.app.services.verify import (
    coerce_citation_groups_payload,
    coerce_citations_payload,
    coerce_claims_payload,
    coerce_highlight_claims_from_claims,
    coerce_highlight_claims_payload,
    normalize_verification_summary,
    select_summary_inputs,
)
from packages.shared_db.models import Answer


class _HydratedBase(TypedDict):
    answer_text: str
    citations: list[CitationOut]
    citation_groups: list[CitationGroupOut]
    verification_summary: VerificationSummaryOut
    answer_style: AnswerStyle


class HydratedAnswerPayload(_HydratedBase):
    claims: list[ClaimOut]


class HydratedHighlightPayload(_HydratedBase):
    claims: list[ClaimHighlightOut]


@overload
def hydrate_answer_payload(
    answer_row: Answer, *, grouped: bool, highlights: Literal[False]
) -> HydratedAnswerPayload: ...


@overload
def hydrate_answer_payload(
    answer_row: Answer, *, grouped: bool, highlights: Literal[True]
) -> HydratedHighlightPayload: ...


def hydrate_answer_payload(
    answer_row: Answer, *, grouped: bool, highlights: bool
) -> HydratedAnswerPayload | HydratedHighlightPayload:
    raw_citations = answer_row.raw_citations
    if not isinstance(raw_citations, dict):
        raw_citations = {}
    raw_claims = raw_citations.get("claims")
    raw_highlights = raw_citations.get("claims_highlights")
    raw_summary = raw_citations.get("verification_summary")

    citations = coerce_citations_payload(raw_citations.get("citations"))
    raw_ids = raw_citations.get("ids", [])
    citations_count = len(raw_ids) if isinstance(raw_ids, list) else len(citations)

    summary_highlights = raw_highlights if grouped or highlights else None

    citation_groups = (
        coerce_citation_groups_payload(raw_citations.get("citation_groups"))
        if grouped
        else []
    )

    if highlights:
        base_claims = coerce_claims_payload(raw_claims)
        highlight_claims = coerce_highlight_claims_payload(raw_highlights)
        if highlight_claims:
            highlight_claims_out = highlight_claims
        else:
            highlight_claims_out = coerce_highlight_claims_from_claims(base_claims)
        raw_claims_for_summary, claims_for_summary = select_summary_inputs(
            raw_claims, raw_highlights, base_claims
        )
        verification_summary = normalize_verification_summary(
            answer_row.answer,
            raw_claims_for_summary,
            raw_summary,
            citations_count,
            claims=claims_for_summary,
        )
        answer_style = verification_summary.answer_style
        return {
            "answer_text": answer_row.answer,
            "citations": citations,
            "citation_groups": citation_groups,
            "claims": highlight_claims_out,
            "verification_summary": verification_summary,
            "answer_style": answer_style,
        }

    claims_out = coerce_claims_payload(raw_claims)
    raw_claims_for_summary, claims_for_summary = select_summary_inputs(
        raw_claims, summary_highlights, claims_out
    )
    verification_summary = normalize_verification_summary(
        answer_row.answer,
        raw_claims_for_summary,
        raw_summary,
        citations_count,
        claims=claims_for_summary,
    )
    answer_style = verification_summary.answer_style

    return {
        "answer_text": answer_row.answer,
        "citations": citations,
        "citation_groups": citation_groups,
        "claims": claims_out,
        "verification_summary": verification_summary,
        "answer_style": answer_style,
    }
