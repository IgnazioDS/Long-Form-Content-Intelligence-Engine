from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.api import query_verified_highlights
from apps.api.app.deps import get_session
from apps.api.app.main import app
from apps.api.app.schemas import ClaimOut, EvidenceOut, EvidenceRelation, Verdict
from apps.api.app.services.highlights import add_highlights_to_claims
from apps.api.app.services.retrieval import RetrievedChunk


class FakeSession:
    def __init__(self) -> None:
        self._items: list[Any] = []

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self._items.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:
        return None

    def close(self) -> None:
        return None


def _make_chunk(chunk_id: uuid.UUID, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        source_title="Test Doc",
        page_start=1,
        page_end=1,
        char_start=None,
        char_end=None,
        text=text,
        score=1.0,
    )


def _get_test_session() -> Generator[FakeSession, None, None]:
    session = FakeSession()
    try:
        yield session
    finally:
        session.close()


def test_add_highlights_to_claims_fake() -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk = _make_chunk(chunk_id, "The policy term is three years.")
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

    highlighted = add_highlights_to_claims("Question", [claim], [chunk])
    assert len(highlighted) == 1
    evidence = highlighted[0].evidence[0]
    assert evidence.highlight_start is not None
    assert evidence.highlight_end is not None
    assert evidence.highlight_text is not None
    assert evidence.highlight_text in chunk.text


def test_query_verified_highlights_response_shape(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk = _make_chunk(chunk_id, "The policy term is three years.")

    def fake_retrieve_candidates(
        *,
        session: FakeSession,
        question: str,
        query_embedding: list[float],
        source_ids: list[uuid.UUID] | None,
        rerank: bool | None = None,
    ) -> list[RetrievedChunk]:
        return [chunk]

    def fake_generate_answer(
        question: str, chunks: list[RetrievedChunk]
    ) -> tuple[str, list[uuid.UUID]]:
        return "The policy term is three years.", [chunk_id]

    def fake_verify_answer(
        question: str,
        answer: str,
        chunks: list[RetrievedChunk],
        cited_ids: list[uuid.UUID],
    ) -> list[ClaimOut]:
        return [
            ClaimOut(
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
        ]

    monkeypatch.setattr(
        query_verified_highlights, "retrieve_candidates", fake_retrieve_candidates
    )
    monkeypatch.setattr(query_verified_highlights, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(query_verified_highlights, "verify_answer", fake_verify_answer)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/verified/highlights",
            json={"question": "What is the policy term?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"answer", "citations", "claims"}
    claim = payload["claims"][0]
    evidence = claim["evidence"][0]
    assert "highlight_start" in evidence
    assert "highlight_end" in evidence
    assert "highlight_text" in evidence
    assert evidence["highlight_text"] is not None
