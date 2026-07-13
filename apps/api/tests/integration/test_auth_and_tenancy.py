"""Integration: authentication, role enforcement, and cross-tenant isolation
against real Postgres."""


class TestAuth:
    def test_login_bad_password_uniform_401(self, client, env):
        r = client.post("/v1/auth/login", json={
            "email": f"operator@{env.slug}.relayiq.test", "password": "wrong-password-123",
        })
        assert r.status_code == 401
        r2 = client.post("/v1/auth/login", json={
            "email": f"ghost@{env.slug}.relayiq.test", "password": "wrong-password-123",
        })
        assert r2.status_code == 401
        assert r.json()["detail"] == r2.json()["detail"]  # no account enumeration

    def test_missing_token_401(self, client):
        assert client.get("/v1/contacts").status_code == 401

    def test_garbage_token_401(self, client):
        r = client.get("/v1/contacts", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_me_reflects_db_role(self, client, env):
        r = client.get("/v1/auth/me", headers=env.headers("reviewer"))
        assert r.status_code == 200
        assert r.json()["role"] == "reviewer"


class TestRoleMatrix:
    def test_analyst_cannot_execute_enrichment(self, client, env, helpers):
        r = client.post(
            "/v1/enrichment/execute",
            json={"entity_type": "contact", "entity": {"work_email": "x@y.test"},
                  "requested_fields": ["job_title"]},
            headers=env.headers("analyst"),
        )
        assert r.status_code == 403

    def test_analyst_cannot_patch_provider(self, client, env):
        r = client.patch("/v1/admin/providers/alpha", json={"enabled": True},
                         headers=env.headers("analyst"))
        assert r.status_code == 403

    def test_operator_cannot_patch_provider(self, client, env):
        r = client.patch("/v1/admin/providers/alpha", json={"enabled": True},
                         headers=env.headers("operator"))
        assert r.status_code == 403  # provider settings are admin-only

    def test_reviewer_cannot_create_budget(self, client, env):
        r = client.post("/v1/admin/budgets",
                        json={"name": "b", "limit_credits": 10},
                        headers=env.headers("reviewer"))
        assert r.status_code == 403

    def test_analyst_cannot_read_audit_log(self, client, env):
        assert client.get("/v1/audit", headers=env.headers("analyst")).status_code == 403
        assert client.get("/v1/audit", headers=env.headers("operator")).status_code == 200

    def test_analyst_can_read_metrics(self, client, env):
        assert client.get("/v1/metrics/overview", headers=env.headers("analyst")).status_code == 200


class TestCrossTenantIsolation:
    def test_tenant_b_cannot_see_tenant_a_data(self, client, env, make_env, helpers, contact_pool):
        contact = next(contact_pool)
        r = helpers.enrich(client, env, helpers.contact_payload(contact), ["job_title"])
        assert r.status_code == 201
        job = r.json()

        env_b = make_env("isolation")
        # Job invisible across tenants
        assert client.get(f"/v1/enrichment/jobs/{job['id']}",
                          headers=env_b.headers("operator")).status_code == 404
        # Entity invisible
        assert client.get(f"/v1/entities/contact/{job['entity_id']}",
                          headers=env_b.headers("operator")).status_code == 404
        # Entity list scoped: tenant B sees none of tenant A's contacts
        listing = client.get("/v1/contacts", params={"q": contact["truth"]["work_email"]},
                             headers=env_b.headers("operator")).json()
        assert listing["total"] == 0

    def test_review_tasks_scoped(self, client, env, make_env):
        env_b = make_env("isolation2")
        mine = client.get("/v1/review/queue", params={"status": "all"},
                          headers=env.headers("reviewer")).json()
        theirs = client.get("/v1/review/queue", params={"status": "all"},
                            headers=env_b.headers("reviewer")).json()
        my_ids = {t["id"] for t in mine["items"]}
        their_ids = {t["id"] for t in theirs["items"]}
        assert my_ids.isdisjoint(their_ids)
        assert theirs["total"] == 0
