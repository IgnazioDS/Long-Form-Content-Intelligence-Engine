from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from apps.api.app.schemas import (
    AnswerStyle,
    ClaimOut,
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
    )


def rewrite_verified_answer(
    question: str,
    answer: str,
    claims: list[ClaimOut],
    verification_summary: VerificationSummaryOut,
) -> tuple[str, AnswerStyle]:
    if verification_summary.answer_style is None:
        verification_summary.answer_style = AnswerStyle.ORIGINAL
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
