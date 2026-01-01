from __future__ import annotations

import logging
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
    assert_verification_consistency,
    coerce_citation_groups_payload,
    coerce_citations_payload,
    coerce_claims_payload,
    coerce_highlight_claims_from_claims,
    coerce_highlight_claims_payload,
    normalize_verification_summary,
    select_summary_inputs,
)
from packages.shared_db.logging import request_id_var
from packages.shared_db.models import Answer
from packages.shared_db.observability.metrics import record_verification_summary_inconsistent

logger = logging.getLogger(__name__)


class _HydratedBase(TypedDict):
    answer_text: str
    citations: list[CitationOut]
    citation_groups: list[CitationGroupOut]
    verification_summary: VerificationSummaryOut
    answer_style: AnswerStyle
    citations_count: int
    consistency_claims: list[ClaimOut]


class HydratedAnswerPayload(_HydratedBase):
    claims: list[ClaimOut]


class HydratedHighlightPayload(_HydratedBase):
    claims: list[ClaimHighlightOut]


def _claims_for_consistency(claims: list[ClaimHighlightOut]) -> list[ClaimOut]:
    return [
        ClaimOut(
            claim_text=claim.claim_text,
            verdict=claim.verdict,
            support_score=claim.support_score,
            contradiction_score=claim.contradiction_score,
            evidence=[],
        )
        for claim in claims
    ]


def log_verification_inconsistency(
    *,
    answer_id: str,
    path: str,
    answer_text: str,
    claims: list[ClaimOut],
    verification_summary: VerificationSummaryOut,
    citations_count: int,
) -> None:
    def _reason_code(message: str) -> str:
        if ":" in message:
            message = message.split(":", 1)[1].strip()
        if not message:
            return "unknown"
        reason = message.split(";", 1)[0].strip()
        if "(" in reason:
            reason = reason.split("(", 1)[0].strip()
        return reason or "unknown"

    try:
        assert_verification_consistency(
            answer_text,
            claims,
            verification_summary,
            citations_count=citations_count,
        )
    except (AssertionError, ValueError) as exc:
        request_id = request_id_var.get()
        record_verification_summary_inconsistent()
        logger.warning(
            "verification_summary_inconsistent",
            extra={
                "event": "verification_summary_inconsistent",
                "answer_id": answer_id,
                "request_id": request_id,
                "path": path,
                "reason": str(exc),
                "reason_code": _reason_code(str(exc)),
            },
        )


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
            "citations_count": citations_count,
            "consistency_claims": _claims_for_consistency(highlight_claims_out),
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
        "citations_count": citations_count,
        "consistency_claims": claims_out,
    }
