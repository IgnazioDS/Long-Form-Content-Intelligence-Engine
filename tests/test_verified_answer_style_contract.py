from __future__ import annotations

from pytest import raises

from apps.api.app.schemas import (
    AnswerStyle,
    ClaimOut,
    QueryVerifiedResponse,
    Verdict,
    VerificationOverallVerdict,
    VerificationSummaryOut,
)


def test_verified_response_answer_style_mismatch() -> None:
    summary = VerificationSummaryOut(
        supported_count=1,
        weak_support_count=0,
        unsupported_count=0,
        contradicted_count=0,
        conflicting_count=0,
        has_contradictions=False,
        overall_verdict=VerificationOverallVerdict.OK,
        answer_style=AnswerStyle.CONFLICT_REWRITTEN,
    )

    with raises(ValueError):
        QueryVerifiedResponse(
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
