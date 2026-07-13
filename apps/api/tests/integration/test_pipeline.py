"""Integration: enrichment pipeline against real Postgres + Redis — cache reuse, cost
ledger persistence, webhook security path, provider adapters, HubSpot fixture adapter."""

import json
import time
import uuid

import httpx
import respx

from relayiq.services.crm import HubSpotCrmAdapter


class TestPipelineAndCache:
    def test_second_request_serves_from_store_zero_cost(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        payload = helpers.contact_payload(contact)
        r1 = helpers.enrich(client, env, payload, ["job_title", "seniority"])
        assert r1.status_code == 201
        j1 = r1.json()
        if j1["status"] not in ("completed", "awaiting_review"):
            # deterministic simulators: pick a contact that fills
            j1, contact = helpers.enrich_until(
                client, env, contact_pool, ["job_title", "seniority"],
                lambda j: j["status"] in ("completed", "awaiting_review") and j["actual_cost_credits"] > 0,
            )
            payload = helpers.contact_payload(contact)
        calls_before = helpers.provider_request_count(env.tenant_id, j1["entity_id"])

        r2 = helpers.enrich(client, env, payload, ["job_title", "seniority"])
        assert r2.status_code == 201
        j2 = r2.json()
        assert j2["pre_decision"] == "use_cache"
        assert j2["actual_cost_credits"] == 0.0
        assert helpers.provider_request_count(env.tenant_id, j1["entity_id"]) == calls_before

    def test_every_paid_field_has_ledger_entry(self, client, env, helpers, contact_pool):
        job, _ = helpers.enrich_until(
            client, env, contact_pool, ["job_title", "department"],
            lambda j: j["actual_cost_credits"] > 0,
        )
        entries = helpers.ledger_entries(job["id"])
        paid = [e for e in entries if e["actual"] > 0]
        assert paid, "expected paid ledger entries"
        assert sum(e["actual"] for e in entries) == round(job["actual_cost_credits"], 4)
        assert all(e["provider_key"] for e in paid)

    def test_field_level_routing_splits_providers(self, client, env, helpers, contact_pool):
        """Contact fields route to beta (quality_first); an account request routes
        company fields to alpha (cheapest_capable)."""
        # Beta is primary for people fields; a contact Beta lacks falls back to Alpha,
        # so search for a job Beta actually served (deterministic across runs).
        job, _ = helpers.enrich_until(
            client, env, contact_pool, ["job_title", "seniority"],
            lambda j: "beta" in (j["result_summary"].get("providers_used") or []),
        )

        r = client.post(
            "/v1/enrichment/execute",
            json={"entity_type": "account",
                  "entity": {"name": "Routing Probe Co",
                             "root_domain": f"routingprobe{uuid.uuid4().hex[:6]}.test"},
                  "requested_fields": ["industry", "employee_count"], "mode": "sync"},
            headers=env.headers("operator"),
        )
        assert r.status_code == 201
        # Unknown domain → providers return no fields, but routing decisions persist
        lineage = client.get(
            f"/v1/entities/account/{r.json()['entity_id']}/lineage/employee_count",
            headers=env.headers("operator"),
        ).json()
        selected = [rd["selected_provider"] for rd in lineage["routing_decisions"]]
        assert "alpha" in selected

    def test_dry_run_estimates_without_spend(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        r = client.post(
            "/v1/enrichment/decide",
            json={"entity_type": "contact", "entity": helpers.contact_payload(contact),
                  "requested_fields": ["job_title", "seniority"]},
            headers=env.headers("operator"),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["estimated_cost_credits"] > 0
        assert body["decision"] in ("enrich", "use_cache")


class TestWebhooks:
    FIELDS = ["job_title", "seniority"]

    def test_valid_signature_processes(self, client, env, helpers, contact_pool, sync_worker):
        contact = next(contact_pool)
        body = helpers.webhook_payload(env, contact, self.FIELDS)
        r = client.post("/v1/webhooks/enrichment", content=body,
                        headers=helpers.webhook_headers(body, f"d-{uuid.uuid4().hex[:8]}"))
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] and not r.json()["duplicate"]
        assert r.json()["job_id"]

    def test_invalid_signature_401(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        body = helpers.webhook_payload(env, contact, self.FIELDS)
        # Fresh timestamp + wrong digest → the signature check itself must reject
        headers = helpers.webhook_headers(
            body, "d-bad", signature=f"t={int(time.time())},v1={'0' * 64}"
        )
        r = client.post("/v1/webhooks/enrichment", content=body, headers=headers)
        assert r.status_code == 401
        assert r.json()["reason"] == "invalid_signature"

    def test_tampered_body_401(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        body = helpers.webhook_payload(env, contact, self.FIELDS)
        headers = helpers.webhook_headers(body, "d-tamper")
        tampered = body.replace(b"enrichment.requested", b"enrichment.injected!")
        r = client.post("/v1/webhooks/enrichment", content=tampered, headers=headers)
        assert r.status_code == 401

    def test_stale_timestamp_400(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        body = helpers.webhook_payload(env, contact, self.FIELDS)
        headers = helpers.webhook_headers(body, "d-stale", ts=int(time.time()) - 4000)
        r = client.post("/v1/webhooks/enrichment", content=body, headers=headers)
        assert r.status_code == 400
        assert r.json()["reason"] == "stale_timestamp"

    def test_missing_delivery_id_400(self, client, env, helpers, contact_pool):
        contact = next(contact_pool)
        body = helpers.webhook_payload(env, contact, self.FIELDS)
        r = client.post("/v1/webhooks/enrichment", content=body,
                        headers=helpers.webhook_headers(body, None))
        assert r.status_code == 400

    def test_unknown_tenant_404(self, client, helpers, world):
        contact = next(c for c in world["contacts"] if c["truth"].get("work_email"))
        body = json.dumps({
            "event": "enrichment.requested", "tenant_slug": "no-such-tenant",
            "entity_type": "contact",
            "entity": {"work_email": contact["truth"]["work_email"]},
            "requested_fields": ["job_title"],
        }).encode()
        r = client.post("/v1/webhooks/enrichment", content=body,
                        headers=helpers.webhook_headers(body, "d-ghost"))
        assert r.status_code == 404


class TestHubSpotAdapter:
    """Adapter tested against v3-API-shaped fixtures. Live sync NOT verified (no creds)."""

    BASE = "https://hubspot.fixture.test"

    def _adapter(self) -> HubSpotCrmAdapter:
        return HubSpotCrmAdapter(access_token="fixture-token", base_url=self.BASE,
                                 client=httpx.Client(base_url=self.BASE,
                                                     headers={"Authorization": "Bearer fixture-token"}))

    @respx.mock
    def test_create_contact(self):
        route = respx.post(f"{self.BASE}/crm/v3/objects/contacts").mock(
            return_value=httpx.Response(201, json={"id": "1501", "properties": {"jobtitle": "CEO"}})
        )
        res = self._adapter().write(None, "t", "contact", None, {"jobtitle": "CEO"})
        assert res.ok and res.external_id == "1501"
        sent = json.loads(route.calls[0].request.content)
        assert sent == {"properties": {"jobtitle": "CEO"}}

    @respx.mock
    def test_update_by_external_id(self):
        respx.patch(f"{self.BASE}/crm/v3/objects/contacts/1501").mock(
            return_value=httpx.Response(200, json={"id": "1501"})
        )
        res = self._adapter().write(None, "t", "contact", "1501", {"jobtitle": "CTO"})
        assert res.ok

    @respx.mock
    def test_rate_limit_is_retryable(self):
        respx.post(f"{self.BASE}/crm/v3/objects/companies").mock(
            return_value=httpx.Response(429, json={"message": "rate limited"})
        )
        res = self._adapter().write(None, "t", "company", None, {"domain": "x.test"})
        assert not res.ok and res.retryable and res.status_code == 429

    @respx.mock
    def test_server_error_retryable_client_error_permanent(self):
        respx.post(f"{self.BASE}/crm/v3/objects/companies").mock(
            return_value=httpx.Response(502, json={})
        )
        assert self._adapter().write(None, "t", "company", None, {"domain": "x.test"}).retryable
        respx.post(f"{self.BASE}/crm/v3/objects/companies").mock(
            return_value=httpx.Response(400, json={"message": "bad property"})
        )
        res = self._adapter().write(None, "t", "company", None, {"domain": "x.test"})
        assert not res.ok and not res.retryable

    @respx.mock
    def test_search_by_natural_key(self):
        respx.post(f"{self.BASE}/crm/v3/objects/contacts/search").mock(
            return_value=httpx.Response(200, json={
                "results": [{"id": "77", "properties": {"email": "a@b.test", "jobtitle": "VP"}}]
            })
        )
        res = self._adapter().read(None, "t", "contact", None, {"email": "a@b.test"})
        assert res.found and res.external_id == "77"
        assert res.properties["jobtitle"] == "VP"
