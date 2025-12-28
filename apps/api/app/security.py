from __future__ import annotations

from fastapi import Header, HTTPException

from packages.shared_db.settings import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = settings.api_key.strip()
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
