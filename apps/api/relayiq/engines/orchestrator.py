"""The enrichment workflow orchestrator — runs one EnrichmentJob end to end.

Pipeline (see docs/architecture): pre-decision → routing → cache → budget reserve →
provider calls (+fallbacks) → observations → reconciliation → confidence → acceptance/
review → CRM gate & sync → finalize. Each step is persisted as a WorkflowStep; work
committed at step boundaries survives partial failures. Runs inline (sync API path) or
inside a Celery task — same code.
"""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.canonical.normalize import normalize_value, validate_field
from relayiq.config import get_settings
from relayiq.engines import confidence as confidence_engine
from relayiq.engines import decision as decision_engine
from relayiq.engines import routing as routing_engine
from relayiq.engines.reconciliation import reconcile_field
from relayiq.enums import (
    CacheStatus,
    EntityType,
    JobStatus,
    PreDecision,
    ReconciliationOutcome,
    StepStatus,
)
from relayiq.logging_setup import get_logger
from relayiq.models import (
    Account,
    Campaign,
    Contact,
    EnrichmentJob,
    FieldObservation,
    ReconciliationDecision,
    RoutingDecision,
    RoutingPolicy,
    Suppression,
    WorkflowStep,
)
from relayiq.observability.metrics import JOB_DURATION, JOBS, PRE_DECISIONS, ROUTING_DECISIONS
from relayiq.observability.tracing import current_trace_id, get_tracer
from relayiq.providers.registry import get_registry
from relayiq.services import budget as budget_service
from relayiq.services import ledger, quality, staleness
from relayiq.services.cache import FieldCache
from relayiq.services.crm_sync import sync_entity
from relayiq.services.entities import (
    apply_canonical_to_entity,
    entity_lookup_key,
    upsert_canonical_value,
)
from relayiq.services.provider_exec import execute_with_retries
from relayiq.services.review import create_task

log = get_logger("orchestrator")
tracer = get_tracer("relayiq.orchestrator")


class _StepRecorder:
    def __init__(self, session: Session, job: EnrichmentJob):
        self.session = session
        self.job = job
        self.seq = 0

    def run(self, name: str):
        recorder = self

        class _Ctx:
            def __enter__(ctx):
                recorder.seq += 1
                ctx.step = WorkflowStep(
                    job_id=recorder.job.id, sequence=recorder.seq, step_name=name,
                    status=StepStatus.RUNNING.value, started_at=datetime.now(UTC),
                )
                recorder.session.add(ctx.step)
                recorder.session.flush()
                ctx.span = tracer.start_span(f"enrich.{name}")
                return ctx.step

            def __exit__(ctx, exc_type, exc, tb):
                ctx.step.finished_at = datetime.now(UTC)
                ctx.step.status = StepStatus.FAILED.value if exc else StepStatus.COMPLETED.value
                if exc:
                    ctx.step.detail = {**(ctx.step.detail or {}), "error": str(exc)[:500]}
                ctx.span.end()
                recorder.session.commit()
                return False

        return _Ctx()


def _entity_for(session: Session, job: EnrichmentJob):
    model = Contact if job.entity_type == EntityType.CONTACT.value else Account
    return session.get(model, job.entity_id)


def _identifiers(entity_type: str, entity) -> dict:
    if entity_type == EntityType.CONTACT.value:
        return {
            "work_email": entity.work_email, "full_name": entity.full_name,
            "last_name": entity.last_name, "root_domain": entity.company_domain,
            "company_name": entity.company_name, "country": entity.country,
        }
    return {
        "root_domain": entity.root_domain, "website": entity.website,
        "name": entity.name, "company_name": entity.name,
        "hq_country": entity.hq_country, "employee_count": entity.employee_count,
    }


def _active_policy(session: Session, tenant_id: str, campaign: Campaign | None) -> dict:
    if campaign and campaign.routing_policy_id:
        row = session.get(RoutingPolicy, campaign.routing_policy_id)
        if row and row.is_active:
            return row.document
    row = session.execute(
        select(RoutingPolicy).where(
            RoutingPolicy.tenant_id == tenant_id, RoutingPolicy.is_active.is_(True)
        ).order_by(RoutingPolicy.created_at)
    ).scalars().first()
    return row.document if row else routing_engine.DEFAULT_POLICY


def run_enrichment_job(session: Session, job_id: str, *, cache: FieldCache | None = None) -> dict:
    """Execute the full pipeline for a job. Returns the job's result summary."""
    t0 = time.monotonic()
    settings = get_settings()
    job = session.get(EnrichmentJob, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    if job.status not in (JobStatus.RECEIVED.value, JobStatus.QUEUED.value):
        return job.result_summary  # already processed (idempotent re-entry)
    job.status = JobStatus.RUNNING.value
    job.trace_id = job.trace_id or current_trace_id()
    session.commit()

    steps = _StepRecorder(session, job)
    cache = cache or FieldCache()
    registry = get_registry(session, refresh=True)
    campaign = session.get(Campaign, job.campaign_id) if job.campaign_id else None
    entity = _entity_for(session, job)
    if entity is None:
        job.status = JobStatus.FAILED.value
        job.error = "entity not found"
        session.commit()
        return {}
    identifiers = _identifiers(job.entity_type, entity)
    entity_key = entity_lookup_key(job.entity_type, entity)
    summary: dict = {"fields_filled": 0, "providers_used": [], "served_from_cache": []}

    # ── 1. Pre-enrichment decision ────────────────────────────────────────
    with steps.run("pre_decision") as step:
        budget = budget_service.get_active_budget(session, job.tenant_id, job.campaign_id)
        est_cost = _estimate_cost(job, registry)
        bstate = budget_service.check(session, budget, est_cost)
        decision = decision_engine.decide(
            session,
            decision_engine.DecisionInput(
                tenant_id=job.tenant_id, entity_type=job.entity_type, entity_id=job.entity_id,
                requested_fields=list(job.requested_fields or []),
                identifiers=identifiers, campaign=campaign, budget_state=bstate,
                providers_available=any(registry.available(k) for k in registry.all()),
                estimated_min_cost=est_cost,
                current_job_id=job.id,
            ),
        )
        job.pre_decision = decision.decision.value
        job.decision_reasons = decision.reasons
        job.estimated_cost_credits = est_cost
        PRE_DECISIONS.labels(decision=decision.decision.value).inc()
        step.detail = {"decision": decision.decision.value, "reasons": decision.reasons}

    if decision.decision != PreDecision.ENRICH:
        # Terminal without provider spend. Cache-served fields get zero-cost ledger entries
        # with the avoided cost measured from live provider pricing.
        for f in decision.fields_from_cache:
            avoided = _cheapest_cost(registry, job.entity_type, f)
            ledger.record_entry(
                session, tenant_id=job.tenant_id, operation="enrich_field",
                campaign_id=job.campaign_id, job_id=job.id, entity_type=job.entity_type,
                entity_id=job.entity_id, fields_requested=[f],
                cache_status=CacheStatus.HIT.value, was_redundant=False,
                avoided_cost=avoided, trace_id=job.trace_id,
            )
        job.status = decision.job_status
        job.finished_at = datetime.now(UTC)
        summary["served_from_cache"] = sorted(decision.fields_from_cache)
        summary["fields_filled"] = len(decision.fields_from_cache)
        summary["accepted"] = decision.decision == PreDecision.USE_CACHE
        summary["decision"] = decision.decision.value
        job.result_summary = summary
        JOBS.labels(status=job.status).inc()
        JOB_DURATION.labels(status=job.status).observe(time.monotonic() - t0)
        session.commit()
        return summary

    fields_needed = decision.fields_to_enrich
    summary["served_from_cache"] = sorted(decision.fields_from_cache)

    # ── 2. Redis cache check (canonical store already consulted in decide()) ──
    with steps.run("cache_check") as step:
        cache_served: dict[str, dict] = {}
        for f in list(fields_needed):
            entry = cache.get_field(job.tenant_id, job.entity_type, entity_key, f)
            if entry.status == CacheStatus.HIT and entry.value is not None:
                cache_served[f] = {"value": entry.value, "provider": entry.provider_key}
                fields_needed.remove(f)
                ledger.record_entry(
                    session, tenant_id=job.tenant_id, operation="enrich_field",
                    campaign_id=job.campaign_id, job_id=job.id, entity_type=job.entity_type,
                    entity_id=job.entity_id, fields_requested=[f],
                    cache_status=CacheStatus.HIT.value,
                    avoided_cost=entry.avoided_cost_credits
                    or _cheapest_cost(registry, job.entity_type, f),
                    trace_id=job.trace_id,
                )
        step.detail = {"redis_hits": sorted(cache_served), "remaining": sorted(fields_needed)}
        summary["served_from_cache"] = sorted(set(summary["served_from_cache"]) | set(cache_served))

    # ── 3. Routing ────────────────────────────────────────────────────────
    with steps.run("routing") as step:
        policy = _active_policy(session, job.tenant_id, campaign)
        strategy_override = None
        allowlist = None
        budget = budget_service.get_active_budget(session, job.tenant_id, job.campaign_id)
        bstate = budget_service.check(session, budget, 0)
        if bstate.warning and bstate.degradation_mode == "cheapest":
            strategy_override = "cheapest_capable"
        elif bstate.warning and bstate.degradation_mode == "cache_only":
            fields_needed = []
        elif bstate.warning and bstate.degradation_mode == "required_fields_only" and campaign:
            fields_needed = [f for f in fields_needed if f in (campaign.required_fields or [])]
        routes = routing_engine.route_fields(
            job.entity_type, fields_needed, registry, policy,
            session=session, tenant_id=job.tenant_id, strategy_override=strategy_override,
            provider_allowlist=allowlist,
        )
        route_rows: dict[str, RoutingDecision] = {}
        for r in routes:
            row = RoutingDecision(
                tenant_id=job.tenant_id, job_id=job.id, entity_type=job.entity_type,
                entity_id=job.entity_id, field_name=r.field_name,
                candidates=[c.factors | {"provider": c.provider_key, "score": c.score}
                            for c in r.candidates],
                selected_provider=r.selected, rejected_providers=r.rejected,
                factors=r.factors, strategy=r.strategy, expected_cost_credits=r.expected_cost,
            )
            session.add(row)
            route_rows[r.field_name] = row
            if r.selected:
                ROUTING_DECISIONS.labels(provider=r.selected, strategy=r.strategy).inc()
        session.flush()
        step.detail = {
            "routes": {r.field_name: r.selected for r in routes},
            "strategy_override": strategy_override,
            "degradation": bstate.degradation_mode if bstate.warning else None,
        }

    # ── 4. Budget reservation ─────────────────────────────────────────────
    with steps.run("budget_reserve") as step:
        expected_total = sum(r.expected_cost for r in routes if r.selected)
        reserve_state = budget_service.reserve(session, budget, expected_total)
        if not reserve_state.allowed:
            job.status = JobStatus.BLOCKED_BUDGET.value
            job.decision_reasons = [*job.decision_reasons, f"budget: {reserve_state.reason}"]
            job.finished_at = datetime.now(UTC)
            summary["accepted"] = False
            summary["budget_blocked"] = True
            job.result_summary = summary
            step.detail = {"blocked": True, "reason": reserve_state.reason}
            JOBS.labels(status=job.status).inc()
            session.commit()
            return summary
        step.detail = {"reserved_credits": expected_total, "warning": reserve_state.warning}

    # ── 5. Provider calls with per-field fallback ─────────────────────────
    actual_cost = 0.0
    observations_by_field: dict[str, list[FieldObservation]] = {}
    with steps.run("provider_calls") as step:
        remaining = {r.field_name: r for r in routes if r.selected}
        plan = routing_engine.group_by_provider(list(remaining.values()))
        attempted_pairs: set[tuple[str, str]] = set()
        round_no = 0
        while plan and round_no < 4:  # bounded fallback rounds — no retry storms
            round_no += 1
            next_plan: dict[str, list[str]] = {}
            for provider_key, pfields in plan.items():
                adapter = registry.get(provider_key)
                if adapter is None or not registry.breaker(provider_key).allow():
                    _reroute(remaining, pfields, provider_key, attempted_pairs, next_plan)
                    continue
                attempted_pairs.update((provider_key, f) for f in pfields)
                result, req = execute_with_retries(
                    session, registry, adapter,
                    tenant_id=job.tenant_id, job_id=job.id, entity_type=job.entity_type,
                    entity_id=job.entity_id, identifiers=identifiers, fields=pfields,
                    trace_id=job.trace_id,
                )
                actual_cost += result.cost_credits
                returned = set()
                for f, fv in result.fields.items():
                    obs = _persist_observation(session, job, entity, f, fv, provider_key, req.id)
                    observations_by_field.setdefault(f, []).append(obs)
                    returned.add(f)
                    route_row = route_rows.get(f)
                    if route_row is not None:
                        route_row.actual_cost_credits = adapter.field_cost(job.entity_type, f)
                        if route_row.selected_provider != provider_key:
                            route_row.fallback_used = True
                            route_row.fallback_detail = {
                                **(route_row.fallback_detail or {}),
                                "served_by": provider_key,
                            }
                    ledger.record_entry(
                        session, tenant_id=job.tenant_id, operation="enrich_field",
                        campaign_id=job.campaign_id, job_id=job.id, entity_type=job.entity_type,
                        entity_id=job.entity_id, provider_key=provider_key,
                        provider_request_id=req.id, fields_requested=[f],
                        estimated_cost=adapter.field_cost(job.entity_type, f),
                        actual_cost=adapter.field_cost(job.entity_type, f),
                        outcome=result.outcome.value, cache_status=CacheStatus.MISS.value,
                        spent_on_stale=(fv.source_age_days or 0) > 180,
                        trace_id=job.trace_id,
                    )
                if not result.ok:
                    ledger.record_entry(
                        session, tenant_id=job.tenant_id, operation="enrich_call",
                        campaign_id=job.campaign_id, job_id=job.id, entity_type=job.entity_type,
                        entity_id=job.entity_id, provider_key=provider_key,
                        provider_request_id=req.id, fields_requested=pfields,
                        estimated_cost=adapter.estimate_cost(job.entity_type, pfields),
                        actual_cost=0.0, outcome=result.outcome.value,
                        cache_status=CacheStatus.MISS.value, trace_id=job.trace_id,
                    )
                missing = [f for f in pfields if f not in returned]
                if missing:
                    _reroute(remaining, missing, provider_key, attempted_pairs, next_plan)
            plan = next_plan
        # Fields nobody could fill → negative cache (avoid re-buying known-empty lookups).
        for f in remaining:  # noqa: B007 — key iteration
            if f not in observations_by_field:
                cache.set_negative(job.tenant_id, job.entity_type, entity_key, f)
        step.detail = {
            "fields_returned": sorted(observations_by_field),
            "actual_cost_credits": round(actual_cost, 4),
            "fallback_rounds": round_no - 1,
        }
        summary["providers_used"] = sorted({
            o.provider_key for obs in observations_by_field.values() for o in obs
        })

    # ── 6. Reconciliation (across today's AND historical observations) ───
    review_fields: list[str] = []
    accepted_fields: dict[str, float] = {}
    with steps.run("reconciliation") as step:
        provider_priors = {
            k: (registry.config(k).reliability_prior if registry.config(k) else 0.8)
            for k in registry.all()
        }
        outcomes: dict[str, str] = {}
        for f in observations_by_field:
            all_obs = session.execute(
                select(FieldObservation).where(
                    FieldObservation.tenant_id == job.tenant_id,
                    FieldObservation.entity_type == job.entity_type,
                    FieldObservation.entity_id == job.entity_id,
                    FieldObservation.field_name == f,
                )
            ).scalars().all()
            thresholds = staleness.get_thresholds(session, job.tenant_id, job.entity_type, f)
            result = reconcile_field(
                job.entity_type, f, all_obs,
                provider_priors=provider_priors, thresholds=thresholds,
            )
            rd = ReconciliationDecision(
                tenant_id=job.tenant_id, job_id=job.id, entity_type=job.entity_type,
                entity_id=job.entity_id, field_name=f,
                observation_ids=[o.id for o in all_obs],
                outcome=result.outcome.value,
                chosen_observation_id=result.chosen.id if result.chosen else None,
                chosen_value=(result.chosen.normalized_value or result.chosen.raw_value)
                if result.chosen else None,
                reasoning=result.reasoning, factors=result.factors,
                conflict_severity=result.conflict_severity,
            )
            session.add(rd)
            session.flush()
            outcomes[f] = result.outcome.value
            from relayiq.observability.metrics import RECONCILIATIONS

            RECONCILIATIONS.labels(outcome=result.outcome.value).inc()

            if result.outcome in (ReconciliationOutcome.AUTO_ACCEPT,
                                  ReconciliationOutcome.ACCEPT_WITH_WARNING):
                chosen = result.chosen
                chosen.is_selected = True
                score = _field_confidence(
                    session, job, f, chosen, result, provider_priors, thresholds
                )
                accepted_fields[f] = score
                state = staleness.classify(
                    session, job.tenant_id, job.entity_type, f,
                    age_days=_obs_age_days(chosen),
                )
                upsert_canonical_value(
                    session, job.tenant_id, job.entity_type, job.entity_id, f,
                    value=chosen.raw_value, normalized_value=chosen.normalized_value,
                    confidence=score, observation_id=chosen.id,
                    reconciliation_decision_id=rd.id, staleness_state=state.value,
                )
                apply_canonical_to_entity(session, job.entity_type, entity, f, chosen.raw_value)
                cache.set_field(
                    job.tenant_id, job.entity_type, entity_key, f,
                    value=chosen.raw_value, normalized_value=chosen.normalized_value,
                    provider_key=chosen.provider_key, confidence=score, observation_id=chosen.id,
                    cost_credits=float(chosen.cost_credits or 0),
                    ttl_seconds=thresholds.stale_days * 86400,
                    soft_ttl_seconds=thresholds.fresh_days * 86400,
                )
            elif result.outcome == ReconciliationOutcome.REQUIRE_REVIEW:
                review_fields.append(f)
                create_task(
                    session, job.tenant_id, entity_type=job.entity_type, entity_id=job.entity_id,
                    field_name=f, reason=result.reasoning[:300], job_id=job.id,
                    reconciliation_decision_id=rd.id,
                    confidence=_field_confidence(
                        session, job, f, result.chosen, result, provider_priors, thresholds
                    ) if result.chosen else None,
                    suggested_value=(result.chosen.normalized_value or result.chosen.raw_value)
                    if result.chosen else None,
                    suggested_observation_id=result.chosen.id if result.chosen else None,
                )
            elif result.outcome == ReconciliationOutcome.REJECT_ALL:
                for o in all_obs:
                    o.is_rejected = True
                    o.rejection_reason = "all observations failed validation"
        step.detail = {"outcomes": outcomes, "review_fields": review_fields}

    # ── 7. Entity confidence, acceptance, usable-lead ─────────────────────
    with steps.run("confidence") as step:
        from relayiq.models import ConfidenceEvaluation

        entity_result = confidence_engine.score_entity(
            accepted_fields,
            required_fields=(campaign.required_fields if campaign else None),
            requested_fields=list(job.requested_fields or []),
        )
        session.add(ConfidenceEvaluation(
            tenant_id=job.tenant_id, job_id=job.id, entity_type=job.entity_type,
            entity_id=job.entity_id, level="entity", score=entity_result.score,
            components=entity_result.components,
        ))
        entity.record_confidence = entity_result.score
        min_conf = campaign.min_confidence if campaign else settings.default_min_confidence
        auto_accept = entity_result.score >= min_conf and not review_fields
        if not auto_accept and not review_fields and accepted_fields:
            create_task(
                session, job.tenant_id, entity_type=job.entity_type, entity_id=job.entity_id,
                field_name=None,
                reason=f"entity confidence {entity_result.score:.2f} below campaign minimum {min_conf:.2f}",
                job_id=job.id, confidence=entity_result.score,
            )
        summary["entity_confidence"] = entity_result.score
        summary["accepted"] = auto_accept
        summary["review_required"] = bool(review_fields) or not auto_accept
        step.detail = {"entity_confidence": entity_result.score, "auto_accept": auto_accept}

    # ── 8. CRM gate & sync ────────────────────────────────────────────────
    with steps.run("crm_sync") as step:
        sync_status = None
        if job.entity_type == EntityType.CONTACT.value:
            suppressed = bool(session.execute(
                select(Suppression).where(
                    Suppression.tenant_id == job.tenant_id,
                    Suppression.kind == "email",
                    Suppression.value == (entity.work_email or ""),
                ).limit(1)
            ).scalar_one_or_none())
            usable, failures = quality.evaluate_usable_lead(
                session, job.tenant_id, entity,
                entity_confidence=summary.get("entity_confidence"),
                suppressed=suppressed,
                sync_eligible=bool(campaign.crm_write_enabled) if campaign else True,
            )
            summary["usable_lead"] = usable
            summary["usable_lead_failures"] = failures
        if summary.get("accepted") and (campaign.crm_write_enabled if campaign else True):
            attempt = sync_entity(
                session, job.tenant_id, job.entity_type, entity,
                job_id=job.id, min_confidence=min_conf,
                crm_write_enabled=True, dry_run=job.dry_run, trace_id=job.trace_id,
            )
            sync_status = attempt.status if attempt else "no_connection"
        summary["crm_sync"] = sync_status
        step.detail = {"sync_status": sync_status}

    # ── 9. Finalize: ledger acceptance, budget commit, job bookkeeping ────
    with steps.run("finalize") as step:
        accepted_by_provider: dict[str, bool] = {}
        for obs_list in observations_by_field.values():
            for o in obs_list:
                accepted_by_provider[o.provider_key] = (
                    accepted_by_provider.get(o.provider_key, False) or o.is_selected
                )
        ledger.mark_acceptance(session, job.id, accepted_by_provider)
        budget_service.commit_spend(session, budget, expected_total, actual_cost)
        job.actual_cost_credits = actual_cost
        filled = len(accepted_fields) + len(summary.get("served_from_cache", []))
        summary["fields_filled"] = filled
        summary["all_requested_fields_filled"] = filled >= len(job.requested_fields or [])
        job.status = (
            JobStatus.AWAITING_REVIEW.value if summary.get("review_required") else JobStatus.COMPLETED.value
        )
        job.finished_at = datetime.now(UTC)
        job.result_summary = summary
        step.detail = {"status": job.status, "cost": actual_cost, "filled": filled}
        JOBS.labels(status=job.status).inc()
        JOB_DURATION.labels(status=job.status).observe(time.monotonic() - t0)

    log.info(
        "enrichment job finished", job_id=job.id, status=job.status,
        cost_credits=actual_cost, fields_filled=summary["fields_filled"],
        review_required=summary.get("review_required"),
    )
    return summary


# ── helpers ─────────────────────────────────────────────────────────────────

def _estimate_cost(job: EnrichmentJob, registry) -> float:
    total = 0.0
    for f in job.requested_fields or []:
        total += _cheapest_cost(registry, job.entity_type, f)
    return round(total, 4)


def _cheapest_cost(registry, entity_type: str, field_name: str) -> float:
    costs = [
        a.field_cost(entity_type, field_name)
        for a in registry.all().values()
        if a.supports(entity_type, field_name)
    ]
    return min(costs) if costs else 0.0


def _reroute(remaining, failed_fields, failed_provider, attempted_pairs, next_plan) -> None:
    """Send failed/missing fields to their next untried candidate."""
    for f in failed_fields:
        route = remaining.get(f)
        if route is None:
            continue
        for cand in route.candidates:
            pk = cand.provider_key
            if pk != failed_provider and (pk, f) not in attempted_pairs:
                next_plan.setdefault(pk, []).append(f)
                break


def _persist_observation(session, job, entity, field_name, fv, provider_key, request_id):
    raw = str(fv.value) if fv.value is not None else None
    normalized = normalize_value(field_name, raw)
    validation = validate_field(field_name, normalized or raw)
    now = datetime.now(UTC)
    source_ts = now - timedelta(days=fv.source_age_days) if fv.source_age_days is not None else None
    state = staleness.classify(
        session, job.tenant_id, job.entity_type, field_name, age_days=fv.source_age_days
    )
    from relayiq.providers.registry import get_registry

    adapter = get_registry().get(provider_key)
    obs = FieldObservation(
        tenant_id=job.tenant_id, entity_type=job.entity_type, entity_id=job.entity_id,
        field_name=field_name, raw_value=raw, normalized_value=normalized,
        provider_key=provider_key, provider_request_id=request_id,
        source_timestamp=source_ts, retrieved_at=now,
        cost_credits=adapter.field_cost(job.entity_type, field_name) if adapter else 0,
        provider_latency_ms=None, provider_confidence=fv.provider_confidence,
        staleness_state=state.value, validation_results=validation,
        trace_id=job.trace_id, workflow_id=job.id,
    )
    session.add(obs)
    session.flush()
    return obs


def _obs_age_days(obs: FieldObservation) -> float | None:
    if obs.source_timestamp and obs.retrieved_at:
        return max(0.0, (obs.retrieved_at - obs.source_timestamp).total_seconds() / 86400)
    return None


def _field_confidence(session, job, field_name, chosen, recon_result, provider_priors, thresholds) -> float:
    from relayiq.models import ConfidenceEvaluation

    if chosen is None:
        return 0.0
    fresh = staleness.freshness_factor(_obs_age_days(chosen), thresholds)
    inp = confidence_engine.FieldConfidenceInput(
        provider_reliability_prior=provider_priors.get(chosen.provider_key, 0.8),
        field_quality_prior=routing_engine.quality_prior(chosen.provider_key, field_name),
        freshness_factor=fresh,
        agreement=recon_result.agreement,
        format_valid=bool((chosen.validation_results or {}).get("valid", True)),
        provider_native_confidence=chosen.provider_confidence,
        conflict_severity=recon_result.conflict_severity,
    )
    result = confidence_engine.score_field(inp)
    chosen.internal_confidence = result.score
    session.add(ConfidenceEvaluation(
        tenant_id=job.tenant_id, job_id=job.id, entity_type=job.entity_type,
        entity_id=job.entity_id, field_name=field_name, level="field",
        score=result.score, components=result.components,
    ))
    return result.score
