from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from apps.api.app.main import create_app
from apps.api.app.middleware import RateLimitMiddleware
from packages.shared_db.settings import settings


def test_in_memory_rate_limit_disallowed_with_multiple_workers(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    monkeypatch.setattr(settings, "rate_limit_rps", 1.0)
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    app = create_app()
    with pytest.raises(RuntimeError, match="In-memory rate limiting"):
        with TestClient(app):
            pass


def test_external_rate_limit_allows_multi_worker(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "external")
    monkeypatch.setattr(settings, "rate_limit_rps", 1.0)
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    app = create_app()
    middleware_classes = [middleware.cls for middleware in app.user_middleware]
    assert RateLimitMiddleware not in middleware_classes

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
