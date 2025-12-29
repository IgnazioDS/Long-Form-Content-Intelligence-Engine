from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.api import query
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


def _make_chunk(chunk_id: uuid.UUID, source_id: uuid.UUID, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=source_id,
        source_title=title,
        page_start=1,
        page_end=1,
        char_start=None,
        char_end=None,
        text="Chunk text",
        score=1.0,
    )


def _get_test_session() -> Generator[FakeSession, None, None]:
    session = FakeSession()
    try:
        yield session
    finally:
        session.close()


def test_query_grouped_groups_citations(monkeypatch: MonkeyPatch) -> None:
    source_one = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    source_two = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    chunk_one_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    chunk_two_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    chunks = [
        _make_chunk(chunk_one_id, source_one, "Doc One"),
        _make_chunk(chunk_two_id, source_two, "Doc Two"),
    ]

    def fake_retrieve_candidates(
        *,
        session: FakeSession,
        question: str,
        query_embedding: list[float],
        source_ids: list[uuid.UUID] | None,
        rerank: bool | None = None,
        per_source_limit: int | None = None,
    ) -> list[RetrievedChunk]:
        return chunks

    def fake_generate_answer(
        question: str, chunks_in: list[RetrievedChunk]
    ) -> tuple[str, list[uuid.UUID]]:
        return "Answer", [chunk_one_id, chunk_two_id]

    monkeypatch.setattr(query, "retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(query, "generate_answer", fake_generate_answer)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/grouped",
            json={"question": "What is listed?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"answer", "citations", "citation_groups"}
    assert len(payload["citations"]) == 2
    assert len(payload["citation_groups"]) == 2

    group_sources = {group["source_id"] for group in payload["citation_groups"]}
    assert group_sources == {str(source_one), str(source_two)}

    for group in payload["citation_groups"]:
        for citation in group["citations"]:
            assert citation["source_id"] == group["source_id"]
