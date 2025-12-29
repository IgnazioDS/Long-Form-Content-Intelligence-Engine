from __future__ import annotations

import json
import re
from typing import Any

from apps.api.app.schemas import ClaimHighlightOut, ClaimOut, EvidenceHighlightOut
from apps.api.app.services.retrieval import RetrievedChunk
from packages.shared_db.openai_client import chat
from packages.shared_db.settings import settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MAX_HIGHLIGHT_LEN = 240
_CONTEXT_LEADING = 80
_CONTEXT_TRAILING = 160
_SNAP_RANGE = 20
_CHUNK_TEXT_LIMIT = 900


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _find_best_token_span(claim_text: str, chunk_text: str) -> tuple[int | None, int | None]:
    tokens = _tokenize(claim_text)
    if not tokens:
        return None, None

    chunk_lower = chunk_text.casefold()
    unique_tokens: list[str] = []
    seen = set()
    for token in tokens:
        if token not in seen:
            unique_tokens.append(token)
            seen.add(token)

    ranked_tokens = sorted(
        enumerate(unique_tokens),
        key=lambda item: (-len(item[1]), item[0]),
    )
    for _, token in ranked_tokens:
        idx = chunk_lower.find(token)
        if idx != -1:
            return idx, idx + len(token)
    return None, None


def _snap_start(text: str, start: int) -> int:
    if start <= 0:
        return 0
    lower = max(0, start - _SNAP_RANGE)
    for pos in range(start, lower - 1, -1):
        if text[pos].isspace():
            return pos + 1
    return start


def _snap_end(text: str, end: int) -> int:
    if end >= len(text):
        return len(text)
    upper = min(len(text) - 1, end + _SNAP_RANGE)
    for pos in range(end, upper + 1):
        if text[pos].isspace():
            return pos
    return end


def _highlight_from_text(
    claim_text: str, chunk_text: str
) -> tuple[int | None, int | None, str | None]:
    if not chunk_text:
        return None, None, None

    token_start, token_end = _find_best_token_span(claim_text, chunk_text)
    if token_start is None or token_end is None:
        return None, None, None

    start = max(0, token_start - _CONTEXT_LEADING)
    end = min(len(chunk_text), token_end + _CONTEXT_TRAILING)
    start = _snap_start(chunk_text, start)
    end = _snap_end(chunk_text, end)

    if end - start > _MAX_HIGHLIGHT_LEN:
        end = min(len(chunk_text), start + _MAX_HIGHLIGHT_LEN)

    if start >= end:
        return None, None, None

    highlight_text = chunk_text[start:end]
    return start, end, highlight_text


def _safe_json_load(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _extract_openai_spans(
    question: str,
    claim: ClaimOut,
    evidence_payloads: list[dict[str, Any]],
    allowed_ids: set[str],
    chunk_len_by_id: dict[str, int],
) -> dict[tuple[str, str], dict[str, int]]:
    system_prompt = (
        "You extract evidence highlight spans. "
        "Return JSON only with a 'spans' array. "
        "Do not include any other text. "
        "Only use the provided chunk_id values. "
        "Span start/end must be integers within the provided chunk_text length. "
        "If no span is found for an evidence item, omit it."
    )
    evidence_json = json.dumps(evidence_payloads, ensure_ascii=True)
    user_prompt = (
        f"Question: {question}\n"
        f"Claim: {claim.claim_text}\n\n"
        f"Evidence:\n{evidence_json}\n\n"
        "Return JSON: {{\"spans\": [{{\"chunk_id\": \"...\", "
        "\"relation\": \"SUPPORTS|CONTRADICTS|RELATED\", \"start\": 0, "
        "\"end\": 10}}]}}"
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
    spans_raw = payload.get("spans") if isinstance(payload, dict) else None
    if not isinstance(spans_raw, list):
        return {}

    spans: dict[tuple[str, str], dict[str, int]] = {}
    for item in spans_raw:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id", "")).strip()
        relation = str(item.get("relation", "")).strip().upper()
        if not chunk_id or chunk_id not in allowed_ids:
            continue
        if relation not in {"SUPPORTS", "CONTRADICTS", "RELATED"}:
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end <= start:
            continue
        max_len = chunk_len_by_id.get(chunk_id)
        if max_len is None or end > max_len:
            continue
        spans[(chunk_id, relation)] = {"start": start, "end": end}
    return spans


def _apply_highlights_fake(
    claims: list[ClaimOut],
    chunks: dict[str, RetrievedChunk],
) -> list[ClaimHighlightOut]:
    highlighted: list[ClaimHighlightOut] = []
    for claim in claims:
        evidence_items: list[EvidenceHighlightOut] = []
        for evidence in claim.evidence:
            chunk = chunks.get(str(evidence.chunk_id))
            if not chunk:
                evidence_items.append(
                    EvidenceHighlightOut(
                        chunk_id=evidence.chunk_id,
                        relation=evidence.relation,
                        snippet=evidence.snippet,
                        highlight_start=None,
                        highlight_end=None,
                        highlight_text=None,
                    )
                )
                continue
            start, end, text = _highlight_from_text(claim.claim_text, chunk.text)
            evidence_items.append(
                EvidenceHighlightOut(
                    chunk_id=evidence.chunk_id,
                    relation=evidence.relation,
                    snippet=evidence.snippet,
                    highlight_start=start,
                    highlight_end=end,
                    highlight_text=text,
                )
            )
        highlighted.append(
            ClaimHighlightOut(
                claim_text=claim.claim_text,
                verdict=claim.verdict,
                support_score=claim.support_score,
                contradiction_score=claim.contradiction_score,
                evidence=evidence_items,
            )
        )
    return highlighted


def add_highlights_to_claims(
    question: str,
    claims: list[ClaimOut],
    chunks: list[RetrievedChunk],
) -> list[ClaimHighlightOut]:
    chunk_lookup = {str(chunk.chunk_id): chunk for chunk in chunks}
    provider = settings.ai_provider.strip().lower() or "openai"
    if provider != "openai":
        return _apply_highlights_fake(claims, chunk_lookup)

    highlighted: list[ClaimHighlightOut] = []
    for claim in claims:
        evidence_items: list[EvidenceHighlightOut] = []
        evidence_payloads: list[dict[str, Any]] = []
        allowed_ids: set[str] = set()
        chunk_len_by_id: dict[str, int] = {}
        for evidence in claim.evidence:
            chunk = chunk_lookup.get(str(evidence.chunk_id))
            if not chunk:
                continue
            chunk_text = chunk.text
            truncated = _truncate_text(chunk_text, _CHUNK_TEXT_LIMIT)
            evidence_payloads.append(
                {
                    "chunk_id": str(evidence.chunk_id),
                    "relation": evidence.relation.value,
                    "chunk_text": truncated,
                    "chunk_text_length": len(truncated),
                    "chunk_full_length": len(chunk_text),
                }
            )
            allowed_ids.add(str(evidence.chunk_id))
            chunk_len_by_id[str(evidence.chunk_id)] = len(truncated)

        spans: dict[tuple[str, str], dict[str, int]] = {}
        if evidence_payloads:
            try:
                spans = _extract_openai_spans(
                    question, claim, evidence_payloads, allowed_ids, chunk_len_by_id
                )
            except Exception:
                spans = {}

        for evidence in claim.evidence:
            chunk_id = str(evidence.chunk_id)
            chunk = chunk_lookup.get(chunk_id)
            if not chunk:
                evidence_items.append(
                    EvidenceHighlightOut(
                        chunk_id=evidence.chunk_id,
                        relation=evidence.relation,
                        snippet=evidence.snippet,
                        highlight_start=None,
                        highlight_end=None,
                        highlight_text=None,
                    )
                )
                continue

            relation = evidence.relation.value
            span = spans.get((chunk_id, relation))
            highlight_start: int | None = None
            highlight_end: int | None = None
            highlight_text: str | None = None
            if span:
                truncated = _truncate_text(chunk.text, _CHUNK_TEXT_LIMIT)
                start = span.get("start")
                end = span.get("end")
                if (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start < end <= len(truncated)
                ):
                    highlight_start = start
                    highlight_end = end
                    # Indices are defined over full chunk text; validate against prefix length only.
                    highlight_text = chunk.text[start:end]

            if highlight_start is None or highlight_end is None or highlight_text is None:
                start, end, text = _highlight_from_text(claim.claim_text, chunk.text)
                highlight_start = start
                highlight_end = end
                highlight_text = text

            evidence_items.append(
                EvidenceHighlightOut(
                    chunk_id=evidence.chunk_id,
                    relation=evidence.relation,
                    snippet=evidence.snippet,
                    highlight_start=highlight_start,
                    highlight_end=highlight_end,
                    highlight_text=highlight_text,
                )
            )

        highlighted.append(
            ClaimHighlightOut(
                claim_text=claim.claim_text,
                verdict=claim.verdict,
                support_score=claim.support_score,
                contradiction_score=claim.contradiction_score,
                evidence=evidence_items,
            )
        )

    return highlighted
