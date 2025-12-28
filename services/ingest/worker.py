from celery import Celery

from packages.shared_db.logging import configure_logging
from packages.shared_db.settings import settings

configure_logging("worker", settings.log_level)

celery_app = Celery("ingest", broker=settings.redis_url, backend=settings.redis_url)
celery_app.autodiscover_tasks(["services.ingest"])
