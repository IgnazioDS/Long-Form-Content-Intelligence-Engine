from celery import Celery

from packages.shared_db.logging import configure_logging
from packages.shared_db.settings import settings

configure_logging("worker", settings.log_level)

if settings.ai_provider.strip().lower() == "openai" and not settings.openai_api_key.strip():
    raise RuntimeError("OPENAI_API_KEY is required when AI_PROVIDER=openai.")

celery_app = Celery("ingest", broker=settings.redis_url, backend=settings.redis_url)
celery_conf: dict[str, object] = {
    "task_acks_late": True,
    "task_reject_on_worker_lost": True,
    "worker_prefetch_multiplier": max(1, settings.worker_prefetch_multiplier),
}
if settings.worker_concurrency > 0:
    celery_conf["worker_concurrency"] = settings.worker_concurrency
if settings.worker_max_tasks_per_child > 0:
    celery_conf["worker_max_tasks_per_child"] = settings.worker_max_tasks_per_child
if settings.worker_task_time_limit > 0:
    celery_conf["task_time_limit"] = settings.worker_task_time_limit
if settings.worker_task_soft_time_limit > 0:
    celery_conf["task_soft_time_limit"] = settings.worker_task_soft_time_limit
if settings.worker_visibility_timeout > 0:
    celery_conf["broker_transport_options"] = {
        "visibility_timeout": settings.worker_visibility_timeout
    }
celery_app.conf.update(celery_conf)
celery_app.autodiscover_tasks(["services.ingest"])
