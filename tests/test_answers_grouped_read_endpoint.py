from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from typing import Any

from fastapi.testclient import TestClient

from apps.api.app.deps import get_session
from apps.api.app.main import app
from apps.api.app.schemas import AnswerStyle, Verdict, VerificationOverallVerdict
from apps.api.app.services.verify import CONTRADICTION_PREFIX
from packages.shared_db.models import Answer


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

    def get(self, model: type[Any], obj_id: uuid.UUID) -> Any | None:
        for item in self._items:
            if isinstance(item, model) and item.id == obj_id:
                return item
        return None


def _make_session_override(
    session: FakeSession,
) -> Generator[FakeSession, None, None]:
    try:
        yield session
    finally:
        session.close()


def _override_session(
    session: FakeSession,
) -> Callable[[], Generator[FakeSession, None, None]]:
    def _override() -> Generator[FakeSession, None, None]:
        yield from _make_session_override(session)

    return _override


def _raw_claim(verdict: Verdict) -> dict[str, Any]:
    return {
        "claim_text": f"{verdict.value} claim.",
        "verdict": verdict.value,
        "support_score": 0.0,
        "contradiction_score": 0.0,
        "evidence": [],
    }


def _make_answer(answer_text: str, raw_citations: dict[str, Any] | None) -> Answer:
    return Answer(
        query_id=uuid.uuid4(),
        answer=answer_text,
        raw_citations=raw_citations,
    )


def test_get_answer_grouped_missing_summary() -> None:
    raw_claims = [_raw_claim(Verdict.SUPPORTED)]
    answer_row = _make_answer(
        "Ok.",
        raw_citations={"ids": ["one"], "claims": raw_claims},
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(f"/answers/{answer_row.id}/grouped")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer_style"] == payload["verification_summary"]["answer_style"]
    assert payload["verification_summary"]["supported_count"] == 1


def test_get_answer_grouped_uses_highlights_for_summary() -> None:
    raw_highlights = [
        {
            "claim_text": "The API uses port 9000.",
            "verdict": Verdict.CONTRADICTED.value,
            "support_score": 0.0,
            "contradiction_score": 0.9,
            "evidence": [],
        }
    ]
    answer_text = f"{CONTRADICTION_PREFIX}The API uses port 8000."
    answer_row = _make_answer(
        answer_text,
        raw_citations={"ids": ["one"], "claims_highlights": raw_highlights},
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(f"/answers/{answer_row.id}/grouped")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer_style"] == AnswerStyle.CONFLICT_REWRITTEN.value
    assert payload["verification_summary"]["has_contradictions"] is True
    assert payload["verification_summary"]["overall_verdict"] == (
        VerificationOverallVerdict.HAS_CONTRADICTIONS.value
    )


def test_get_answer_grouped_hydrates_citation_groups() -> None:
    chunk_id = uuid.uuid4()
    source_id = uuid.uuid4()
    answer_row = _make_answer(
        "Ok.",
        raw_citations={
            "ids": ["one"],
            "citations": [
                {
                    "chunk_id": str(chunk_id),
                    "source_id": str(source_id),
                    "source_title": "Doc",
                    "snippet": "Snippet text.",
                }
            ],
            "citation_groups": [
                {
                    "source_id": str(source_id),
                    "source_title": "Doc",
                    "citations": [
                        {
                            "chunk_id": str(chunk_id),
                            "source_id": str(source_id),
                            "source_title": "Doc",
                            "snippet": "Snippet text.",
                        }
                    ],
                }
            ],
        },
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(f"/answers/{answer_row.id}/grouped")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer_style"] == payload["verification_summary"]["answer_style"]
    assert len(payload["citations"]) == 1
    assert len(payload["citation_groups"]) == 1
    assert payload["citation_groups"][0]["source_id"] == str(source_id)


def test_get_answer_grouped_filters_bad_citation_groups() -> None:
    answer_row = _make_answer(
        "Ok.",
        raw_citations={
            "citation_groups": [
                {
                    "source_id": "not-a-uuid",
                    "citations": [],
                }
            ],
        },
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(f"/answers/{answer_row.id}/grouped")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["citation_groups"] == []
