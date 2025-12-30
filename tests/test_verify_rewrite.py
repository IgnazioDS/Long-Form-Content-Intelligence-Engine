from __future__ import annotations

from apps.api.app.schemas import ClaimOut, Verdict
from apps.api.app.services.verify import (
    CONTRADICTION_PREFIX,
    rewrite_verified_answer,
    summarize_claims,
)


def _extract_section(text: str, heading: str, headings: list[str]) -> str:
    start = text.find(heading)
    assert start != -1
    start += len(heading)
    end = len(text)
    for other in headings:
        if other == heading:
            continue
        idx = text.find(other, start)
        if idx != -1 and idx < end:
            end = idx
    return text[start:end]


def test_rewrite_verified_answer_with_contradictions() -> None:
    claims = [
        ClaimOut(
            claim_text="Alpha is enabled.",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[],
        ),
        ClaimOut(
            claim_text="Beta is present.",
            verdict=Verdict.WEAK_SUPPORT,
            support_score=0.6,
            contradiction_score=0.0,
            evidence=[],
        ),
        ClaimOut(
            claim_text="Port is 8000.",
            verdict=Verdict.CONTRADICTED,
            support_score=0.0,
            contradiction_score=0.8,
            evidence=[],
        ),
        ClaimOut(
            claim_text="Release date is 2024.",
            verdict=Verdict.CONFLICTING,
            support_score=0.7,
            contradiction_score=0.7,
            evidence=[],
        ),
        ClaimOut(
            claim_text="Gamma is disabled.",
            verdict=Verdict.UNSUPPORTED,
            support_score=0.0,
            contradiction_score=0.0,
            evidence=[],
        ),
    ]
    summary = summarize_claims(claims, "Original answer.", citations_count=2)

    rewritten = rewrite_verified_answer("Question", "Original answer.", claims, summary)
    assert rewritten.startswith(CONTRADICTION_PREFIX)

    headings = [
        "What the sources support",
        "Where the sources conflict",
        "What's not supported",
    ]
    support_section = _extract_section(rewritten, headings[0], headings)
    conflict_section = _extract_section(rewritten, headings[1], headings)
    unsupported_section = _extract_section(rewritten, headings[2], headings)

    assert "Alpha is enabled." in support_section
    assert "Beta is present." in support_section
    assert "Port is 8000." not in support_section
    assert "Release date is 2024." not in support_section

    assert "Port is 8000." in conflict_section
    assert "Release date is 2024." in conflict_section
    assert "Alpha is enabled." not in conflict_section

    assert "Gamma is disabled." in unsupported_section


def test_rewrite_verified_answer_without_contradictions() -> None:
    claims = [
        ClaimOut(
            claim_text="Alpha is enabled.",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[],
        )
    ]
    original = "Alpha is enabled."
    summary = summarize_claims(claims, original, citations_count=1)

    rewritten = rewrite_verified_answer("Question", original, claims, summary)
    assert rewritten == original


def test_rewrite_verified_answer_insufficient_evidence() -> None:
    claims: list[ClaimOut] = []
    original = "insufficient evidence. Suggested follow-ups: clarify the question."
    summary = summarize_claims(claims, original, citations_count=0)

    rewritten = rewrite_verified_answer("Question", original, claims, summary)
    assert rewritten == original


def test_rewrite_verified_answer_no_double_prefix() -> None:
    claims = [
        ClaimOut(
            claim_text="Alpha is enabled.",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[],
        ),
        ClaimOut(
            claim_text="Port is 8000.",
            verdict=Verdict.CONTRADICTED,
            support_score=0.0,
            contradiction_score=0.8,
            evidence=[],
        ),
    ]
    summary = summarize_claims(claims, "Original answer.", citations_count=1)
    prefixed = f"{CONTRADICTION_PREFIX}Original answer."
    rewritten = rewrite_verified_answer("Question", prefixed, claims, summary)
    assert rewritten.startswith(CONTRADICTION_PREFIX)
    assert rewritten.count(CONTRADICTION_PREFIX) == 1
