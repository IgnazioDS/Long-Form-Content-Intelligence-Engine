from fastapi import FastAPI

from apps.api.app.api import (
    answers,
    health,
    query,
    query_verified,
    query_verified_highlights,
    sources,
)
from apps.api.app.middleware import RateLimitMiddleware, RequestContextMiddleware
from packages.shared_db.logging import configure_logging
from packages.shared_db.settings import settings

configure_logging("api", settings.log_level, force=True)

app = FastAPI(title="Long-Form Content Intelligence Engine")

app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(query.router)
app.include_router(query_verified.router)
app.include_router(query_verified_highlights.router)
app.include_router(answers.router)
