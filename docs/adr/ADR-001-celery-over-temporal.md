# ADR-001: Celery over Temporal for workflow execution

## Status

Accepted

## Date

2026-07-11

## Context

Every enrichment request runs a multi-step pipeline (pre-decision → routing → cache →
budget reservation → provider calls with fallback → reconciliation → confidence →
review/CRM gating → finalize). Steps have side effects that cost real money (provider
credits), so the execution substrate must survive worker crashes, tolerate redelivery,
and never double-spend. Candidates were a durable workflow engine (Temporal), a task
queue (Celery), or in-process execution only.

The MVP constraints: a single Python service (`apps/api`, package `relayiq`), Redis
already present for caching, one team, and jobs that complete in seconds (Celery
`task_time_limit=120` is generous — the sync API path runs the same pipeline inline).

## Decision

Use **Celery with a Redis broker** (`relayiq/workers/celery_app.py`), and make
durability the *application's* job rather than the queue's:

- The orchestrator (`relayiq/engines/orchestrator.py::run_enrichment_job`) is an
  **idempotent re-entry point**: it refuses to re-run a job that has left the
  `received`/`queued` states and instead returns the stored `result_summary`. Worker
  crashes and Celery redeliveries are therefore safe.
- Each pipeline step is persisted as a `WorkflowStep` row (`_StepRecorder` in the
  orchestrator, `workflow_steps` table) with status, timings, and detail — the audit
  value of a workflow engine's event history, in Postgres.
- Celery is configured for at-least-once semantics: `task_acks_late=True`,
  `worker_prefetch_multiplier=1`, `task_time_limit=120`, `task_soft_time_limit=90`,
  two queues (`enrichment`, `sync`) routed in `celery_app.conf.task_routes`.
- Failure handling lives in `relayiq/workers/tasks.py`: `run_enrichment_task` retries
  up to 3 times with exponential countdown (`2 ** retries`), then parks the job as
  `failed`. `retry_crm_sync_task` retries transient CRM failures up to 5 times with
  `min(300, 5 * 2**retries)` backoff.
- The same orchestrator code runs inline for `mode: "sync"` API calls
  (`relayiq/api/routers/enrichment.py::execute`) — no workflow-engine-only code path.

## Alternatives considered

- **Temporal** — genuine durable execution, replayable histories, first-class timers
  and signals. Rejected for the MVP: it adds a second stateful service (Temporal server
  + its own database), a worker programming model that constrains SQLAlchemy session
  handling, and operational surface the project does not yet need for
  seconds-long, mostly-linear pipelines.
- **AWS Step Functions / managed orchestrators** — ties local development to cloud
  emulators; the whole stack currently runs from one `docker-compose.yml`.
- **RQ / Dramatiq / Arq** — comparable to Celery with smaller ecosystems; Celery's
  routing, late-ack, and retry primitives were the deciding features.
- **FastAPI `BackgroundTasks` only** — no redelivery on process death; unacceptable
  when a lost task means a paid-for provider result is never reconciled.

## Consequences

- Durability guarantees are only as strong as the orchestrator's own idempotency and
  the DB constraints backing it (see ADR-007). This is enforced by convention and
  tests, not by an engine.
- Step boundaries commit their work (`_StepRecorder.__exit__` calls
  `session.commit()`), so partial progress survives a crash and re-entry does not
  repeat completed side effects within a *finished* job — but a crash mid-step can
  re-run that step's provider calls on retry (bounded by Celery `max_retries=3` and
  by per-call ledger rows making any duplicate spend visible).
- No durable timers: scheduled refresh ("mark_refresh" gate outcomes) has no built-in
  scheduler; it would need Celery beat or an external cron.
- The Redis broker in `docker-compose.yml` runs with `--appendonly no`; queued (not yet
  started) tasks are lost if Redis restarts. Jobs remain in Postgres in `queued` status
  and can be re-dispatched, but nothing currently does so automatically.

## Risks

- **Broker loss**: queued tasks vanish with Redis; a reconciliation sweep for stuck
  `queued` jobs is not implemented.
- **At-least-once, not exactly-once**: a crash between a provider call and its commit
  can spend twice on retry; the cost ledger records both attempts, so the failure mode
  is visible-but-paid rather than silent.
- **Long-running future workflows** (multi-day review SLAs, cross-system sagas) would
  strain this model.

## Revisit conditions

- Pipelines gain human-in-the-loop waits, durable timers, or fan-out/fan-in beyond
  the current per-field fallback loop.
- Double-spend incidents traced to mid-step crashes exceed the tolerance the ledger
  makes visible.
- More than one service needs to participate in a single workflow.
