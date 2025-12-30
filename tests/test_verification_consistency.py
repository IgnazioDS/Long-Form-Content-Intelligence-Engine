from __future__ import annotations

from pytest import raises

from apps.api.app.schemas import AnswerStyle, ClaimOut, Verdict
from apps.api.app.services.verify import (
    CONTRADICTION_PREFIX,
    assert_verification_consistency,
    rewrite_verified_answer,
    summarize_claims,
)


def test_assert_verification_consistency_raises_on_mismatch() -> None:
    claims = [
        ClaimOut(
            claim_text="Alpha is enabled.",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[],
        )
    ]
    summary = summarize_claims(claims, "Alpha is enabled.", citations_count=1)
    summary.supported_count = 0
    with raises(ValueError, match="summary_count_mismatch"):
        assert_verification_consistency(
            "Alpha is enabled.", claims, summary, citations_count=1
        )


def test_assert_verification_consistency_accepts_rewrite_output() -> None:
    claims = [
        ClaimOut(
            claim_text="Port is 8000.",
            verdict=Verdict.CONTRADICTED,
            support_score=0.0,
            contradiction_score=0.8,
            evidence=[],
        )
    ]
    summary = summarize_claims(claims, "Port is 8000.", citations_count=1)
    rewritten, style = rewrite_verified_answer(
        "Question", "Port is 8000.", claims, summary
    )
    assert style == AnswerStyle.CONFLICT_REWRITTEN
    assert rewritten.startswith(CONTRADICTION_PREFIX)
    assert_verification_consistency(rewritten, claims, summary, citations_count=1)
