from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.api import query_verified
from apps.api.app.deps import get_session
from apps.api.app.main import app
from apps.api.app.schemas import AnswerStyle, ClaimOut, Verdict
from apps.api.app.services.retrieval import RetrievedChunk
from apps.api.app.services.verify import CONTRADICTION_PREFIX


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
    assert set(payload.keys()) == {
        "answer",
        "answer_style",
        "citations",
        "claims",
        "verification_summary",
    }
    assert isinstance(payload["claims"], list)
    assert isinstance(payload["verification_summary"], dict)
    assert payload["answer_style"] == AnswerStyle.ORIGINAL.value

    allowed_ids = {str(chunk.chunk_id) for chunk in chunks}
    for citation in payload["citations"]:
        assert citation["chunk_id"] in allowed_ids
    for claim in payload["claims"]:
        for evidence in claim.get("evidence", []):
            assert evidence["chunk_id"] in allowed_ids


def test_query_verified_contradiction_prefix(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
    chunk = _make_chunk(chunk_id, "The system uses port 8000.")

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
        return "The system uses port 8000.", [chunk_id]

    def fake_verify_answer(
        question: str,
        answer: str,
        chunks: list[RetrievedChunk],
        cited_ids: list[uuid.UUID],
    ) -> list[ClaimOut]:
        return [
            ClaimOut(
                claim_text="The system uses port 8000.",
                verdict=Verdict.CONTRADICTED,
                support_score=0.0,
                contradiction_score=0.9,
                evidence=[],
            )
        ]

    monkeypatch.setattr(query_verified, "retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(query_verified, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(query_verified, "verify_answer", fake_verify_answer)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/verified",
            json={"question": "Which port does the system use?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    summary = payload["verification_summary"]
    assert summary["has_contradictions"] is True
    assert summary["contradicted_count"] == 1
    assert summary["overall_verdict"] == "HAS_CONTRADICTIONS"
    assert payload["answer"].startswith(CONTRADICTION_PREFIX)
    assert "The system uses port 8000." in payload["answer"]
    assert payload["answer_style"] == AnswerStyle.CONFLICT_REWRITTEN.value


def test_query_verified_grouped_contradiction_prefix(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    chunk = _make_chunk(chunk_id, "The system uses port 9000.")

    def fake_retrieve_candidates(
        *,
        session: FakeSession,
        question: str,
        query_embedding: list[float],
        source_ids: list[uuid.UUID] | None,
        rerank: bool | None = None,
        per_source_limit: int | None = None,
    ) -> list[RetrievedChunk]:
        return [chunk]

    def fake_generate_answer(
        question: str, chunks: list[RetrievedChunk]
    ) -> tuple[str, list[uuid.UUID]]:
        return "The system uses port 9000.", [chunk_id]

    def fake_verify_answer(
        question: str,
        answer: str,
        chunks: list[RetrievedChunk],
        cited_ids: list[uuid.UUID],
    ) -> list[ClaimOut]:
        return [
            ClaimOut(
                claim_text="The system uses port 9000.",
                verdict=Verdict.CONFLICTING,
                support_score=0.7,
                contradiction_score=0.7,
                evidence=[],
            )
        ]

    monkeypatch.setattr(query_verified, "retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(query_verified, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(query_verified, "verify_answer", fake_verify_answer)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/verified/grouped",
            json={"question": "Which port does the system use?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    summary = payload["verification_summary"]
    assert summary["has_contradictions"] is True
    assert summary["conflicting_count"] == 1
    assert summary["overall_verdict"] == "HAS_CONTRADICTIONS"
    assert payload["answer"].startswith(CONTRADICTION_PREFIX)
    assert isinstance(payload.get("citation_groups"), list)
    assert payload["answer_style"] == AnswerStyle.CONFLICT_REWRITTEN.value


def test_query_verified_insufficient_evidence_answer_style(monkeypatch: MonkeyPatch) -> None:
    chunk_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
    chunk = _make_chunk(chunk_id, "No relevant information.")

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
        return "insufficient evidence.", []

    def fake_verify_answer(
        question: str,
        answer: str,
        chunks: list[RetrievedChunk],
        cited_ids: list[uuid.UUID],
    ) -> list[ClaimOut]:
        return []

    monkeypatch.setattr(query_verified, "retrieve_candidates", fake_retrieve_candidates)
    monkeypatch.setattr(query_verified, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(query_verified, "verify_answer", fake_verify_answer)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)
    try:
        response = client.post(
            "/query/verified",
            json={"question": "Who authored this?", "source_ids": []},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer_style"] == AnswerStyle.INSUFFICIENT_EVIDENCE.value
