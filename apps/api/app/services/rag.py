from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from apps.api.app.services.retrieval import RetrievedChunk
from packages.shared_db.openai_client import chat


def build_context(chunks: list[RetrievedChunk]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        title = chunk.source_title or "Untitled"
        pages = f"{chunk.page_start}-{chunk.page_end}" if chunk.page_start else "unknown"
        parts.append(
            f"[CHUNK {chunk.chunk_id}]\n"
            f"Source: {title} | Pages: {pages}\n"
            f"{chunk.text}"
        )
    return "\n\n".join(parts)


def _parse_citation_ids(payload: Any) -> list[str]:
    citations = payload.get("citations", []) if isinstance(payload, dict) else []
    ids: list[str] = []
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and "chunk_id" in item:
                ids.append(str(item["chunk_id"]))
    return ids


def _format_followups(followups: Any) -> str | None:
    if not isinstance(followups, list):
        return None
    cleaned = [str(item).strip() for item in followups if str(item).strip()]
    if not cleaned:
        return None
    return "Suggested follow-ups: " + "; ".join(cleaned)


def _call_llm(question: str, context: str, allowed_ids: list[str], strict: bool) -> dict:
    allowed_str = ", ".join(allowed_ids)
    guardrail = (
        "Only use the provided context. "
        "Cite chunk IDs for each major claim. "
        "If evidence is insufficient, reply with answer='insufficient evidence' and include follow_ups."
    )
    if strict:
        guardrail += " You MUST use only these chunk IDs: " + allowed_str

    user_prompt = (
        f"Question: {question}\n\n"
        f"Context:\n{context}\n\n"
        "Return a JSON object with keys: "
        "answer (string), citations (array of chunk_id strings), follow_ups (array of strings)."
    )
    content = chat(
        messages=[
            {"role": "system", "content": guardrail},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def generate_answer(
    question: str, chunks: list[RetrievedChunk]
) -> tuple[str, list[UUID]]:
    if not chunks:
        return "insufficient evidence", []

    allowed_ids = [str(chunk.chunk_id) for chunk in chunks]
    context = build_context(chunks)
    suggested_ids = [UUID(cid) for cid in allowed_ids]

    for attempt in range(2):
        payload = _call_llm(question, context, allowed_ids, strict=attempt == 1)
        if not payload:
            continue
        answer = str(payload.get("answer", "")).strip()
        citation_ids = _parse_citation_ids(payload)
        followups = _format_followups(payload.get("follow_ups"))

        if answer.lower().startswith("insufficient evidence"):
            if not followups:
                followups = "Suggested follow-ups: ask for a narrower question or specific sections."
            answer = f"insufficient evidence. {followups}"
            return answer, []

        invalid = [cid for cid in citation_ids if cid not in allowed_ids]
        if invalid or not citation_ids:
            if attempt == 0:
                continue
            return (
                "insufficient evidence. Suggested follow-ups: clarify the question.",
                suggested_ids,
            )

        return answer, [UUID(cid) for cid in citation_ids]

    return "insufficient evidence. Suggested follow-ups: narrow the question.", suggested_ids


def build_snippet(text: str, max_len: int = 280) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."
