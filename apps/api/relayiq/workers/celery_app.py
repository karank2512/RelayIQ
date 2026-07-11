"""Celery application (ADR-001: Celery over Temporal for the MVP)."""

from celery import Celery

from relayiq.config import get_settings
from relayiq.logging_setup import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery(
    "relayiq",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["relayiq.workers.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,               # worker crash → task redelivered (idempotency handles dupes)
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    task_soft_time_limit=90,
    task_default_queue="enrichment",
    task_routes={
        "relayiq.workers.tasks.run_enrichment_task": {"queue": "enrichment"},
        "relayiq.workers.tasks.retry_crm_sync_task": {"queue": "sync"},
    },
    broker_connection_retry_on_startup=True,
    result_expires=3600,
)
