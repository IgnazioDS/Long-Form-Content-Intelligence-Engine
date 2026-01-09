from celery import Celery

from packages.shared_db.logging import configure_logging
from packages.shared_db.settings import settings

configure_logging("worker", settings.log_level)

if settings.ai_provider.strip().lower() == "openai" and not settings.openai_api_key.strip():
    raise RuntimeError("OPENAI_API_KEY is required when AI_PROVIDER=openai.")

celery_app = Celery("ingest", broker=settings.redis_url, backend=settings.redis_url)
celery_app.autodiscover_tasks(["services.ingest"])
