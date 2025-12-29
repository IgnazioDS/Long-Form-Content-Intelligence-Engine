from __future__ import annotations

import json
import uuid

from pytest import MonkeyPatch

from apps.api.app.schemas import ClaimOut, EvidenceOut, EvidenceRelation, Verdict
from apps.api.app.services import highlights
from apps.api.app.services.retrieval import RetrievedChunk
from packages.shared_db.settings import settings


def _make_chunk(chunk_id: uuid.UUID, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=uuid.uuid4(),
        source_title="Test",
        page_start=1,
        page_end=1,
        text=text,
        score=1.0,
    )


def test_openai_highlight_uses_full_text(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk_text = "alpha beta gamma delta"
    chunk = _make_chunk(chunk_id, chunk_text)
    start = chunk_text.index("gamma")
    end = start + len("gamma")

    def fake_chat(*args: object, **kwargs: object) -> str:
        payload = {
            "spans": [
                {
                    "chunk_id": str(chunk_id),
                    "relation": "SUPPORTS",
                    "start": start,
                    "end": end,
                }
            ]
        }
        return json.dumps(payload)

    original_provider = settings.ai_provider
    settings.ai_provider = "openai"
    try:
        monkeypatch.setattr(highlights, "chat", fake_chat)
        monkeypatch.setattr(
            highlights,
            "_truncate_text",
            lambda text, limit: text[:limit].upper(),
        )

        claim = ClaimOut(
            claim_text="gamma",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[
                EvidenceOut(
                    chunk_id=chunk_id,
                    relation=EvidenceRelation.SUPPORTS,
                    snippet="gamma",
                )
            ],
        )

        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.highlight_start == start
    assert evidence.highlight_end == end
    assert evidence.highlight_text == chunk_text[start:end]
    assert evidence.highlight_text != chunk_text[start:end].upper()
    assert len(evidence.highlight_text) == end - start


def test_openai_span_out_of_bounds_falls_back(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    token = "gamma"
    chunk_text = f"alpha {token} delta"
    chunk = _make_chunk(chunk_id, chunk_text)
    token_start = chunk_text.index(token)
    token_end = token_start + len(token)

    def fake_chat(*args: object, **kwargs: object) -> str:
        payload = {
            "spans": [
                {
                    "chunk_id": str(chunk_id),
                    "relation": "SUPPORTS",
                    "start": 999,
                    "end": 1002,
                }
            ]
        }
        return json.dumps(payload)

    original_provider = settings.ai_provider
    settings.ai_provider = "openai"
    try:
        monkeypatch.setattr(highlights, "chat", fake_chat)
        claim = ClaimOut(
            claim_text=token,
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[
                EvidenceOut(
                    chunk_id=chunk_id,
                    relation=EvidenceRelation.SUPPORTS,
                    snippet=token,
                )
            ],
        )
        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.highlight_start is not None
    assert evidence.highlight_end is not None
    assert evidence.highlight_text is not None
    assert 0 <= evidence.highlight_start < evidence.highlight_end <= len(chunk_text)
    assert (
        evidence.highlight_text
        == chunk_text[evidence.highlight_start : evidence.highlight_end]
    )
    assert token in evidence.highlight_text


def test_fake_highlight_indices_match_full_text() -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    chunk_text = "The policy term is three years."
    chunk = _make_chunk(chunk_id, chunk_text)

    claim = ClaimOut(
        claim_text="The policy term is three years.",
        verdict=Verdict.SUPPORTED,
        support_score=0.9,
        contradiction_score=0.0,
        evidence=[
            EvidenceOut(
                chunk_id=chunk_id,
                relation=EvidenceRelation.SUPPORTS,
                snippet="The policy term is three years.",
            )
        ],
    )

    original_provider = settings.ai_provider
    settings.ai_provider = "fake"
    try:
        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.highlight_start is not None
    assert evidence.highlight_end is not None
    assert evidence.highlight_text is not None
    assert 0 <= evidence.highlight_start < evidence.highlight_end <= len(chunk_text)
    assert (
        evidence.highlight_text
        == chunk_text[evidence.highlight_start : evidence.highlight_end]
    )
    assert len(evidence.highlight_text) == evidence.highlight_end - evidence.highlight_start
