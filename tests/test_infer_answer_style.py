from __future__ import annotations

from apps.api.app.schemas import AnswerStyle, VerificationOverallVerdict
from apps.api.app.services.verify import CONTRADICTION_PREFIX, infer_answer_style


def test_infer_answer_style_prefers_prefix_for_contradictions() -> None:
    summary = {
        "has_contradictions": True,
        "overall_verdict": VerificationOverallVerdict.OK.value,
    }
    answer_text = f"{CONTRADICTION_PREFIX}Original answer."
    assert infer_answer_style(answer_text, summary) == AnswerStyle.CONFLICT_REWRITTEN


def test_infer_answer_style_insufficient_evidence() -> None:
    summary = {"overall_verdict": VerificationOverallVerdict.INSUFFICIENT_EVIDENCE.value}
    assert (
        infer_answer_style("insufficient evidence.", summary)
        == AnswerStyle.INSUFFICIENT_EVIDENCE
    )


def test_infer_answer_style_defaults_original() -> None:
    summary = {
        "has_contradictions": False,
        "overall_verdict": VerificationOverallVerdict.OK.value,
    }
    assert infer_answer_style("Answer text.", summary) == AnswerStyle.ORIGINAL
