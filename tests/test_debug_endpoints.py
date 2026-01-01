from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.deps import get_session
from apps.api.app.main import create_app
from packages.shared_db.models import Chunk
from packages.shared_db.settings import settings


class FakeSession:
    def __init__(self) -> None:
        self._items: list[Any] = []

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self._items.append(obj)

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


def test_debug_endpoints_hidden_when_debug_false(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "api_key", "secret")

    app = create_app()
    client = TestClient(app)
    response = client.get(f"/debug/chunks/{uuid.uuid4()}")

    assert response.status_code == 404

    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    paths = schema_response.json().get("paths", {})
    assert "/debug/chunks/{chunk_id}" not in paths
    assert "/debug/sources/{source_id}/chunks" not in paths


def test_debug_endpoints_available_when_debug_true(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "api_key", "secret")

    app = create_app()
    session = FakeSession()
    chunk_id = uuid.uuid4()
    chunk = Chunk(
        id=chunk_id,
        source_id=uuid.uuid4(),
        chunk_index=0,
        text="chunk text",
        tsv="",
        embedding=[0.0],
        char_start=0,
        char_end=10,
    )
    session.add(chunk)
    app.dependency_overrides[get_session] = _override_session(session)

    client = TestClient(app)
    try:
        unauthorized = client.get(f"/debug/chunks/{chunk_id}")
        response = client.get(
            f"/debug/chunks/{chunk_id}",
            headers={"X-API-Key": "secret"},
        )
        schema_response = client.get("/openapi.json")
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["chunk_id"] == str(chunk_id)
    assert schema_response.status_code == 200
    paths = schema_response.json().get("paths", {})
    assert "/debug/chunks/{chunk_id}" in paths
