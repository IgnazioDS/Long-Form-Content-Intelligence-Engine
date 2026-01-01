from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from packages.shared_db.observability.metrics import record_http_request

_UUID_RE = re.compile(r"/[0-9a-fA-F-]{36}")


def _normalize_path(request: Request) -> str:
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        return str(route.path)
    path = request.url.path
    return _UUID_RE.sub("/{uuid}", path)


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            record_http_request(request.method, _normalize_path(request), status_code, duration)
