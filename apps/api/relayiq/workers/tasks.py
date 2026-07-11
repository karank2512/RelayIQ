"""Celery tasks. All tasks are idempotent re-entries: the orchestrator refuses to re-run a
job that already left the RECEIVED/QUEUED state, so worker restarts and redeliveries are safe."""

import httpx
from celery.utils.log import get_task_logger

from relayiq.db import get_sessionmaker
from relayiq.engines.orchestrator import run_enrichment_job
from relayiq.workers.celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True, max_retries=3, autoretry_for=(), name="relayiq.workers.tasks.run_enrichment_task")
def run_enrichment_task(self, job_id: str) -> dict:
    session = get_sessionmaker()()
    try:
        summary = run_enrichment_job(session, job_id)
        _maybe_callback(session, job_id, summary)
        return summary
    except Exception as exc:  # bounded retry, then park the job as failed
        session.rollback()
        logger.warning("enrichment task failed job=%s err=%s", job_id, exc)
        try:
            raise self.retry(countdown=2 ** self.request.retries, exc=exc)
        except self.MaxRetriesExceededError:
            from relayiq.enums import JobStatus
            from relayiq.models import EnrichmentJob

            job = session.get(EnrichmentJob, job_id)
            if job is not None and job.status not in (JobStatus.COMPLETED.value,):
                job.status = JobStatus.FAILED.value
                job.error = str(exc)[:500]
                session.commit()
            return {"error": str(exc)[:200]}
    finally:
        session.close()


def _maybe_callback(session, job_id: str, summary: dict) -> None:
    """Deliver the result to the job's callback URL (already SSRF-validated at intake).
    Signed with the webhook secret so receivers can authenticate us."""
    import json
    import time as _time

    from relayiq.config import get_settings
    from relayiq.models import EnrichmentJob
    from relayiq.services.ssrf import validate_callback_url
    from relayiq.services.webhook_security import build_signature_header

    job = session.get(EnrichmentJob, job_id)
    if job is None or not job.callback_url:
        return
    settings = get_settings()
    check = validate_callback_url(job.callback_url, allow_private=not settings.is_production)
    if not check.ok:  # re-validate at send time (DNS may have changed — TOCTOU defense)
        logger.warning("callback suppressed job=%s reason=%s", job_id, check.reason)
        return
    body = json.dumps({
        "job_id": job.id, "status": job.status, "result_summary": summary,
        "metadata": job.metadata_passthrough,
    }).encode()
    secret = settings.webhook_secret_list[0]
    headers = {
        "Content-Type": "application/json",
        "X-RelayIQ-Signature": build_signature_header(secret, int(_time.time()), body),
        "X-Delivery-Id": f"cb-{job.id}",
    }
    try:
        httpx.post(job.callback_url, content=body, headers=headers, timeout=5.0)
    except httpx.HTTPError as exc:
        logger.warning("callback delivery failed job=%s err=%s", job_id, type(exc).__name__)


@celery_app.task(bind=True, max_retries=5, name="relayiq.workers.tasks.retry_crm_sync_task")
def retry_crm_sync_task(self, attempt_id: str) -> str:
    """Retry a temporarily-failed CRM sync with exponential backoff."""
    from relayiq.enums import SyncStatus
    from relayiq.models import Account, Contact, CrmSyncAttempt

    session = get_sessionmaker()()
    try:
        attempt = session.get(CrmSyncAttempt, attempt_id)
        if attempt is None or attempt.status != SyncStatus.RETRYING.value:
            return "noop"
        from relayiq.services.crm_sync import sync_entity

        model = Contact if attempt.entity_type == "contact" else Account
        entity = session.get(model, attempt.entity_id)
        if entity is None:
            return "entity_missing"
        new_attempt = sync_entity(
            session, attempt.tenant_id, attempt.entity_type, entity, job_id=attempt.job_id
        )
        session.commit()
        if new_attempt is not None and new_attempt.status == SyncStatus.RETRYING.value:
            raise self.retry(countdown=min(300, 5 * 2 ** self.request.retries))
        return new_attempt.status if new_attempt else "skipped"
    finally:
        session.close()
