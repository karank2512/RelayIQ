# RelayIQ architecture overview

RelayIQ is an **enrichment control plane**: middleware between GTM tooling
(Clay-style workflows, webhooks, batches) and the CRM that decides — per record, per
field — whether to spend enrichment credits, which provider answers each field,
whether the answers can be trusted, and whether they may be written to the CRM.
Providers in this build are **simulated** (ADR-009); the control plane around them is
the real production code.

## System components

```mermaid
flowchart LR
    Clay["Clay / webhooks / batch / CSV"] -->|"HMAC-signed webhooks,\nidempotent REST"| API
    Dashboard["Dashboard (React/Vite, apps/dashboard)"] -->|"JWT REST"| API

    subgraph Backend["apps/api (one image: API + worker)"]
      API["FastAPI app\nrelayiq/main.py + api/routers/*"]
      Worker["Celery worker\nrelayiq/workers/*"]
      Orch["Orchestrator + engines\nrelayiq/engines/*"]
      API -->|"sync mode: run inline"| Orch
      API -->|"async mode: .delay(job_id)"| Worker
      Worker --> Orch
    end

    Orch --> Redis[("Redis\nfield cache riq:{ver}:{tenant}:*\n+ Celery broker")]
    Orch --> PG[("PostgreSQL\nsource of truth, 32 tables\nrelayiq/models/*")]
    API --> PG
    Orch --> Providers["Provider adapters\nrelayiq/providers/*\n(Alpha & Beta simulators)"]
    Orch --> CRM["CRM adapters\nrelayiq/services/crm.py\n(simulator default; HubSpot fixture-tested)"]
    Worker -->|"signed result callbacks\n(SSRF re-validated)"| Clay
    API --> Obs["Prometheus /metrics\nOpenTelemetry traces\nstructlog JSON"]
```

- **API** (`relayiq/main.py`, `relayiq/api/routers/*`) — FastAPI app with an
  observability middleware (correlation IDs, `relayiq_http_*` metrics), JWT auth
  re-verified against the DB per request (`api/deps.py`), and routers for
  enrichment, entities, review, CRM, admin, metrics, auth, and webhooks.
- **Worker** (`relayiq/workers/`) — Celery tasks running the *same* orchestrator code
  as the sync API path; also delivers HMAC-signed result callbacks with SSRF
  re-validation at send time.
- **Orchestrator + engines** (`relayiq/engines/`) — the pipeline below; each step is
  persisted as a `workflow_steps` row and committed at step boundaries so partial
  failures leave an inspectable trail.
- **PostgreSQL** — source of truth (ADR-002): 32 tables covering canonical entities,
  observations, decisions, ledger, review, CRM sync, webhooks
  (see `docs/architecture/canonical-schema.md`).
- **Redis** — tenant- and schema-version-scoped field cache with negative entries and
  stale-while-revalidate TTLs (ADR-003), plus the Celery broker/result backend.
- **Providers** — adapter SDK (`providers/base.py`) with two deterministic simulator
  personalities (`providers/simulators.py`); registry + circuit breakers in
  `providers/registry.py` (see `docs/architecture/provider-sdk.md`).
- **CRM** — adapter interface with a fully working simulator and a fixture-tested
  (not live-verified) HubSpot adapter; all writes pass the per-field sync gate
  (`services/crm_gate.py`, ADR-008).
- **Dashboard** (`apps/dashboard`) — React UI over the REST API (review queue, cost
  ledger, job/lineage inspection).

## The enrichment pipeline

One `EnrichmentJob` flows through nine persisted steps. The step names below are
exactly the `step_name` values written by `relayiq/engines/orchestrator.py`
(`run_enrichment_job`) into `workflow_steps`:

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (API sync / Celery worker)
    participant O as Orchestrator (engines/orchestrator.py)
    participant PG as PostgreSQL
    participant R as Redis (FieldCache)
    participant P as Providers (via registry)
    participant CRM as CRM adapter

    C->>O: run_enrichment_job(job_id)
    O->>PG: job -> RUNNING (idempotent re-entry guard)

    rect rgb(240, 240, 240)
    Note over O: step 1 — pre_decision
    O->>PG: budget check, suppressions, fresh canonical fields,<br/>campaign filters (engines/decision.py)
    PG-->>O: enrich / reject / skip / use_cache / review / blocked
    end

    rect rgb(240, 240, 240)
    Note over O: step 2 — cache_check
    O->>R: get_field per remaining field
    R-->>O: HIT (serve, ledger avoided-cost) / MISS
    end

    rect rgb(240, 240, 240)
    Note over O: step 3 — routing
    O->>PG: score candidates per field (engines/routing.py)<br/>persist routing_decisions (+budget degradation override)
    end

    rect rgb(240, 240, 240)
    Note over O: step 4 — budget_reserve
    O->>PG: guarded UPDATE: spent+reserved+X <= limit
    PG-->>O: reserved (or job -> blocked_budget, stop)
    end

    rect rgb(240, 240, 240)
    Note over O: step 5 — provider_calls
    O->>P: batched call per provider (bounded retries, breaker)
    P-->>O: field values + cost (provider_requests/responses persisted)
    O->>P: per-field fallback rounds (max 4) + stale cross-check
    O->>R: set_negative for unfillable fields
    end

    rect rgb(240, 240, 240)
    Note over O: step 6 — reconciliation
    O->>PG: reconcile all observations per field (engines/reconciliation.py)<br/>auto_accept -> canonical_field_values + cache.set_field<br/>require_review -> review task
    end

    rect rgb(240, 240, 240)
    Note over O: step 7 — confidence
    O->>PG: field + entity scores (engines/confidence.py, rules-v1)<br/>auto-accept vs review threshold
    end

    rect rgb(240, 240, 240)
    Note over O: step 8 — crm_sync
    O->>PG: usable-lead evaluation (services/quality.py)
    O->>CRM: per-field gate then write (services/crm_gate.py, crm_sync.py)
    end

    rect rgb(240, 240, 240)
    Note over O: step 9 — finalize
    O->>PG: ledger acceptance flags, budget commit_spend,<br/>job -> completed / awaiting_review
    end

    O-->>C: result_summary (fields_filled, confidence, usable_lead, cost)
```

Terminal pre-decisions (anything but `enrich`) end the job after step 1 with
zero provider spend — cache-served fields get zero-cost ledger entries recording the
avoided cost. A failed budget reservation ends the job after step 4 as
`blocked_budget`.

## Design invariants

- **Observations are never overwritten** (ADR-006): every provider answer is a
  `field_observations` row; canonical values are *selected*, and reviewer actions are
  append-only and reversible.
- **Every cost-bearing operation gets a ledger row** (`services/ledger.py`),
  including cache hits (with measured avoided cost) — "redundant cost avoided" is
  measured, not estimated.
- **Idempotency is durable** (ADR-007): DB unique constraints, not in-memory state,
  arbitrate replays for API requests, webhook deliveries, and CRM syncs.
- **Same code sync and async**: the API's `mode: "sync"` runs the orchestrator
  inline; `mode: "async"` and webhooks run it in Celery — behavior and persisted
  trail are identical.
- **Explainability**: routing candidates/scores, reconciliation reasoning, confidence
  components, and CRM gate reasons are all persisted per decision.

## Measured behavior (for orientation, see docs/benchmarks/)

On the seeded synthetic benchmark (simulated providers, real control plane;
`docs/benchmarks/results.md`): cost per true usable lead 13.24 credits naive → 4.07
with static field routing → 4.65 for the full pipeline (which buys stale
cross-checks and routes ~5% of records to review); field precision 0.682 → 0.773 /
0.784. Dynamic routing honestly *lost* (5.94) at 2-provider scale due to warmup
cost. Load behavior on a dev laptop (`docs/benchmarks/load-test-results.md`): 2,061
requests, 0 failures, 35.4 req/s sustained, p50 32 ms / p95 580 ms, idempotent
replays p50 12 ms.
