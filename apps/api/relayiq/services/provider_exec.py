"""Provider call execution: bounded retries, circuit breaker, persistence, health windows.

Every attempt (including failures) is persisted as a ProviderRequest and — when cost-bearing
or attempted — a ledger entry. Health windows aggregate per provider per 5-minute bucket.
"""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.enums import ProviderOutcome
from relayiq.models import ProviderHealthWindow, ProviderRequest, ProviderResponse
from relayiq.observability.metrics import (
    CIRCUIT_STATE,
    PROVIDER_CALLS,
    PROVIDER_LATENCY,
    PROVIDER_RETRIES,
)
from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter
from relayiq.providers.registry import ProviderRegistry


def execute_with_retries(
    session: Session,
    registry: ProviderRegistry,
    adapter: ProviderAdapter,
    *,
    tenant_id: str,
    job_id: str | None,
    entity_type: str,
    entity_id: str,
    identifiers: dict[str, str],
    fields: list[str],
    trace_id: str | None = None,
    timeout_ms: int = 8000,
) -> tuple[EnrichmentCallResult, ProviderRequest]:
    """Run one provider call with the adapter's retry policy. Returns the final result and
    the persisted ProviderRequest row (one row per logical call; attempts recorded on it)."""
    policy = adapter.retry_policy()
    breaker = registry.breaker(adapter.key)
    attempts = 0
    result: EnrichmentCallResult

    if not breaker.allow():
        CIRCUIT_STATE.labels(provider=adapter.key).set(1)
        result = EnrichmentCallResult(
            provider_key=adapter.key, entity_type=entity_type,
            outcome=ProviderOutcome.TEMP_FAIL, error="circuit breaker open", retryable=True,
        )
    else:
        CIRCUIT_STATE.labels(provider=adapter.key).set(0)
        while True:
            attempts += 1
            result = adapter.enrich(entity_type, identifiers, fields, timeout_ms=timeout_ms)
            if result.ok or result.outcome not in policy.retry_on or attempts > policy.max_retries:
                break
            PROVIDER_RETRIES.labels(provider=adapter.key).inc()
            time.sleep(policy.backoff_base_seconds * (2 ** (attempts - 1)))
        if result.ok:
            breaker.record_success()
        elif result.retryable:
            breaker.record_failure()

    req = ProviderRequest(
        tenant_id=tenant_id,
        provider_key=adapter.key,
        workflow_id=job_id,
        entity_type=entity_type,
        entity_id=entity_id,
        fields_requested=fields,
        outcome=result.outcome.value,
        retry_count=max(0, attempts - 1),
        latency_ms=result.latency_ms,
        cost_credits=result.cost_credits,
        error_type=result.error,
        trace_id=trace_id,
    )
    session.add(req)
    session.flush()
    if result.raw_payload:
        # Raw payloads are synthetic (simulator) or normalized summaries only — ADR-012.
        session.add(
            ProviderResponse(
                provider_request_id=req.id,
                raw_payload=result.raw_payload,
                received_at=datetime.now(UTC),
            )
        )

    PROVIDER_CALLS.labels(provider=adapter.key, outcome=result.outcome.value).inc()
    PROVIDER_LATENCY.labels(provider=adapter.key).observe(result.latency_ms)
    _record_health(session, adapter.key, result)
    return result, req


def _window_start(now: datetime) -> datetime:
    return now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)


def _record_health(session: Session, provider_key: str, result: EnrichmentCallResult) -> None:
    now = datetime.now(UTC)
    ws = _window_start(now)
    row = session.execute(
        select(ProviderHealthWindow).where(
            ProviderHealthWindow.provider_key == provider_key,
            ProviderHealthWindow.window_start == ws,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProviderHealthWindow(provider_key=provider_key, window_start=ws)
        session.add(row)
        session.flush()
    row.request_count += 1
    if result.ok:
        row.success_count += 1
    elif result.outcome == ProviderOutcome.TEMP_FAIL:
        row.temp_fail_count += 1
    elif result.outcome == ProviderOutcome.PERM_FAIL:
        row.perm_fail_count += 1
    elif result.outcome == ProviderOutcome.TIMEOUT:
        row.timeout_count += 1
    elif result.outcome == ProviderOutcome.RATE_LIMITED:
        row.rate_limited_count += 1
    # Streaming latency percentiles: keep a bounded reservoir on the row (JSON), recompute.
    reservoir = list(row.latency_reservoir or [])
    if len(reservoir) < 500:
        reservoir.append(result.latency_ms)
    row.latency_reservoir = reservoir
    if reservoir:
        s = sorted(reservoir)

        def pct(p: float) -> float:
            return s[min(len(s) - 1, int(p * len(s)))]

        row.p50_latency_ms = pct(0.50)
        row.p95_latency_ms = pct(0.95)
        row.p99_latency_ms = pct(0.99)
    row.total_cost_credits = float(row.total_cost_credits or 0) + result.cost_credits
    session.flush()


def provider_stats(session: Session, provider_key: str, hours: int = 24) -> dict:
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = session.execute(
        select(ProviderHealthWindow).where(
            ProviderHealthWindow.provider_key == provider_key,
            ProviderHealthWindow.window_start >= since,
        )
    ).scalars().all()
    total = sum(r.request_count for r in rows)
    if not total:
        return {"provider": provider_key, "requests": 0}
    lat: list[float] = []
    for r in rows:
        lat.extend(r.latency_reservoir or [])
    lat.sort()

    def pct(p: float) -> float | None:
        return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None

    return {
        "provider": provider_key,
        "requests": total,
        "success_rate": sum(r.success_count for r in rows) / total,
        "temp_fail_rate": sum(r.temp_fail_count for r in rows) / total,
        "perm_fail_rate": sum(r.perm_fail_count for r in rows) / total,
        "timeout_rate": sum(r.timeout_count for r in rows) / total,
        "rate_limited": sum(r.rate_limited_count for r in rows),
        "p50_latency_ms": pct(0.50),
        "p95_latency_ms": pct(0.95),
        "p99_latency_ms": pct(0.99),
        "cost_credits": sum(float(r.total_cost_credits or 0) for r in rows),
    }
