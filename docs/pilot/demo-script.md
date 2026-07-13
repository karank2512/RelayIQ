# RelayIQ demo recording script (2 minutes + 30s architecture talk-track)

A tight, reproducible screen-recording script. Every URL, click, and command below exists
in the current build. All data shown is **synthetic** (seeded `.test` world) and providers
are **simulators** — say so on camera; it is the project's core honesty rule.

## Pre-flight (before you hit record)

1. `docker compose up --build` — wait until API (:8000), dashboard (:5173), and the Celery
   worker are healthy. (Host-run alternative: `make dev-deps && make migrate && make seed`,
   then `make api`, `make worker`, `make dashboard`.)
2. Seed data is created by the API container (or `make seed`): demo tenant `demo`,
   127 synthetic accounts, 236 contacts, users one-per-role.
3. **Prime the review queue**: submit 5–10 contact enrichments from the Requests page (or
   rerun step 2 of the script a few times with different seeded contacts) until
   `http://localhost:5173/review` shows at least one **pending** task. Conflicting provider
   answers on `job_title` are common in the seeded world, but not guaranteed per record.
4. Open a terminal with the repo as cwd, font large enough to read at 1080p.
5. Log out of the dashboard so the recording starts at the login screen.

Login used throughout: `operator@demo.relayiq.test` / `relayiq-demo-password`
(operator outranks reviewer in the role ladder, so one login covers the whole demo).

---

## The 2-minute script

### 0:00–0:08 — Login

- URL: `http://localhost:5173/login`
- Type `operator@demo.relayiq.test` / `relayiq-demo-password`, click **Sign in**.
- Say: *"RelayIQ is an enrichment control plane — it sits between Clay-style workflows
  and your CRM and decides whether every credit is worth spending. Everything here is
  synthetic data against simulated providers; the control plane is the real code."*

### 0:08–0:18 — Overview

- Lands on `http://localhost:5173/` (Overview).
- Mouse across the stat cards: **Cost / usable lead**, **Fill rate**,
  **Redundant-call rate**, **Conflict rate**, **Review acceptance**, **CRM sync failure
  rate**, **Total spend**.
- Say: *"Every number is derived from persisted decision records — nothing hardcoded."*

### 0:18–0:38 — New enrichment (sync)

- Click **Requests** in the nav → `http://localhost:5173/requests`.
- Click **New enrichment** (top right). In the drawer:
  - Entity type: `contact`
  - Fill **Full name**, **Work email**, **Company domain** with a seeded contact
    (grab one from `http://localhost:5173/entities` beforehand and keep it on a sticky note).
  - Check requested fields: `job_title`, `seniority`, `department`, `linkedin_url`.
  - Mode: `sync — wait for result`. Leave dry-run unchecked.
  - Click **Execute enrichment**.
- The drawer shows the finished job inline: status, pre-decision, estimated vs actual
  cost, and the decision-reasons list.
- Say: *"One request just ran the whole pipeline: pre-decision, field-level routing,
  provider calls, reconciliation, confidence, CRM gate."*

### 0:38–1:00 — Entity detail → field lineage

- In the job detail, click **View entity …** →
  `http://localhost:5173/entities/contact/<entity_id>`.
- Point at the canonical-fields table: value, confidence bar, staleness badge per field.
- On the `job_title` row click **Lineage** →
  `http://localhost:5173/lineage/contact/<entity_id>/job_title`.
- Scroll once through the stages, naming them: *"Routing decisions with every scoring
  factor — candidates, costs, quality priors, health. Provider calls with latency and
  spend. Both providers' observations side by side — nothing is overwritten. Conflict
  reconciliation with human-readable reasoning. Confidence with its component breakdown.
  And the CRM sync record."*
- Expand the routing **factors** JSON and the confidence **components** JSON briefly.

### 1:00–1:20 — Review queue: accept, then reverse

- Click **Review Queue** in the nav → `http://localhost:5173/review`. Click a **pending** task.
- On `http://localhost:5173/review/<task_id>`: point at *Why this needs review*
  (reconciliation prose) and the two provider observation cards with the **suggested** badge.
- Click **Accept suggested**.
- Click **Reverse decision** → the confirm dialog appears: *"Reverse this approval? …
  Nothing is deleted — the reversal is appended to the audit history."* Click **Reverse**.
- Point at **Decision history**: both the accept and the reverse are recorded, with the
  prior state snapshot.

### 1:20–1:35 — CRM sync gate

- Click **CRM Sync** in the nav → `http://localhost:5173/crm` (tab **Sync attempts**).
- Expand a row: the per-field table shows **Before (CRM) / After / Gate / Reasons** —
  outcomes like `write`, `preserve_crm`, `secondary_property`, `require_approval`.
- Switch to the **CRM simulator contents** tab: *"This is what 'the CRM' actually holds —
  you can verify gated fields did NOT land."*

### 1:35–1:50 — Duplicate webhook replay (terminal)

Pre-stage these commands in the terminal; run them on camera.

```bash
printf '%s' '{"event":"row.created","tenant_slug":"demo","entity_type":"contact","entity":{"full_name":"Demo Duplicate","work_email":"demo.duplicate@replaytest.test","company_domain":"replaytest.test"},"requested_fields":["job_title","seniority"]}' > /tmp/wh.json

SIG=$(apps/api/.venv/bin/python -c "import time,pathlib; from relayiq.services.webhook_security import build_signature_header; print(build_signature_header('dev_only_webhook_secret', int(time.time()), pathlib.Path('/tmp/wh.json').read_bytes()))")

curl -s http://localhost:8000/v1/webhooks/enrichment -H "Content-Type: application/json" \
  -H "X-RelayIQ-Signature: $SIG" -H "X-Delivery-Id: demo-dupe-001" --data-binary @/tmp/wh.json
```

- First response: `{"accepted": true, "duplicate": false, "job_id": "..."}`.
- Press up-arrow, run the **identical** curl again (same signature, same `X-Delivery-Id`):
  `{"accepted": true, "duplicate": true, "job_id": "<same id>"}`.
- Say: *"Same signed delivery replayed — HMAC verified, delivery-ID deduped by a database
  unique constraint, same job returned, zero additional credits spent. That's e2e test 8."*
- (`dev_only_webhook_secret` is the documented dev-only default from `relayiq/config.py`;
  real deployments set `RELAYIQ_WEBHOOK_SECRETS`.)

### 1:50–2:00 — Analytics

- Back in the browser: **Analytics** → `http://localhost:5173/analytics`.
- Point at **Redundant spend avoided** (*"measured, not estimated — the ledger records the
  cost every cache hit and replay did NOT spend"*), **Spend on stale results**, spend by
  provider/field, and the **Provider × field performance** table (selected/rejected share).
- Close: *"On the seeded synthetic benchmark this control plane cut cost per usable lead
  from 13.24 credits to 4.65 — with field precision up, not down. The point is that it's
  a ledger, not a guess."*

---

## 30-second architecture talk-track

> "Under the hood it's a FastAPI service with PostgreSQL as the source of truth — 32
> tables — and Redis for caching and the Celery queue. A request flows through an
> idempotency claim, a pre-enrichment decision engine, a field-level router driven by a
> YAML policy, provider adapters behind a common SDK — here, two deterministic simulators —
> then reconciliation, a documented rules-based confidence score, a human-review workflow,
> and a per-field CRM sync gate. Every step persists its decision, so lineage, audit, and
> the cost ledger are queries, not logs. Observability is Prometheus, OpenTelemetry, and
> Grafana. The two honest caveats: providers are simulated, and the confidence score is a
> ranking signal, not a calibrated probability — measured ECE 0.09."

---

## Capture notes (OBS / asciinema)

- **OBS**: 1920×1080 @ 30 fps is plenty. Two scenes — "Browser" (window capture of the
  dashboard) and "Terminal". Use a scene transition at 1:35 and back at 1:50. Record mic
  and screen to separate tracks so you can re-take narration. Set the browser to 110–125%
  zoom so table text is legible after YouTube/Loom compression.
- **Cursor discipline**: park the mouse when talking; move only to the thing you name.
- **Terminal**: pre-run the commands once off-camera so DNS/venv warm-up doesn't eat your
  15 seconds; clear the screen before recording. `asciinema rec demo.cast` is a good
  companion artifact for the webhook segment alone (it stays copy-pasteable), but the main
  recording should be one OBS take so the duplicate-delivery moment is visibly live.
- **Retakes**: the review accept/reverse step mutates state. To re-record it cleanly,
  pick a different pending task, or re-prime the queue (pre-flight step 3).
- **Do not show**: `.env` contents or JWTs in the network tab. The webhook secret shown
  is the documented dev-only default — say that out loud.
- Trim silence in post; target 1:55–2:05 final length.
