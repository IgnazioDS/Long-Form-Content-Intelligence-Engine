from fastapi import FastAPI

from apps.api.app.api import health, query, sources

app = FastAPI(title="Long-Form Content Intelligence Engine")

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(query.router)
