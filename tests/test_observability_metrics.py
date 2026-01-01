from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app.deps import get_session
from apps.api.app.main import create_app
from packages.shared_db import openai_client
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


def _find_metric_value(
    payload: str, metric_name: str, labels: dict[str, str] | None = None
) -> float | None:
    labels = labels or {}
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith(metric_name):
            continue
        if "{" in line:
            name, remainder = line.split("{", 1)
            if name != metric_name:
                continue
            label_block, value_block = remainder.split("}", 1)
            matches = all(
                f'{key}="{value}"' in label_block for key, value in labels.items()
            )
            if not matches:
                continue
            value_str = value_block.strip().split(" ", 1)[0]
            return float(value_str)
        if labels:
            continue
        value_str = line.split(" ", 1)[1]
        return float(value_str)
    return None


def test_metrics_endpoint_exposed_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_path", "/metrics")
    app = create_app()
    client = TestClient(app)

    response = client.get(settings.metrics_path)

    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_metrics_endpoint_hidden_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", False)
    monkeypatch.setattr(settings, "metrics_path", "/metrics")
    app = create_app()
    client = TestClient(app)

    response = client.get(settings.metrics_path)

    assert response.status_code == 404


def test_http_metrics_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_path", "/metrics")
    app = create_app()
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200

    metrics_response = client.get(settings.metrics_path)
    value = _find_metric_value(
        metrics_response.text,
        "http_requests_total",
        {"method": "GET", "path": "/health", "status": "200"},
    )

    assert value is not None
    assert value >= 1


def test_llm_metrics_increment_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_path", "/metrics")
    monkeypatch.setattr(settings, "ai_provider", "fake")
    app = create_app()
    client = TestClient(app)

    def _boom(_: list[dict[str, str]]) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(openai_client, "_fake_chat", _boom)

    with pytest.raises(RuntimeError):
        openai_client.chat(messages=[{"role": "user", "content": "Hi"}])

    metrics_response = client.get(settings.metrics_path)
    value = _find_metric_value(
        metrics_response.text,
        "llm_chat_errors_total",
        {"provider": "fake", "model": "fake", "error_type": "RuntimeError"},
    )

    assert value is not None
    assert value >= 1


def test_integrity_metric_and_log_emitted(
    monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "metrics_path", "/metrics")
    app = create_app()

    raw_citations = {
        "verification_summary": {
            "supported_count": 1,
            "weak_support_count": 0,
            "unsupported_count": 0,
            "contradicted_count": 0,
            "conflicting_count": 0,
            "overall_verdict": "OK",
            "has_contradictions": False,
        },
        "claims": "bad",
    }
    answer_row = Answer(
        query_id=uuid.uuid4(),
        answer="Ok.",
        raw_citations=raw_citations,
    )
    session = FakeSession()
    session.add(answer_row)
    app.dependency_overrides[get_session] = _override_session(session)

    with TestClient(app) as client:
        with caplog.at_level(
            logging.WARNING, logger="apps.api.app.api._answers_hydration"
        ):
            response = client.get(f"/answers/{answer_row.id}")
        assert response.status_code == 200

        assert any(
            record.getMessage() == "verification_summary_inconsistent"
            or record.__dict__.get("event") == "verification_summary_inconsistent"
            for record in caplog.records
        )

        metrics_response = client.get(settings.metrics_path)
        value = _find_metric_value(
            metrics_response.text, "verification_summary_inconsistent_total"
        )
        assert value is not None
        assert value >= 1

    app.dependency_overrides.clear()
