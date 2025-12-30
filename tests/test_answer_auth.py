from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.deps import get_session
from apps.api.app.main import app
from packages.shared_db.models import Answer
from packages.shared_db.settings import settings


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


def _get_test_session(session: FakeSession) -> Generator[FakeSession, None, None]:
    try:
        yield session
    finally:
        session.close()


def _override_session(
    session: FakeSession,
) -> Callable[[], Generator[FakeSession, None, None]]:
    def _override() -> Generator[FakeSession, None, None]:
        yield from _get_test_session(session)

    return _override


def test_answers_requires_api_key(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_key", "secret")
    session = FakeSession()
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(f"/answers/{uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_answers_accepts_api_key(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_key", "secret")
    session = FakeSession()
    answer_row = Answer(query_id=uuid.uuid4(), answer="Ok.", raw_citations=None)
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        response = client.get(
            f"/answers/{answer_row.id}",
            headers={"X-API-Key": "secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
