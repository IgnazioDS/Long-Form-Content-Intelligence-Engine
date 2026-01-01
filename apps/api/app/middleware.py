from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable

import opentelemetry.trace as trace
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from packages.shared_db.logging import request_id_var
from packages.shared_db.settings import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, rps: float, burst: int) -> None:
        self.rps = max(rps, 0.0)
        self.capacity = max(burst, 0)
        if self.capacity == 0 and self.rps > 0:
            self.capacity = max(1, int(self.rps))
        self.tokens: dict[str, float] = defaultdict(lambda: float(self.capacity))
        self.timestamps: dict[str, float] = defaultdict(time.monotonic)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.rps <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            last = self.timestamps.get(key, now)
            tokens = self.tokens.get(key, float(self.capacity))
            tokens = min(self.capacity, tokens + (now - last) * self.rps)
            if tokens < 1:
                self.tokens[key] = tokens
                self.timestamps[key] = now
                return False
            tokens -= 1
            self.tokens[key] = tokens
            self.timestamps[key] = now
            return True


rate_limiter = RateLimiter(settings.rate_limit_rps, settings.rate_limit_burst)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute("request_id", request_id)
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        request_id_var.reset(token)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if settings.rate_limit_rps <= 0:
            return await call_next(request)
        client_host = request.client.host if request.client else "unknown"
        api_key = request.headers.get("X-API-Key")
        key = api_key or client_host
        if not rate_limiter.allow(key):
            return JSONResponse(
                {"detail": "rate_limit_exceeded"},
                status_code=429,
            )
        return await call_next(request)
