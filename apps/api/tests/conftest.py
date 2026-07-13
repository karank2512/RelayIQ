"""Shared fixtures for the integration and e2e suites.

These tests run against the REAL local Postgres (localhost:5433) and Redis
(localhost:6379). Isolation rules:

- The database is never wiped. Every pytest session creates its OWN tenants with
  uuid slugs (plus per-tenant users, campaigns, budgets, CRM connections).
- The provider_configs table is global (key is unique) — the existing 'alpha'/'beta'
  rows are reused when present and created only when missing (get-or-create).
- Provider results come from the deterministic simulators reading
  data/synthetic_world.json (RFC-2606 .test domains, known truth per contact).
"""

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

# Environment must be set BEFORE importing relayiq (settings are lru_cached).
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://relayiq:relayiq_dev_password@localhost:5433/relayiq"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["RELAYIQ_SYNTHETIC_WORLD_PATH"] = str(API_ROOT / "data" / "synthetic_world.json")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from relayiq.canonical.normalize import values_equivalent  # noqa: E402
from relayiq.config import get_settings  # noqa: E402
from relayiq.db import get_sessionmaker  # noqa: E402
from relayiq.main import app  # noqa: E402
from relayiq.models import (  # noqa: E402
    CanonicalFieldValue,
    CostLedgerEntry,
    CrmConnection,
    ProviderConfig,
    ProviderRequest,
    Tenant,
    User,
)
from relayiq.security import hash_password  # noqa: E402
from relayiq.services.cache import FieldCache  # noqa: E402
from relayiq.services.webhook_security import build_signature_header  # noqa: E402

TEST_PASSWORD = "relayiq-itest-password"
ROLES = ("admin", "operator", "reviewer", "analyst")


# ── tenant environments ─────────────────────────────────────────────────────

@dataclass
class TenantEnv:
    tenant_id: str
    slug: str
    tokens: dict

    def headers(self, role: str = "operator", idempotency_key: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.tokens[role]}"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h


def _ensure_providers(session) -> None:
    """Get-or-create the global simulator provider rows (shared table — never dropped)."""
    for key, adapter, prior in (("alpha", "simulator.alpha", 0.86), ("beta", "simulator.beta", 0.9)):
        row = session.execute(
            select(ProviderConfig).where(ProviderConfig.key == key)
        ).scalar_one_or_none()
        if row is None:
            session.add(ProviderConfig(
                key=key, display_name=f"Provider {key.title()} (simulated)", adapter=adapter,
                reliability_prior=prior, timeout_ms=8000, max_retries=2,
            ))
    session.commit()


def _make_env(client: TestClient, prefix: str = "itest") -> TenantEnv:
    slug = f"{prefix}-{uuid.uuid4().hex[:10]}"
    session = get_sessionmaker()()
    try:
        _ensure_providers(session)
        tenant = Tenant(name=f"Test tenant {slug}", slug=slug, settings={})
        session.add(tenant)
        session.flush()
        for role in ROLES:
            session.add(User(
                tenant_id=tenant.id, email=f"{role}@{slug}.relayiq.test",
                password_hash=hash_password(TEST_PASSWORD), role=role, full_name=role.title(),
            ))
        session.add(CrmConnection(
            tenant_id=tenant.id, system="simulator", display_name="CRM Simulator", mode="simulator",
        ))
        session.commit()
        tenant_id = tenant.id
    finally:
        session.close()

    tokens = {}
    for role in ROLES:
        r = client.post("/v1/auth/login", json={
            "email": f"{role}@{slug}.relayiq.test", "password": TEST_PASSWORD,
        })
        assert r.status_code == 200, r.text
        tokens[role] = r.json()["access_token"]
    return TenantEnv(tenant_id=tenant_id, slug=slug, tokens=tokens)


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def world() -> dict:
    return json.loads((API_ROOT / "data" / "synthetic_world.json").read_text())


@pytest.fixture(scope="session")
def env(client) -> TenantEnv:
    """Primary tenant shared by most tests (isolated per pytest session by uuid slug)."""
    return _make_env(client, "itest")


@pytest.fixture(scope="session")
def make_env(client):
    """Factory for extra tenants (cross-tenant tests, destructive scenarios)."""
    return lambda prefix="extra": _make_env(client, prefix)


@pytest.fixture(scope="session")
def contact_pool(world):
    """Session-wide iterator over clean world contacts (no tags, has email) so each
    test draws distinct entities and canonical-store state never leaks across tests."""
    def gen():
        for c in world["contacts"]:
            if not c["tags"] and c["truth"].get("work_email"):
                yield c
    return gen()


@pytest.fixture
def sync_worker(monkeypatch):
    """Run async (Celery) enrichment jobs synchronously with a fresh DB session, so
    webhook/batch paths complete inline without a worker process."""
    from relayiq.engines.orchestrator import run_enrichment_job
    from relayiq.workers import tasks

    def _delay(job_id: str):
        session = get_sessionmaker()()
        try:
            return run_enrichment_job(session, job_id)
        finally:
            session.close()

    monkeypatch.setattr(tasks.run_enrichment_task, "delay", _delay)
    return _delay


# ── helpers ──────────────────────────────────────────────────────────────────

class Helpers:
    password = TEST_PASSWORD

    @staticmethod
    def session():
        return get_sessionmaker()()

    @staticmethod
    def contact_payload(contact: dict, country: str | None = None) -> dict:
        t = contact["truth"]
        p = {
            "first_name": t.get("first_name"), "last_name": t.get("last_name"),
            "full_name": t.get("full_name"), "work_email": t.get("work_email"),
        }
        c = country if country is not None else t.get("country")
        if c:
            p["country"] = c
        return {k: v for k, v in p.items() if v is not None}

    @staticmethod
    def enrich(client, env: TenantEnv, payload: dict, fields: list[str], *,
               campaign_id: str | None = None, idempotency_key: str | None = None,
               mode: str = "sync", dry_run: bool = False):
        body = {
            "entity_type": "contact", "entity": payload, "requested_fields": fields,
            "mode": mode, "dry_run": dry_run,
        }
        if campaign_id:
            body["campaign_id"] = campaign_id
        return client.post(
            "/v1/enrichment/execute", json=body,
            headers=env.headers("operator", idempotency_key=idempotency_key),
        )

    @classmethod
    def enrich_until(cls, client, env: TenantEnv, pool, fields: list[str], predicate,
                     max_attempts: int = 30):
        """Enrich successive clean world contacts until a job satisfies `predicate`.
        Simulators are deterministic, so which contact succeeds is stable run-to-run."""
        last = None
        for _ in range(max_attempts):
            contact = next(pool)
            r = cls.enrich(client, env, cls.contact_payload(contact), fields)
            assert r.status_code == 201, r.text
            job = r.json()
            last = job
            if predicate(job):
                return job, contact
        pytest.fail(f"no job satisfied predicate within {max_attempts} attempts; last={last}")

    @staticmethod
    def expire_canonical(tenant_id: str, entity_type: str, entity_id: str,
                         field: str | None = None, days: int = 200) -> int:
        session = get_sessionmaker()()
        try:
            q = select(CanonicalFieldValue).where(
                CanonicalFieldValue.tenant_id == tenant_id,
                CanonicalFieldValue.entity_type == entity_type,
                CanonicalFieldValue.entity_id == entity_id,
            )
            if field:
                q = q.where(CanonicalFieldValue.field_name == field)
            rows = session.execute(q).scalars().all()
            for row in rows:
                row.last_verified_at = datetime.now(UTC) - timedelta(days=days)
            session.commit()
            return len(rows)
        finally:
            session.close()

    @staticmethod
    def invalidate_cache(tenant_id: str, entity_type: str, entity_key: str) -> int:
        return FieldCache().invalidate_entity(tenant_id, entity_type, entity_key)

    @staticmethod
    def provider_request_count(tenant_id: str, entity_id: str) -> int:
        session = get_sessionmaker()()
        try:
            return len(session.execute(
                select(ProviderRequest).where(
                    ProviderRequest.tenant_id == tenant_id,
                    ProviderRequest.entity_id == entity_id,
                )
            ).scalars().all())
        finally:
            session.close()

    @staticmethod
    def ledger_entries(job_id: str) -> list[dict]:
        session = get_sessionmaker()()
        try:
            rows = session.execute(
                select(CostLedgerEntry).where(CostLedgerEntry.job_id == job_id)
            ).scalars().all()
            return [
                {
                    "operation": r.operation, "provider_key": r.provider_key,
                    "fields": list(r.fields_requested or []),
                    "actual": float(r.actual_cost_credits or 0),
                    "avoided": float(r.avoided_cost_credits or 0),
                    "cache_status": r.cache_status, "outcome": r.outcome,
                }
                for r in rows
            ]
        finally:
            session.close()

    @staticmethod
    def set_routing_policy(client, env: TenantEnv, providers: list[str],
                           field_key: str = "contact.job_title",
                           name: str = "itest-flip") -> None:
        doc = {
            "version": 1,
            "defaults": {"strategy": "balanced", "fallback": True, "max_candidates": 3},
            "fields": {field_key: {"providers": providers, "strategy": "quality_first"}},
        }
        r = client.post(
            "/v1/admin/routing-policies",
            json={"name": name, "document": doc, "activate": True},
            headers=env.headers("operator"),
        )
        assert r.status_code == 201, r.text

    @classmethod
    def ensure_review_task(cls, client, env: TenantEnv, world: dict,
                           max_candidates: int = 30) -> tuple[dict, dict, dict]:
        """Produce a pending review task on job_title by enriching a contact whose two
        provider views genuinely disagree: first via beta only, then (after expiring the
        canonical value and the Redis cache) via alpha only, so reconciliation sees a
        real conflict. Returns (task, second_job, contact); caps attempts at
        `max_candidates`."""
        candidates = []
        for c in world["contacts"]:
            t = c["truth"]
            if not t.get("work_email") or "suppressed" in c["tags"]:
                continue
            a = (c["provider_views"].get("alpha") or {}).get("job_title")
            b = (c["provider_views"].get("beta") or {}).get("job_title")
            if not a or not b or a.get("value") is None or b.get("value") is None:
                continue
            if not values_equivalent("job_title", str(a["value"]), str(b["value"])):
                candidates.append(c)

        for contact in candidates[:max_candidates]:
            payload = cls.contact_payload(contact)
            cls.set_routing_policy(client, env, ["beta"])
            r1 = cls.enrich(client, env, payload, ["job_title"])
            assert r1.status_code == 201, r1.text
            j1 = r1.json()
            if "beta" not in (j1["result_summary"].get("providers_used") or []):
                continue  # simulator failure or coverage gap — try the next candidate
            entity_id = j1["entity_id"]
            cls.set_routing_policy(client, env, ["alpha"])
            cls.expire_canonical(env.tenant_id, "contact", entity_id, "job_title")
            cls.invalidate_cache(env.tenant_id, "contact", payload["work_email"])
            r2 = cls.enrich(client, env, payload, ["job_title"])
            assert r2.status_code == 201, r2.text
            j2 = r2.json()
            q = client.get(
                "/v1/review/queue", params={"status": "pending", "limit": 200},
                headers=env.headers("reviewer"),
            )
            assert q.status_code == 200, q.text
            for task in q.json()["items"]:
                if task["entity_id"] == entity_id and task["field_name"] == "job_title":
                    return task, j2, contact
        pytest.fail(f"no review task produced within {max_candidates} conflict candidates")

    @staticmethod
    def webhook_headers(body: bytes, delivery_id: str | None, *,
                        ts: int | None = None, secret: str | None = None,
                        signature: str | None = None) -> dict:
        secret = secret or get_settings().webhook_secret_list[0]
        ts = int(time.time()) if ts is None else ts
        h = {
            "Content-Type": "application/json",
            "X-RelayIQ-Signature": signature or build_signature_header(secret, ts, body),
        }
        if delivery_id:
            h["X-Delivery-Id"] = delivery_id
        return h

    @staticmethod
    def webhook_payload(env: TenantEnv, contact: dict, fields: list[str]) -> bytes:
        t = contact["truth"]
        return json.dumps({
            "event": "enrichment.requested",
            "tenant_slug": env.slug,
            "entity_type": "contact",
            "entity": {
                "first_name": t.get("first_name"), "last_name": t.get("last_name"),
                "full_name": t.get("full_name"), "work_email": t.get("work_email"),
            },
            "requested_fields": fields,
        }).encode()


@pytest.fixture(scope="session")
def helpers():
    return Helpers
