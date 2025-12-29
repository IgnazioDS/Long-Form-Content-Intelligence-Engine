from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.api import query_verified
from apps.api.app.deps import get_session
from apps.api.app.main import app
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
        page_end=2,
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


def test_query_verified_returns_claims(monkeypatch: MonkeyPatch) -> None:
    chunk_one_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk_two_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    chunks = [
        _make_chunk(
            chunk_one_id,
            "The policy term is three years. It covers property damage and theft.",
        ),
        _make_chunk(
            chunk_two_id,
            "Renewal is optional and handled annually.",
        ),
    ]

    def fake_retrieve_candidates(
        *,
        session: FakeSession,
        question: str,
        query_embedding: list[float],
        source_ids: list[uuid.UUID] | None,
        rerank: bool | None = None,
    ) -> list[RetrievedChunk]:
        return chunks

    monkeypatch.setattr(query_verified, "retrieve_candidates", fake_retrieve_candidates)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/verified",
            json={"question": "What is the policy term?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"answer", "citations", "claims"}
    assert isinstance(payload["claims"], list)

    allowed_ids = {str(chunk.chunk_id) for chunk in chunks}
    for citation in payload["citations"]:
        assert citation["chunk_id"] in allowed_ids
    for claim in payload["claims"]:
        for evidence in claim.get("evidence", []):
            assert evidence["chunk_id"] in allowed_ids
