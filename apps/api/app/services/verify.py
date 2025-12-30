from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from apps.api.app.schemas import (
    AnswerStyle,
    CitationGroupOut,
    CitationOut,
    ClaimHighlightOut,
    ClaimOut,
    EvidenceHighlightOut,
    EvidenceOut,
    EvidenceRelation,
    Verdict,
    VerificationOverallVerdict,
    VerificationSummaryOut,
)
from apps.api.app.services.rag import build_snippet, compute_absolute_offsets
from apps.api.app.services.retrieval import RetrievedChunk
from packages.shared_db.openai_client import chat
from packages.shared_db.settings import settings

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_QUESTION_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "does",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "the",
    "this",
    "to",
    "what",
    "which",
    "that",
}
_META_TOKENS = {
    "conflict",
    "conflicts",
    "fixture",
    "section",
    "test",
}
_MAX_CLAIMS_FAKE = 5
_MAX_SUPPORT_EVIDENCE = 2
_MAX_CONTRADICT_EVIDENCE = 1
_CHUNK_TEXT_LIMIT = 900
_FAKE_SUPPORT_THRESHOLD = 0.4
_ANSWER_STYLE_VALUES = {style.value for style in AnswerStyle}
CONTRADICTION_PREFIX = (
    "Contradictions detected in the source material. "
    "See claims below for details.\n\n"
)


def verify_answer(
    question: str,
    answer: str,
    chunks: list[RetrievedChunk],
    cited_ids: list[UUID],
) -> list[ClaimOut]:
    claim_texts = _extract_claims(question, answer)
    if not claim_texts:
        return []

    preferred_ids = {str(cid) for cid in cited_ids}
    if settings.ai_provider.strip().lower() == "fake":
        return _align_claims_fake(question, claim_texts, chunks, preferred_ids)
    return _align_claims_openai(question, claim_texts, chunks, preferred_ids)


def summarize_claims(
    claims: list[ClaimOut], answer: str, citations_count: int
) -> VerificationSummaryOut:
    supported_count = 0
    weak_support_count = 0
    unsupported_count = 0
    contradicted_count = 0
    conflicting_count = 0

    for claim in claims:
        if claim.verdict == Verdict.SUPPORTED:
            supported_count += 1
        elif claim.verdict == Verdict.WEAK_SUPPORT:
            weak_support_count += 1
        elif claim.verdict == Verdict.UNSUPPORTED:
            unsupported_count += 1
        elif claim.verdict == Verdict.CONTRADICTED:
            contradicted_count += 1
        elif claim.verdict == Verdict.CONFLICTING:
            conflicting_count += 1

    has_contradictions = (contradicted_count + conflicting_count) > 0
    all_unsupported = bool(claims) and unsupported_count == len(claims)
    insufficient_evidence = _is_insufficient_evidence_answer(answer) or (
        citations_count == 0 and all_unsupported
    )

    if insufficient_evidence:
        overall_verdict = VerificationOverallVerdict.INSUFFICIENT_EVIDENCE
    elif has_contradictions:
        overall_verdict = VerificationOverallVerdict.HAS_CONTRADICTIONS
    else:
        overall_verdict = VerificationOverallVerdict.OK

    return VerificationSummaryOut(
        supported_count=supported_count,
        weak_support_count=weak_support_count,
        unsupported_count=unsupported_count,
        contradicted_count=contradicted_count,
        conflicting_count=conflicting_count,
        has_contradictions=has_contradictions,
        overall_verdict=overall_verdict,
        answer_style=AnswerStyle.ORIGINAL,
    )


def normalize_verification_summary_payload(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return raw
    normalized = dict(raw)
    raw_style = normalized.get("answer_style")
    normalized_style: str | None = None
    if isinstance(raw_style, AnswerStyle):
        normalized_style = raw_style.value
    elif isinstance(raw_style, str):
        candidate = raw_style.strip().upper()
        if candidate in _ANSWER_STYLE_VALUES:
            normalized_style = candidate

    if normalized_style is None:
        verdict = normalized.get("overall_verdict")
        if isinstance(verdict, VerificationOverallVerdict):
            verdict_value = verdict.value
        elif verdict is None:
            verdict_value = ""
        else:
            verdict_value = str(verdict).strip().upper()
        if verdict_value == VerificationOverallVerdict.INSUFFICIENT_EVIDENCE.value:
            normalized_style = AnswerStyle.INSUFFICIENT_EVIDENCE.value
        elif normalized.get("has_contradictions") is True:
            normalized_style = AnswerStyle.CONFLICT_REWRITTEN.value
        else:
            normalized_style = AnswerStyle.ORIGINAL.value

    normalized["answer_style"] = normalized_style
    return normalized


def coerce_claims_payload(raw_claims: list[dict[str, Any]] | None) -> list[ClaimOut]:
    return _coerce_raw_claims(raw_claims)


def coerce_highlight_claims_payload(raw_claims: Any) -> list[ClaimHighlightOut]:
    if not isinstance(raw_claims, list):
        return []
    claims: list[ClaimHighlightOut] = []
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        claim_text = str(item.get("claim_text") or "")
        verdict = _coerce_verdict(item.get("verdict")) or Verdict.UNSUPPORTED
        support_score = _coerce_score(item.get("support_score"))
        contradiction_score = _coerce_score(item.get("contradiction_score"))
        evidence = _coerce_highlight_evidence(item.get("evidence"))
        claims.append(
            ClaimHighlightOut(
                claim_text=claim_text,
                verdict=verdict,
                support_score=support_score,
                contradiction_score=contradiction_score,
                evidence=evidence,
            )
        )
    return claims


def coerce_highlight_claims_from_claims(
    claims: list[ClaimOut],
) -> list[ClaimHighlightOut]:
    return [
        ClaimHighlightOut(
            claim_text=claim.claim_text,
            verdict=claim.verdict,
            support_score=claim.support_score,
            contradiction_score=claim.contradiction_score,
            evidence=[],
        )
        for claim in claims
    ]


def coerce_citation_groups_payload(raw_groups: Any) -> list[CitationGroupOut]:
    if not isinstance(raw_groups, list):
        return []
    groups: list[CitationGroupOut] = []
    for item in raw_groups:
        if not isinstance(item, dict):
            continue
        source_id = _coerce_uuid(item.get("source_id"))
        if source_id is None:
            continue
        source_title = item.get("source_title")
        if not isinstance(source_title, str):
            source_title = None
        citations = _coerce_citations_payload(item.get("citations"))
        groups.append(
            CitationGroupOut(
                source_id=source_id,
                source_title=source_title,
                citations=citations,
            )
        )
    return groups


def coerce_citations_payload(raw: Any) -> list[CitationOut]:
    return _coerce_citations_payload(raw)


def select_summary_inputs(
    raw_claims: Any,
    raw_highlights: Any,
    coerced_claims: list[ClaimOut],
) -> tuple[list[dict[str, Any]] | None, list[ClaimOut] | None]:
    if coerced_claims:
        raw_claims_list = raw_claims if isinstance(raw_claims, list) else None
        return raw_claims_list, coerced_claims
    raw_highlights_list = raw_highlights if isinstance(raw_highlights, list) else None
    if raw_highlights_list:
        return raw_highlights_list, None
    return None, None


def normalize_verification_summary(
    answer_text: str,
    raw_claims: list[dict[str, Any]] | None,
    raw_summary: dict[str, Any] | None,
    citations_count: int | None,
    claims: list[ClaimOut] | None = None,
) -> VerificationSummaryOut:
    if not isinstance(raw_summary, dict):
        raw_summary = None
    if claims is None:
        claims = _coerce_raw_claims(raw_claims)
    count_citations = (
        0 if citations_count is None else max(0, int(citations_count))
    )
    if claims:
        summary = summarize_claims(claims, answer_text, count_citations)
    else:
        summary = _summary_from_raw(answer_text, raw_summary, count_citations)
    summary.answer_style = _answer_style_from_answer(
        answer_text, summary.overall_verdict
    )
    return summary


def infer_answer_style(answer_text: str, summary: dict[str, Any]) -> AnswerStyle:
    if answer_text.strip().startswith(CONTRADICTION_PREFIX):
        return AnswerStyle.CONFLICT_REWRITTEN

    verdict = summary.get("overall_verdict")
    if isinstance(verdict, VerificationOverallVerdict):
        verdict_value = verdict.value
    elif verdict is None:
        verdict_value = ""
    else:
        verdict_value = str(verdict).strip().upper()
    if verdict_value == VerificationOverallVerdict.INSUFFICIENT_EVIDENCE.value:
        return AnswerStyle.INSUFFICIENT_EVIDENCE
    if summary.get("has_contradictions") is True:
        return AnswerStyle.CONFLICT_REWRITTEN
    return AnswerStyle.ORIGINAL


def _answer_style_from_answer(
    answer_text: str, overall_verdict: VerificationOverallVerdict
) -> AnswerStyle:
    if answer_text.strip().startswith(CONTRADICTION_PREFIX):
        return AnswerStyle.CONFLICT_REWRITTEN
    if overall_verdict == VerificationOverallVerdict.INSUFFICIENT_EVIDENCE:
        return AnswerStyle.INSUFFICIENT_EVIDENCE
    return AnswerStyle.ORIGINAL


def _summary_from_raw(
    answer_text: str,
    raw_summary: dict[str, Any] | None,
    citations_count: int,
) -> VerificationSummaryOut:
    if raw_summary:
        supported_count = _coerce_int(raw_summary.get("supported_count"))
        weak_support_count = _coerce_int(raw_summary.get("weak_support_count"))
        unsupported_count = _coerce_int(raw_summary.get("unsupported_count"))
        contradicted_count = _coerce_int(raw_summary.get("contradicted_count"))
        conflicting_count = _coerce_int(raw_summary.get("conflicting_count"))
    else:
        supported_count = 0
        weak_support_count = 0
        unsupported_count = 0
        contradicted_count = 0
        conflicting_count = 0

    has_contradictions = (contradicted_count + conflicting_count) > 0
    total_claims = (
        supported_count
        + weak_support_count
        + unsupported_count
        + contradicted_count
        + conflicting_count
    )
    all_unsupported = total_claims > 0 and unsupported_count == total_claims
    insufficient_evidence = _is_insufficient_evidence_answer(answer_text) or (
        citations_count == 0 and all_unsupported
    )
    if insufficient_evidence:
        computed_overall = VerificationOverallVerdict.INSUFFICIENT_EVIDENCE
    elif has_contradictions:
        computed_overall = VerificationOverallVerdict.HAS_CONTRADICTIONS
    else:
        computed_overall = VerificationOverallVerdict.OK
    raw_overall = (
        _coerce_overall_verdict(raw_summary.get("overall_verdict"))
        if raw_summary
        else None
    )
    if raw_overall is not None and raw_overall == computed_overall:
        overall_verdict = raw_overall
    else:
        overall_verdict = computed_overall

    return VerificationSummaryOut(
        supported_count=supported_count,
        weak_support_count=weak_support_count,
        unsupported_count=unsupported_count,
        contradicted_count=contradicted_count,
        conflicting_count=conflicting_count,
        has_contradictions=has_contradictions,
        overall_verdict=overall_verdict,
        answer_style=AnswerStyle.ORIGINAL,
    )


def rewrite_verified_answer(
    question: str,
    answer: str,
    claims: list[ClaimOut],
    verification_summary: VerificationSummaryOut,
) -> tuple[str, AnswerStyle]:
    clean_answer = answer
    if clean_answer.startswith(CONTRADICTION_PREFIX):
        clean_answer = clean_answer[len(CONTRADICTION_PREFIX) :].lstrip()
    if (
        verification_summary.overall_verdict
        == VerificationOverallVerdict.INSUFFICIENT_EVIDENCE
    ):
        verification_summary.answer_style = AnswerStyle.INSUFFICIENT_EVIDENCE
        return clean_answer, AnswerStyle.INSUFFICIENT_EVIDENCE
    if not verification_summary.has_contradictions:
        verification_summary.answer_style = AnswerStyle.ORIGINAL
        return clean_answer, AnswerStyle.ORIGINAL

    supported = [
        claim.claim_text
        for claim in claims
        if claim.verdict in {Verdict.SUPPORTED, Verdict.WEAK_SUPPORT}
    ]
    conflicted = [
        claim.claim_text
        for claim in claims
        if claim.verdict in {Verdict.CONTRADICTED, Verdict.CONFLICTING}
    ]
    unsupported = [
        claim.claim_text for claim in claims if claim.verdict == Verdict.UNSUPPORTED
    ]

    def format_section(title: str, items: list[str]) -> str:
        lines = [title]
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- None.")
        return "\n".join(lines)

    sections = [
        format_section("What the sources support", supported),
        format_section("Where the sources conflict", conflicted),
    ]
    if unsupported:
        sections.append(format_section("What's not supported", unsupported))

    if not supported and not conflicted and not unsupported:
        verification_summary.answer_style = AnswerStyle.ORIGINAL
        return clean_answer, AnswerStyle.ORIGINAL

    body = "\n\n".join(sections)
    verification_summary.answer_style = AnswerStyle.CONFLICT_REWRITTEN
    return f"{CONTRADICTION_PREFIX}{body}", AnswerStyle.CONFLICT_REWRITTEN


def build_verified_response(
    *,
    question: str,
    answer_text: str,
    claims: list[ClaimOut],
    citations: list[CitationOut],
    verification_summary: VerificationSummaryOut,
    citation_groups: list[CitationGroupOut] | None = None,
    highlighted_claims: list[ClaimHighlightOut] | None = None,
) -> tuple[str, AnswerStyle, VerificationSummaryOut]:
    rewritten_answer, answer_style = rewrite_verified_answer(
        question, answer_text, claims, verification_summary
    )
    verification_summary.answer_style = answer_style
    return rewritten_answer, answer_style, verification_summary


def assert_verification_consistency(
    answer: str,
    claims: list[ClaimOut],
    summary: VerificationSummaryOut,
    citations_count: int,
) -> None:
    errors: list[str] = []
    verdict_counts = {
        Verdict.SUPPORTED: 0,
        Verdict.WEAK_SUPPORT: 0,
        Verdict.UNSUPPORTED: 0,
        Verdict.CONTRADICTED: 0,
        Verdict.CONFLICTING: 0,
    }
    for claim in claims:
        if claim.verdict in verdict_counts:
            verdict_counts[claim.verdict] += 1

    summary_counts = {
        Verdict.SUPPORTED: summary.supported_count,
        Verdict.WEAK_SUPPORT: summary.weak_support_count,
        Verdict.UNSUPPORTED: summary.unsupported_count,
        Verdict.CONTRADICTED: summary.contradicted_count,
        Verdict.CONFLICTING: summary.conflicting_count,
    }
    for verdict, expected_count in verdict_counts.items():
        actual_count = summary_counts[verdict]
        if actual_count != expected_count:
            errors.append(
                "summary_count_mismatch("
                f"verdict={verdict.value}, expected={expected_count}, got={actual_count})"
            )

    expected_has_contradictions = (
        verdict_counts[Verdict.CONTRADICTED] + verdict_counts[Verdict.CONFLICTING]
    ) > 0
    if summary.has_contradictions != expected_has_contradictions:
        errors.append("summary_has_contradictions_mismatch")

    all_unsupported = bool(claims) and verdict_counts[Verdict.UNSUPPORTED] == len(claims)
    insufficient_evidence = _is_insufficient_evidence_answer(answer) or (
        citations_count == 0 and all_unsupported
    )
    if insufficient_evidence:
        expected_overall = VerificationOverallVerdict.INSUFFICIENT_EVIDENCE
    elif expected_has_contradictions:
        expected_overall = VerificationOverallVerdict.HAS_CONTRADICTIONS
    else:
        expected_overall = VerificationOverallVerdict.OK
    if summary.overall_verdict != expected_overall:
        errors.append(
            "summary_overall_verdict_mismatch("
            f"expected={expected_overall.value}, got={summary.overall_verdict.value})"
        )

    if answer.strip().startswith(CONTRADICTION_PREFIX):
        expected_style = AnswerStyle.CONFLICT_REWRITTEN
    elif expected_overall == VerificationOverallVerdict.INSUFFICIENT_EVIDENCE:
        expected_style = AnswerStyle.INSUFFICIENT_EVIDENCE
    else:
        expected_style = AnswerStyle.ORIGINAL
    if summary.answer_style != expected_style:
        errors.append(
            "summary_answer_style_mismatch("
            f"expected={expected_style.value}, got={summary.answer_style.value})"
        )

    if errors:
        raise ValueError("verification_summary_inconsistent: " + "; ".join(errors))


def _extract_claims(question: str, answer: str) -> list[str]:
    cleaned_answer = answer.strip()
    if not cleaned_answer:
        return []

    provider = settings.ai_provider.strip().lower() or "openai"
    if provider == "fake":
        if cleaned_answer.lower().startswith("insufficient evidence"):
            return []
        parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned_answer)]
        claims = [part for part in parts if part]
        return claims[:_MAX_CLAIMS_FAKE]

    system_prompt = (
        "Extract 3-8 atomic, factual claims from the provided answer. "
        "Return only a JSON object with a 'claims' array, each item having "
        "a 'claim_text' string."
    )
    user_prompt = (
        f"Question: {question}\n\nAnswer:\n{cleaned_answer}\n\n"
        "Return JSON: {\"claims\": [{\"claim_text\": \"...\"}]}"
    )
    content = chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    payload = _safe_json_load(content)
    claims_raw = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims_raw, list):
        return []
    extracted_claims: list[str] = []
    for item in claims_raw:
        if isinstance(item, dict):
            text = str(item.get("claim_text", "")).strip()
            if text:
                extracted_claims.append(text)
    return extracted_claims


def _is_insufficient_evidence_answer(answer: str) -> bool:
    cleaned = answer.strip().lower()
    return cleaned.startswith("insufficient evidence")


def _align_claims_openai(
    question: str,
    claim_texts: list[str],
    chunks: list[RetrievedChunk],
    preferred_ids: set[str],
) -> list[ClaimOut]:
    chunk_lookup = {str(chunk.chunk_id): chunk for chunk in chunks}
    allowed_ids = list(chunk_lookup.keys())
    if not allowed_ids:
        return [_empty_claim(claim_text) for claim_text in claim_texts]

    chunk_blocks: list[str] = []
    for chunk in chunks:
        title = chunk.source_title or "Untitled"
        pages = f"{chunk.page_start}-{chunk.page_end}" if chunk.page_start else "unknown"
        text = _truncate_text(chunk.text, _CHUNK_TEXT_LIMIT)
        chunk_blocks.append(
            f"[CHUNK {chunk.chunk_id}]\n"
            f"Source: {title} | Pages: {pages}\n"
            f"{text}"
        )

    claim_list = "\n".join(f"- {claim}" for claim in claim_texts)
    context = "\n\n".join(chunk_blocks)
    system_prompt = (
        "You are verifying claims against evidence. "
        "Use only the provided chunks and return JSON only. "
        "You MUST ONLY use chunk IDs that appear in the provided chunks. "
        "Do not invent chunk IDs. "
        "support_score and contradiction_score MUST be floats in [0,1]. "
        "If unsure, set both scores to 0.0."
    )
    user_prompt = (
        f"Question: {question}\n\n"
        f"Claims:\n{claim_list}\n\n"
        f"Chunks:\n{context}\n\n"
        "Return JSON with key 'results', an array of objects with: "
        "claim_text, verdict, supporting_chunk_ids, contradicting_chunk_ids, "
        "support_score, contradiction_score."
    )
    content = chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    payload = _safe_json_load(content)
    results_raw = payload.get("results") if isinstance(payload, dict) else None
    results_map: dict[str, dict[str, Any]] = {}
    if isinstance(results_raw, list):
        for item in results_raw:
            if isinstance(item, dict):
                claim_text = str(item.get("claim_text", "")).strip()
                if claim_text:
                    results_map[claim_text] = item

    claims_out: list[ClaimOut] = []
    for claim_text in claim_texts:
        result = results_map.get(claim_text, {})
        support_ids = _filter_ids(result.get("supporting_chunk_ids"), allowed_ids)
        contradict_ids = _filter_ids(result.get("contradicting_chunk_ids"), allowed_ids)
        support_ids = _prioritize_ids(support_ids, preferred_ids)
        contradict_ids = _prioritize_ids(contradict_ids, preferred_ids)
        support_score = _coerce_score(result.get("support_score"))
        contradiction_score = _coerce_score(result.get("contradiction_score"))
        verdict = _compute_verdict(support_score, contradiction_score)
        model_verdict = _coerce_verdict(result.get("verdict"))
        if support_score == 0.0 and contradiction_score == 0.0 and model_verdict:
            verdict = model_verdict
        evidence = _build_evidence(
            chunk_lookup,
            support_ids,
            contradict_ids,
            _MAX_SUPPORT_EVIDENCE,
            _MAX_CONTRADICT_EVIDENCE,
        )
        claims_out.append(
            ClaimOut(
                claim_text=claim_text,
                verdict=verdict,
                support_score=support_score,
                contradiction_score=contradiction_score,
                evidence=evidence,
            )
        )
    return claims_out


def _align_claims_fake(
    question: str,
    claim_texts: list[str],
    chunks: list[RetrievedChunk],
    preferred_ids: set[str],
) -> list[ClaimOut]:
    question_lower = question.casefold()
    question_section = None
    if "section a" in question_lower:
        question_section = "a"
    elif "section b" in question_lower:
        question_section = "b"
    question_tokens = _tokenize(question)
    _, question_words = _split_numeric_tokens(question_tokens)
    question_keywords = question_words - _QUESTION_STOPWORDS
    if not question_keywords:
        question_keywords = question_words
    question_signal = question_keywords - _META_TOKENS

    chunk_lookup = {str(chunk.chunk_id): chunk for chunk in chunks}
    chunk_tokens = {
        chunk_id: _tokenize(chunk.text) for chunk_id, chunk in chunk_lookup.items()
    }
    sentence_tokens: dict[str, list[tuple[set[str], set[str]]]] = {}
    for chunk_id, chunk in chunk_lookup.items():
        sentences = _SENTENCE_SPLIT_RE.split(chunk.text)
        sentence_tokens[chunk_id] = []
        for sentence in sentences:
            tokens = _tokenize(sentence)
            numbers, words = _split_numeric_tokens(tokens)
            if tokens:
                sentence_tokens[chunk_id].append((numbers, words))

    claims_out: list[ClaimOut] = []
    for claim_text in claim_texts:
        claim_tokens = _tokenize(claim_text)
        claim_numbers, claim_words = _split_numeric_tokens(claim_tokens)
        claim_section = _get_section_token(claim_words)
        if question_section and claim_section and claim_section != question_section:
            claims_out.append(
                ClaimOut(
                    claim_text=claim_text,
                    verdict=Verdict.UNSUPPORTED,
                    support_score=0.0,
                    contradiction_score=0.0,
                    evidence=[],
                )
            )
            continue
        claim_keywords = claim_words - _QUESTION_STOPWORDS
        if not claim_keywords:
            claim_keywords = claim_words
        claim_signal = claim_keywords - _META_TOKENS
        relevance_score = (
            _overlap_score(question_signal, claim_signal) if question_signal else 0.0
        )
        allow_contradictions = relevance_score >= 0.3
        best_id = None
        best_score = 0.0
        for chunk_id, tokens in chunk_tokens.items():
            score = _overlap_score(claim_tokens, tokens)
            if score > best_score:
                best_score = score
                best_id = chunk_id
        support_score = best_score
        contradiction_score = 0.0
        contradict_ids: list[str] = []
        if allow_contradictions and claim_numbers and claim_words:
            if best_id:
                best_sentences = sentence_tokens.get(best_id, [])
                best_sentence_idx = None
                best_sentence_score = 0.0
                for idx, (_, words) in enumerate(best_sentences):
                    overlap = _overlap_score(claim_words, words)
                    if overlap > best_sentence_score:
                        best_sentence_score = overlap
                        best_sentence_idx = idx
                for idx, (numbers, words) in enumerate(best_sentences):
                    if idx == best_sentence_idx:
                        continue
                    if not numbers or not numbers.isdisjoint(claim_numbers):
                        continue
                    if question_section and claim_section:
                        sentence_section = _get_section_token(words)
                        if sentence_section and sentence_section != claim_section:
                            continue
                    overlap = _overlap_score(claim_words, words)
                    if overlap >= _FAKE_SUPPORT_THRESHOLD:
                        if best_id not in contradict_ids:
                            contradict_ids.append(best_id)
                        contradiction_score = max(contradiction_score, max(overlap, 0.6))
                        break
            for chunk_id, tokens in chunk_tokens.items():
                if chunk_id == best_id:
                    continue
                if question_section and claim_section:
                    chunk_section = _get_section_token(tokens)
                    if chunk_section and chunk_section != claim_section:
                        continue
                chunk_numbers, chunk_words = _split_numeric_tokens(tokens)
                if not chunk_numbers:
                    continue
                if not chunk_numbers.isdisjoint(claim_numbers):
                    continue
                overlap = _overlap_score(claim_words, chunk_words)
                if overlap >= _FAKE_SUPPORT_THRESHOLD:
                    if chunk_id not in contradict_ids:
                        contradict_ids.append(chunk_id)
                    contradiction_score = max(contradiction_score, max(overlap, 0.6))
        verdict = _compute_verdict(support_score, contradiction_score)
        support_ids: list[str] = []
        if best_id and support_score >= _FAKE_SUPPORT_THRESHOLD:
            support_ids = _prioritize_ids([best_id], preferred_ids)
        if contradict_ids:
            contradict_ids = _prioritize_ids(contradict_ids, preferred_ids)
        evidence = _build_evidence(
            chunk_lookup,
            support_ids,
            contradict_ids,
            _MAX_SUPPORT_EVIDENCE,
            _MAX_CONTRADICT_EVIDENCE,
        )
        claims_out.append(
            ClaimOut(
                claim_text=claim_text,
                verdict=verdict,
                support_score=support_score,
                contradiction_score=contradiction_score,
                evidence=evidence,
            )
        )
    return claims_out


def _safe_json_load(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _tokenize(text: str) -> set[str]:
    return {match.group(0) for match in _TOKEN_RE.finditer(text.casefold())}


def _split_numeric_tokens(tokens: set[str]) -> tuple[set[str], set[str]]:
    numeric = {token for token in tokens if token.isdigit()}
    non_numeric = {token for token in tokens if token not in numeric}
    return numeric, non_numeric


def _get_section_token(tokens: set[str]) -> str | None:
    if "section" not in tokens:
        return None
    if "a" in tokens:
        return "a"
    if "b" in tokens:
        return "b"
    return None


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    # Recall-like score: proportion of left tokens covered by right tokens.
    overlap = len(left.intersection(right))
    return overlap / max(1, len(left))


def _truncate_text(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _filter_ids(raw: Any, allowed_ids: list[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    allowed = set(allowed_ids)
    filtered: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in allowed and item not in filtered:
            filtered.append(item)
    return filtered


def _prioritize_ids(ids: list[str], preferred_ids: set[str]) -> list[str]:
    if not preferred_ids:
        return ids
    preferred = [cid for cid in ids if cid in preferred_ids]
    others = [cid for cid in ids if cid not in preferred_ids]
    return preferred + others


def _coerce_int(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    if value < 0:
        return 0
    return value


def _coerce_optional_int(raw: Any) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _coerce_uuid(raw: Any) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _coerce_relation(raw: Any) -> EvidenceRelation | None:
    if isinstance(raw, EvidenceRelation):
        return raw
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().upper()
    try:
        return EvidenceRelation(normalized)
    except ValueError:
        return None


def _coerce_overall_verdict(
    raw: Any,
) -> VerificationOverallVerdict | None:
    if isinstance(raw, VerificationOverallVerdict):
        return raw
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().upper()
    try:
        return VerificationOverallVerdict(normalized)
    except ValueError:
        return None


def _coerce_score(raw: Any) -> float:
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if score < 0:
        return 0.0
    if score > 1:
        return 1.0
    return score


def _coerce_raw_claims(raw_claims: list[dict[str, Any]] | None) -> list[ClaimOut]:
    if not isinstance(raw_claims, list):
        return []
    claims: list[ClaimOut] = []
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        verdict = _coerce_verdict(item.get("verdict")) or Verdict.UNSUPPORTED
        claim_text = str(item.get("claim_text") or "")
        support_score = _coerce_score(item.get("support_score"))
        contradiction_score = _coerce_score(item.get("contradiction_score"))
        claims.append(
            ClaimOut(
                claim_text=claim_text,
                verdict=verdict,
                support_score=support_score,
                contradiction_score=contradiction_score,
                evidence=[],
            )
        )
    return claims


def _coerce_highlight_evidence(raw: Any) -> list[EvidenceHighlightOut]:
    if not isinstance(raw, list):
        return []
    evidence: list[EvidenceHighlightOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        chunk_id = _coerce_uuid(item.get("chunk_id"))
        relation = _coerce_relation(item.get("relation"))
        if chunk_id is None or relation is None:
            continue
        snippet = str(item.get("snippet") or "")
        snippet_start = _coerce_optional_int(item.get("snippet_start"))
        snippet_end = _coerce_optional_int(item.get("snippet_end"))
        highlight_start = _coerce_optional_int(item.get("highlight_start"))
        highlight_end = _coerce_optional_int(item.get("highlight_end"))
        highlight_text = item.get("highlight_text")
        if not (
            isinstance(highlight_start, int)
            and isinstance(highlight_end, int)
            and highlight_start < highlight_end
            and isinstance(highlight_text, str)
        ):
            highlight_start = None
            highlight_end = None
            highlight_text = None
        absolute_start = _coerce_optional_int(item.get("absolute_start"))
        absolute_end = _coerce_optional_int(item.get("absolute_end"))
        evidence.append(
            EvidenceHighlightOut(
                chunk_id=chunk_id,
                relation=relation,
                snippet=snippet,
                snippet_start=snippet_start,
                snippet_end=snippet_end,
                highlight_start=highlight_start,
                highlight_end=highlight_end,
                highlight_text=highlight_text,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )
    return evidence


def _coerce_citations_payload(raw: Any) -> list[CitationOut]:
    if not isinstance(raw, list):
        return []
    citations: list[CitationOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        chunk_id = _coerce_uuid(item.get("chunk_id"))
        source_id = _coerce_uuid(item.get("source_id"))
        if chunk_id is None or source_id is None:
            continue
        source_title = item.get("source_title")
        if not isinstance(source_title, str):
            source_title = None
        snippet = str(item.get("snippet") or "")
        page_start = _coerce_optional_int(item.get("page_start"))
        page_end = _coerce_optional_int(item.get("page_end"))
        snippet_start = _coerce_optional_int(item.get("snippet_start"))
        snippet_end = _coerce_optional_int(item.get("snippet_end"))
        absolute_start = _coerce_optional_int(item.get("absolute_start"))
        absolute_end = _coerce_optional_int(item.get("absolute_end"))
        citations.append(
            CitationOut(
                chunk_id=chunk_id,
                source_id=source_id,
                source_title=source_title,
                page_start=page_start,
                page_end=page_end,
                snippet=snippet,
                snippet_start=snippet_start,
                snippet_end=snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )
    return citations


def _coerce_verdict(raw: Any) -> Verdict | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().upper()
    try:
        return Verdict(normalized)
    except ValueError:
        return None


def _compute_verdict(support_score: float, contradiction_score: float) -> Verdict:
    if contradiction_score >= 0.6 and support_score >= 0.6:
        return Verdict.CONFLICTING
    if contradiction_score >= 0.6:
        return Verdict.CONTRADICTED
    if support_score >= 0.75:
        return Verdict.SUPPORTED
    if support_score >= 0.4:
        return Verdict.WEAK_SUPPORT
    return Verdict.UNSUPPORTED


def _build_evidence(
    chunk_lookup: dict[str, RetrievedChunk],
    support_ids: list[str],
    contradict_ids: list[str],
    max_support: int,
    max_contradict: int,
) -> list[EvidenceOut]:
    evidence: list[EvidenceOut] = []
    for chunk_id in support_ids[:max_support]:
        chunk = chunk_lookup.get(chunk_id)
        if not chunk:
            continue
        snippet = build_snippet(chunk.text)
        absolute_start, absolute_end = compute_absolute_offsets(
            chunk, snippet.snippet_start, snippet.snippet_end
        )
        evidence.append(
            EvidenceOut(
                chunk_id=chunk.chunk_id,
                relation=EvidenceRelation.SUPPORTS,
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )
    for chunk_id in contradict_ids[:max_contradict]:
        chunk = chunk_lookup.get(chunk_id)
        if not chunk:
            continue
        snippet = build_snippet(chunk.text)
        absolute_start, absolute_end = compute_absolute_offsets(
            chunk, snippet.snippet_start, snippet.snippet_end
        )
        evidence.append(
            EvidenceOut(
                chunk_id=chunk.chunk_id,
                relation=EvidenceRelation.CONTRADICTS,
                snippet=snippet.snippet_text,
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
                absolute_start=absolute_start,
                absolute_end=absolute_end,
            )
        )
    return evidence


def _empty_claim(claim_text: str) -> ClaimOut:
    return ClaimOut(
        claim_text=claim_text,
        verdict=Verdict.UNSUPPORTED,
        support_score=0.0,
        contradiction_score=0.0,
        evidence=[],
    )
