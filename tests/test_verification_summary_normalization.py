from __future__ import annotations

from typing import Any

from apps.api.app.schemas import (
    AnswerStyle,
    ClaimOut,
    QueryVerifiedResponse,
    Verdict,
    VerificationOverallVerdict,
)
from apps.api.app.services.verify import (
    CONTRADICTION_PREFIX,
    normalize_verification_summary,
    normalize_verification_summary_payload,
)


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


def _raw_claim(verdict: Verdict) -> dict[str, Any]:
    return {
        "claim_text": f"{verdict.value} claim.",
        "verdict": verdict.value,
        "support_score": 0.0,
        "contradiction_score": 0.0,
        "evidence": [],
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


def test_normalize_verification_summary_missing_summary() -> None:
    raw_claims = [_raw_claim(Verdict.SUPPORTED), _raw_claim(Verdict.CONTRADICTED)]
    answer_text = f"{CONTRADICTION_PREFIX}The API runs on port 8000."
    summary = normalize_verification_summary(
        answer_text=answer_text,
        raw_claims=raw_claims,
        raw_summary=None,
        citations_count=2,
    )
    assert summary.supported_count == 1
    assert summary.contradicted_count == 1
    assert summary.has_contradictions is True
    assert summary.overall_verdict == VerificationOverallVerdict.HAS_CONTRADICTIONS
    assert summary.answer_style == AnswerStyle.CONFLICT_REWRITTEN


def test_normalize_verification_summary_repairs_counts() -> None:
    raw_claims = [_raw_claim(Verdict.UNSUPPORTED), _raw_claim(Verdict.UNSUPPORTED)]
    raw_summary = _base_summary_payload()
    raw_summary["supported_count"] = 3
    raw_summary["unsupported_count"] = 0
    summary = normalize_verification_summary(
        answer_text="Ok.",
        raw_claims=raw_claims,
        raw_summary=raw_summary,
        citations_count=1,
    )
    assert summary.supported_count == 0
    assert summary.unsupported_count == 2
    assert summary.answer_style == AnswerStyle.ORIGINAL


def test_legacy_summary_allows_verified_response() -> None:
    payload = _base_summary_payload()
    summary = normalize_verification_summary(
        answer_text="Ok.",
        raw_claims=[_raw_claim(Verdict.SUPPORTED)],
        raw_summary=payload,
        citations_count=1,
    )

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
