# Portfolio copy — resume bullets, LinkedIn, launch post, deep-dive outline

Every number below is from the project's own measured artifacts
(`docs/benchmarks/results.md`, `calibration.md`, `load-test-results.md`). The cost/quality
numbers come from a **seeded synthetic benchmark** — simulated providers with known ground
truth, real control-plane code — and must always be labeled that way. No live Clay,
HubSpot, or commercial-provider claims, ever.

---

## Resume bullets (XYZ style)

1. Built RelayIQ, an enrichment control plane (FastAPI, PostgreSQL, Redis, Celery) that
   gates B2B data spend via field-level provider routing, conflict reconciliation, and a
   per-field CRM sync gate — cutting enrichment spend 66% and improving
   credits-per-usable-lead 3.3× (13.24 → 4.07) at higher field precision (0.682 → 0.773)
   on a seeded synthetic benchmark with known ground truth.

2. Designed duplicate-proof ingestion for webhook-driven workflow orchestration — HMAC
   (SHA-256, constant-time, key-rotation-aware) signature verification, delivery-ID
   deduplication via DB unique constraints, and durable idempotency records — verified by
   302 passing tests including 12 end-to-end scenarios, and measured at 0 failures across
   2,061 requests (35.4 req/s, p50 32 ms; idempotent replays p50 12 ms) under Locust load.

3. Implemented a documented rules-based confidence model with honest calibration
   measurement (Brier 0.164, ECE 0.091 against synthetic truth), publishing it as a
   ranking signal rather than a probability, and wired it into review-queue
   prioritization and CRM write-gating so low-confidence data never silently overwrites
   CRM records.

## LinkedIn project bullets

- RelayIQ is middleware between Clay-style GTM workflows and the CRM: it decides — per
  record, per field — whether to spend enrichment credits, which provider should answer,
  whether the answer can be trusted, and whether it's good enough to write to HubSpot.
- On a seeded synthetic benchmark (simulated providers, real pipeline code, known ground
  truth): cost per true usable lead fell from 13.24 credits (naive) to 4.07 with static
  field-level routing, with field precision up from 0.682 to 0.773 — routing improved
  cost *and* quality.
- Honest negative result included: learned dynamic routing **lost** to a well-tuned
  static policy at 2-provider scale (5.94 credits/usable lead) because warmup exploration
  wasn't recouped — documented, not hidden.
- Full decision lineage as a product feature: every routing factor, provider observation,
  reconciliation rationale, confidence component, review decision, and CRM gate reason is
  persisted in PostgreSQL and inspectable in the UI — enrichment becomes a ledger, not an
  invoice.
- Stack: FastAPI, PostgreSQL (32 tables), Redis (cache + Celery workflow orchestration),
  HMAC-signed webhooks with idempotent replay, confidence calibration reporting,
  Prometheus/OpenTelemetry/Grafana observability, React dashboard; 302 passing tests,
  lint-clean.

## GitHub repo description (≤350 chars)

> Enrichment control plane between Clay-style GTM workflows and your CRM: field-level
> provider routing, HMAC webhooks with idempotent replay, conflict reconciliation,
> calibration-measured confidence, human review, per-field HubSpot sync gating. FastAPI +
> PostgreSQL + Redis. 3.3× better cost/usable-lead on a synthetic benchmark.

## One-paragraph portfolio description

RelayIQ is an enrichment control plane for RevOps teams running Clay, HubSpot, and
multiple data providers — CRM integration middleware that turns enrichment spend from an
invoice into a ledger. Every record flows through pre-enrichment decisioning, field-level
provider routing, conflict reconciliation, a rules-based confidence score, an audited
human-review workflow, and a per-field CRM sync gate, with each decision persisted for
lineage and cost attribution. It's built on FastAPI, PostgreSQL, Redis, and Celery for
workflow orchestration, with HMAC-secured webhooks, durable idempotency, and
Prometheus/OpenTelemetry observability. On a seeded synthetic benchmark (simulated
providers with known ground truth driving the real pipeline), it cut cost per true usable
lead 3.3× (13.24 → 4.07 credits) while raising field precision from 0.682 to 0.773 — and
its confidence calibration report (Brier 0.164, ECE 0.091) honestly documents the score
as a ranking signal, not a probability. Providers are simulated; the HubSpot adapter is
fixture-tested with live sync not yet verified.

## Launch post (LinkedIn / X)

> I built RelayIQ: an enrichment control plane that sits between your GTM stack (Clay,
> webhooks, CSVs) and your CRM, and decides — per field — whether a credit is worth
> spending, who should answer, and whether the answer deserves to touch HubSpot.
>
> Why: RevOps teams pay for duplicate enrichment (replayed webhooks, re-run tables),
> enrichment of records their own filters would reject, re-buying data they already own,
> and provider conflicts that quietly overwrite good CRM data.
>
> What I measured (seeded synthetic benchmark — simulated providers with known ground
> truth, real pipeline code):
> • 13.24 → 4.07 credits per true usable lead (3.3×), spend down 66%
> • field precision UP from 0.682 to 0.773 — routing each field to the provider that's
>   actually good at it improves cost and quality at the same time
> • honest loss: dynamic routing underperformed static at 2-provider scale (5.94) —
>   warmup cost wasn't recouped. It's in the report.
>
> Under the hood: FastAPI + PostgreSQL + Redis/Celery workflow orchestration, HMAC-signed
> webhooks with idempotent replay (a duplicate delivery costs zero credits, p50 12 ms),
> full decision lineage, confidence calibration measured and published (ECE 0.091 — it's
> a ranking signal, not a probability), per-field CRM sync gating, OpenTelemetry +
> Prometheus observability. 302 tests passing.
>
> What it is NOT (yet): live-verified against Clay or HubSpot — providers are simulators,
> the HubSpot adapter is fixture-tested only. That's the top of the roadmap.
>
> Repo + benchmark reports + demo video in the comments.

## Technical deep-dive outline (blog post / talk)

1. **The waste problem** — four ways enrichment budgets leak (duplicates, pre-filterable
   records, re-buying owned data, silent CRM overwrites); why this is a control-plane
   problem, not a data problem.
2. **Positioning** — sidecar between Clay and HubSpot, not a replacement for either;
   the six roles (decide / route / reconcile / review / gate / account).
3. **Benchmark methodology** — synthetic world with known ground truth, deterministic
   provider personalities, real pipeline code; why "measured on synthetic" is stated on
   every chart; strategy ladder from naive → full pipeline (13.24 → 4.65 credits, with
   static routing at 4.07).
4. **Field-level routing** — policy documents, four strategies, health penalties;
   the honest dynamic-routing loss and what it implies about exploration cost at
   2-provider scale.
5. **Never overwrite: observations + reconciliation** — append-only observation storage,
   value-equivalence grouping, prose reasoning as a first-class output.
6. **Confidence you can audit** — rules-v1 components; measuring calibration (Brier
   0.164, ECE 0.091); why we published the miscalibration instead of hiding it; the
   learned-model path.
7. **Duplicate-proof ingestion** — Stripe-style HMAC verification, delivery-ID dedup via
   unique constraint, durable idempotency claims; load-measured replay behavior (p50 12 ms,
   zero spend).
8. **The CRM gate** — six outcomes, the secondary-property compromise for fresh-CRM
   conflicts, e2e verification that the negative case (data NOT landing) is observable.
9. **Operating it** — Celery vs Temporal decision (ADR-001), cost ledger design,
   Prometheus/OpenTelemetry/Grafana, load-test results and their honest limits (dev
   laptop, single process).
10. **What's not real yet** — simulated providers, unverified HubSpot/Clay integration,
    per-process rate limiting; the roadmap as a credibility instrument.
