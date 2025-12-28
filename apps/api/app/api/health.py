from fastapi import APIRouter
from fastapi.responses import JSONResponse
import redis
from sqlalchemy import text

from packages.shared_db.session import SessionLocal
from packages.shared_db.settings import settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/deps")
def health_dependencies() -> JSONResponse:
    db_status = "ok"
    redis_status = "ok"
    status_code = 200

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc.__class__.__name__}"
        status_code = 503
    finally:
        session.close()

    try:
        client = redis.Redis.from_url(settings.redis_url)
        client.ping()
    except Exception as exc:
        redis_status = f"error: {exc.__class__.__name__}"
        status_code = 503

    payload = {
        "status": "ok" if status_code == 200 else "degraded",
        "db": db_status,
        "redis": redis_status,
    }
    return JSONResponse(content=payload, status_code=status_code)
