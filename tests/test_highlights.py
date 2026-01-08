from __future__ import annotations

import json
import uuid

from pytest import MonkeyPatch

from apps.api.app.schemas import ClaimOut, EvidenceOut, EvidenceRelation, Verdict
from apps.api.app.services import highlights
from apps.api.app.services.rag import build_snippet
from apps.api.app.services.retrieval import RetrievedChunk
from packages.shared_db.settings import settings


def _make_chunk(
    chunk_id: uuid.UUID, text: str, char_start: int | None = None, char_end: int | None = None
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=uuid.uuid4(),
        source_title="Test",
        page_start=1,
        page_end=1,
        char_start=char_start,
        char_end=char_end,
        section_path=[],
        text=text,
        score=1.0,
    )


def test_openai_highlight_uses_full_text(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk_text = "alpha beta gamma delta"
    char_start = 250
    chunk = _make_chunk(chunk_id, chunk_text, char_start=char_start)
    snippet = build_snippet(chunk_text)
    assert snippet.snippet_start is not None
    assert snippet.snippet_end is not None
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
                    snippet_start=snippet.snippet_start,
                    snippet_end=snippet.snippet_end,
                )
            ],
        )

        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.snippet_start == snippet.snippet_start
    assert evidence.snippet_end == snippet.snippet_end
    assert evidence.highlight_start == start
    assert evidence.highlight_end == end
    assert evidence.highlight_text == chunk_text[start:end]
    assert evidence.highlight_text != chunk_text[start:end].upper()
    assert len(evidence.highlight_text) == end - start
    assert evidence.absolute_start == char_start + snippet.snippet_start
    assert evidence.absolute_end == char_start + snippet.snippet_end


def test_empty_provider_defaults_to_openai(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    chunk_text = "alpha beta gamma"
    chunk = _make_chunk(chunk_id, chunk_text)
    snippet = build_snippet(chunk_text)
    assert snippet.snippet_start is not None
    assert snippet.snippet_end is not None
    start = chunk_text.index("beta")
    end = start + len("beta")

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

    def fake_fallback(*args: object, **kwargs: object) -> object:
        raise AssertionError("unexpected fake highlight path")

    original_provider = settings.ai_provider
    settings.ai_provider = ""
    try:
        monkeypatch.setattr(highlights, "chat", fake_chat)
        monkeypatch.setattr(highlights, "_apply_highlights_fake", fake_fallback)
        claim = ClaimOut(
            claim_text="beta",
            verdict=Verdict.SUPPORTED,
            support_score=0.9,
            contradiction_score=0.0,
            evidence=[
                EvidenceOut(
                    chunk_id=chunk_id,
                    relation=EvidenceRelation.SUPPORTS,
                    snippet="beta",
                    snippet_start=snippet.snippet_start,
                    snippet_end=snippet.snippet_end,
                )
            ],
        )
        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.snippet_start == snippet.snippet_start
    assert evidence.snippet_end == snippet.snippet_end
    assert evidence.highlight_start == start
    assert evidence.highlight_end == end
    assert evidence.highlight_text == chunk_text[start:end]
    assert evidence.absolute_start is None
    assert evidence.absolute_end is None


def test_openai_span_out_of_bounds_falls_back(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    token = "gamma"
    chunk_text = f"alpha {token} delta " + ("x" * (highlights._CHUNK_TEXT_LIMIT + 50))
    chunk = _make_chunk(chunk_id, chunk_text)
    snippet = build_snippet(chunk_text)
    assert snippet.snippet_start is not None
    assert snippet.snippet_end is not None
    too_late_start = highlights._CHUNK_TEXT_LIMIT + 10
    too_late_end = too_late_start + 5

    def fake_chat(*args: object, **kwargs: object) -> str:
        payload = {
            "spans": [
                {
                    "chunk_id": str(chunk_id),
                    "relation": "SUPPORTS",
                    "start": too_late_start,
                    "end": too_late_end,
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
                    snippet_start=snippet.snippet_start,
                    snippet_end=snippet.snippet_end,
                )
            ],
        )
        highlighted = highlights.add_highlights_to_claims("Q", [claim], [chunk])
    finally:
        settings.ai_provider = original_provider

    evidence = highlighted[0].evidence[0]
    assert evidence.snippet_start == snippet.snippet_start
    assert evidence.snippet_end == snippet.snippet_end
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
    char_start = 1000
    chunk = _make_chunk(chunk_id, chunk_text, char_start=char_start)
    snippet = build_snippet(chunk_text)
    assert snippet.snippet_start is not None
    assert snippet.snippet_end is not None

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
                snippet_start=snippet.snippet_start,
                snippet_end=snippet.snippet_end,
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
    assert evidence.snippet_start == snippet.snippet_start
    assert evidence.snippet_end == snippet.snippet_end
    assert evidence.highlight_start is not None
    assert evidence.highlight_end is not None
    assert evidence.highlight_text is not None
    assert 0 <= evidence.highlight_start < evidence.highlight_end <= len(chunk_text)
    assert (
        evidence.highlight_text
        == chunk_text[evidence.highlight_start : evidence.highlight_end]
    )
    assert len(evidence.highlight_text) == evidence.highlight_end - evidence.highlight_start
    assert evidence.absolute_start == char_start + evidence.snippet_start
    assert evidence.absolute_end == char_start + evidence.snippet_end
