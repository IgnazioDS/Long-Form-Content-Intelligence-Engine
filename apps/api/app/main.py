from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.app.api import (
    answers,
    answers_grouped,
    answers_highlights,
    debug,
    health,
    query,
    query_verified,
    query_verified_highlights,
    sources,
)
from apps.api.app.api import (
    metrics as metrics_api,
)
from apps.api.app.middleware import RateLimitMiddleware, RequestContextMiddleware
from apps.api.app.observability.http_metrics_middleware import HttpMetricsMiddleware
from apps.api.app.observability.tracing import init_tracing_if_enabled
from packages.shared_db.logging import configure_logging
from packages.shared_db.settings import detect_max_workers, settings

configure_logging("api", settings.log_level, force=True)


def _validate_api_key_settings() -> None:
    if settings.require_api_key and not settings.api_key.strip():
        raise RuntimeError(
            "REQUIRE_API_KEY=true but API_KEY is missing or blank. "
            "Set API_KEY to start the API."
        )
    if settings.rate_limit_backend == "memory" and settings.rate_limit_rps > 0:
        max_workers = detect_max_workers()
        if max_workers > 1 or settings.require_api_key:
            raise RuntimeError(
                "In-memory rate limiting is not supported in multi-worker/"
                "production. Use RATE_LIMIT_BACKEND=external and enforce at "
                "gateway, or run a single worker."
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_api_key_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Long-Form Content Intelligence Engine", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RequestContextMiddleware)
    if settings.rate_limit_backend == "memory" and settings.rate_limit_rps > 0:
        app.add_middleware(RateLimitMiddleware)
    if settings.metrics_enabled:
        app.add_middleware(HttpMetricsMiddleware)

    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(query.router)
    app.include_router(query_verified.router)
    app.include_router(query_verified_highlights.router)
    app.include_router(answers.router)
    app.include_router(answers_highlights.router)
    app.include_router(answers_grouped.router)

    if settings.metrics_enabled:
        app.include_router(metrics_api.get_metrics_router())
    if settings.debug:
        app.include_router(debug.router)

    init_tracing_if_enabled(app)
    return app


app = create_app()
