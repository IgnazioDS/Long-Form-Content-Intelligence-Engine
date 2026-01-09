from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from packages.shared_db.observability.metrics import get_registry
from packages.shared_db.settings import settings
from apps.api.app.security import require_api_key


def get_metrics_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_api_key)])

    @router.get(settings.metrics_path)
    def metrics() -> Response:
        if not settings.metrics_enabled:
            return Response(status_code=404)
        payload = generate_latest(get_registry())
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    return router
