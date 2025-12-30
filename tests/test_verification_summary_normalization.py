from __future__ import annotations

from typing import Any

from apps.api.app.schemas import (
    AnswerStyle,
    ClaimOut,
    QueryVerifiedResponse,
    Verdict,
    VerificationOverallVerdict,
    VerificationSummaryOut,
)
from apps.api.app.services.verify import normalize_verification_summary_payload


def _base_summary_payload() -> dict[str, Any]:
    return {
        "supported_count": 0,
        "weak_support_count": 0,
        "unsupported_count": 0,
        "contradicted_count": 0,
        "conflicting_count": 0,
        "has_contradictions": False,
        "overall_verdict": VerificationOverallVerdict.OK.value,
    }


def test_normalize_summary_infers_insufficient_evidence() -> None:
    payload = _base_summary_payload()
    payload["overall_verdict"] = VerificationOverallVerdict.INSUFFICIENT_EVIDENCE.value
    normalized = normalize_verification_summary_payload(payload)
    assert normalized["answer_style"] == AnswerStyle.INSUFFICIENT_EVIDENCE.value


def test_normalize_summary_infers_conflict_rewrite() -> None:
    payload = _base_summary_payload()
    payload["has_contradictions"] = True
    normalized = normalize_verification_summary_payload(payload)
    assert normalized["answer_style"] == AnswerStyle.CONFLICT_REWRITTEN.value


def test_normalize_summary_infers_original() -> None:
    payload = _base_summary_payload()
    normalized = normalize_verification_summary_payload(payload)
    assert normalized["answer_style"] == AnswerStyle.ORIGINAL.value


def test_legacy_summary_allows_verified_response() -> None:
    payload = _base_summary_payload()
    normalized = normalize_verification_summary_payload(payload)
    summary = VerificationSummaryOut(**normalized)

    response = QueryVerifiedResponse(
        answer="Ok.",
        answer_style=AnswerStyle.ORIGINAL,
        citations=[],
        claims=[
            ClaimOut(
                claim_text="Alpha is enabled.",
                verdict=Verdict.SUPPORTED,
                support_score=0.9,
                contradiction_score=0.0,
                evidence=[],
            )
        ],
        verification_summary=summary,
    )
    assert response.verification_summary.answer_style == AnswerStyle.ORIGINAL
