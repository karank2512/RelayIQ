"""Integration tests for the production-hardening layer against the live app:
per-tenant webhook secrets, login rate limiting, security headers, metrics token,
and body-size caps."""

import time
import uuid

import fakeredis

from relayiq.config import get_settings
from relayiq.models import Tenant
from relayiq.services.ratelimit import reset_rate_limiter
from relayiq.services.webhook_security import build_signature_header


class TestPerTenantWebhookSecrets:
    def _payload(self, env, contact) -> bytes:
        import json

        t = contact["truth"]
        return json.dumps({
            "event": "enrichment.requested", "tenant_slug": env.slug,
            "entity_type": "contact",
            "entity": {"work_email": t["work_email"], "full_name": t["full_name"]},
            "requested_fields": ["job_title"],
        }).encode()

    def _set_tenant_secret(self, helpers, tenant_id: str, secret: str) -> None:
        session = helpers.session()
        try:
            tenant = session.get(Tenant, tenant_id)
            tenant.settings = {**(tenant.settings or {}), "webhook_secrets": [secret]}
            session.commit()
        finally:
            session.close()

    def test_global_secret_cannot_authorize_scoped_tenant(
        self, client, make_env, helpers, world, sync_worker
    ):
        env = make_env("whsec")
        tenant_secret = f"tenant-scoped-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
        self._set_tenant_secret(helpers, env.tenant_id, tenant_secret)
        contact = next(c for c in world["contacts"] if c["truth"].get("work_email"))
        body = self._payload(env, contact)

        # Global (default) secret must now be REJECTED for this tenant.
        global_secret = get_settings().webhook_secret_list[0]
        r = client.post(
            "/v1/webhooks/enrichment", content=body,
            headers={"X-RelayIQ-Signature":
                     build_signature_header(global_secret, int(time.time()), body),
                     "X-Delivery-Id": f"g-{uuid.uuid4().hex[:8]}"},
        )
        assert r.status_code == 401
        assert r.json()["reason"] == "invalid_signature"

        # The tenant's own secret works.
        r = client.post(
            "/v1/webhooks/enrichment", content=body,
            headers={"X-RelayIQ-Signature":
                     build_signature_header(tenant_secret, int(time.time()), body),
                     "X-Delivery-Id": f"t-{uuid.uuid4().hex[:8]}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] is True

    def test_tenant_without_own_secret_uses_global(self, client, env, helpers, world, sync_worker):
        contact = next(c for c in world["contacts"] if c["truth"].get("work_email"))
        body = self._payload(env, contact)
        r = client.post(
            "/v1/webhooks/enrichment", content=body,
            headers=helpers.webhook_headers(body, f"gd-{uuid.uuid4().hex[:8]}"),
        )
        assert r.status_code == 200, r.text


class TestLoginRateLimit:
    def test_login_bruteforce_hits_429(self, client, env, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
        reset_rate_limiter(fakeredis.FakeRedis(decode_responses=True))
        try:
            statuses = []
            for _ in range(6):
                r = client.post("/v1/auth/login", json={
                    "email": f"operator@{env.slug}.relayiq.test", "password": "wrong-password-xx",
                })
                statuses.append(r.status_code)
            assert statuses[:3] == [401, 401, 401]
            assert statuses[3:] == [429, 429, 429]
            assert "Retry-After" in r.headers
        finally:
            reset_rate_limiter()  # back to the shared client / disabled-by-env limits


class TestHttpHardening:
    def test_security_headers_present(self, client):
        r = client.get("/healthz")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "Strict-Transport-Security" in r.headers

    def test_oversized_body_rejected(self, client, env):
        r = client.post(
            "/v1/enrichment/execute",
            content=b"{}",
            headers={**env.headers("operator"),
                     "Content-Type": "application/json",
                     "Content-Length": str(50 * 1024 * 1024)},
        )
        assert r.status_code == 413

    def test_correlation_id_garbage_regenerated(self, client):
        r = client.get("/healthz", headers={"X-Correlation-Id": "<script>alert(1)</script>"})
        assert "<" not in r.headers["X-Correlation-Id"]
        r2 = client.get("/healthz", headers={"X-Correlation-Id": "my-trace-1234"})
        assert r2.headers["X-Correlation-Id"] == "my-trace-1234"

    def test_metrics_token_enforced(self, client, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "metrics_token", "secret-metrics-token-for-test")
        r = client.get("/metrics")
        assert r.status_code == 401
        r = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = client.get("/metrics",
                       headers={"Authorization": "Bearer secret-metrics-token-for-test"})
        assert r.status_code == 200
        assert b"relayiq_http_requests_total" in r.content
