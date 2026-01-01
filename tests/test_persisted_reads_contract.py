from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app.deps import get_session
from apps.api.app.main import app
from apps.api.app.schemas import Verdict, VerificationOverallVerdict
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


CHUNK_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SOURCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
VALID_CITATION = {
    "chunk_id": str(CHUNK_ID),
    "source_id": str(SOURCE_ID),
    "snippet": "Snippet text.",
}

RAW_HIGHLIGHTS = [
    {
        "claim_text": "The API uses port 9000.",
        "verdict": Verdict.CONTRADICTED.value,
        "support_score": 0.0,
        "contradiction_score": 0.9,
        "evidence": [
            {
                "chunk_id": str(CHUNK_ID),
                "relation": "CONTRADICTS",
                "snippet": "Snippet text.",
                "snippet_start": 0,
                "snippet_end": 7,
                "highlight_start": 0,
                "highlight_end": 7,
                "highlight_text": "Snippet",
            },
            {
                "chunk_id": "not-a-uuid",
                "relation": "SUPPORTS",
                "snippet": "Bad evidence.",
            },
        ],
    }
]


@pytest.mark.parametrize(
    "path_template",
    [
        "/answers/{id}",
        "/answers/{id}/highlights",
        "/answers/{id}/grouped",
        "/answers/{id}/grouped/highlights",
    ],
)
@pytest.mark.parametrize(
    "raw_citations, expect_ok_overall",
    [
        ("not-a-dict", False),
        (
            {
                "verification_summary": "bad",
                "claims": "bad",
                "claims_highlights": RAW_HIGHLIGHTS,
            },
            False,
        ),
        (
            {
                "citations": [VALID_CITATION],
                "claims": [_raw_claim(Verdict.UNSUPPORTED)],
                "ids": "bad",
            },
            True,
        ),
    ],
)
def test_persisted_read_contracts(
    path_template: str, raw_citations: Any, expect_ok_overall: bool
) -> None:
    answer_row = Answer(
        query_id=uuid.uuid4(),
        answer="Ok.",
        raw_citations=raw_citations,
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(path_template.format(id=answer_row.id))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer_style"] == payload["verification_summary"]["answer_style"]
    if expect_ok_overall:
        assert payload["verification_summary"]["overall_verdict"] == (
            VerificationOverallVerdict.OK.value
        )
