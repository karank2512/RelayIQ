"""The 12 required end-to-end scenarios, each exercised through the public API against
real Postgres + Redis with deterministic provider simulators."""

import uuid

from sqlalchemy import select

from relayiq.models import CrmSimRecord


def _campaign_with_budget(client, env, *, limit=500.0, min_confidence=0.6,
                          countries=None) -> str:
    filters = {"allowed_countries": countries} if countries else {}
    r = client.post("/v1/admin/campaigns",
                    json={"name": f"e2e-{uuid.uuid4().hex[:6]}", "filters": filters,
                          "required_fields": ["job_title"], "min_confidence": min_confidence},
                    headers=env.headers("operator"))
    assert r.status_code == 201, r.text
    campaign_id = r.json()["id"]
    r = client.post("/v1/admin/budgets",
                    json={"name": "e2e budget", "campaign_id": campaign_id,
                          "limit_credits": limit},
                    headers=env.headers("admin"))
    assert r.status_code == 201, r.text
    return campaign_id


def test_e2e_01_clean_record_enriches_and_syncs(client, env, helpers, contact_pool):
    job, contact = helpers.enrich_until(
        client, env, contact_pool, ["job_title", "seniority"],
        lambda j: j["status"] == "completed" and j["result_summary"].get("crm_sync") == "success",
    )
    assert job["result_summary"]["accepted"] is True
    # The CRM simulator holds the synced record
    session = helpers.session()
    try:
        rows = session.execute(
            select(CrmSimRecord).where(CrmSimRecord.tenant_id == env.tenant_id)
        ).scalars().all()
        emails = {(r.properties or {}).get("email") for r in rows}
        assert contact["truth"]["work_email"] in emails
    finally:
        session.close()


def test_e2e_02_cached_record_avoids_provider_calls(client, env, helpers, contact_pool):
    job, contact = helpers.enrich_until(
        client, env, contact_pool, ["job_title"],
        lambda j: j["actual_cost_credits"] > 0,
    )
    before = helpers.provider_request_count(env.tenant_id, job["entity_id"])
    r2 = helpers.enrich(client, env, helpers.contact_payload(contact), ["job_title"])
    assert r2.json()["pre_decision"] == "use_cache"
    assert r2.json()["actual_cost_credits"] == 0.0
    assert helpers.provider_request_count(env.tenant_id, job["entity_id"]) == before


def test_e2e_03_filtered_record_spends_nothing(client, env, helpers, contact_pool):
    campaign_id = _campaign_with_budget(client, env, countries=["United States"])
    contact = next(contact_pool)
    r = helpers.enrich(client, env, helpers.contact_payload(contact, country="France"),
                       ["job_title"], campaign_id=campaign_id)
    job = r.json()
    assert job["status"] == "skipped" and job["pre_decision"] == "skip"
    assert job["actual_cost_credits"] == 0.0
    assert all(e["actual"] == 0 for e in helpers.ledger_entries(job["id"]))


def test_e2e_04_and_05_conflict_enters_review_and_reviewer_accepts(client, env, helpers, world):
    task, job, _ = helpers.ensure_review_task(client, env, world)
    assert task["field_name"] == "job_title"
    assert task["suggested_value"]
    detail = client.get(f"/v1/review/tasks/{task['id']}", headers=env.headers("reviewer")).json()
    providers = {o["provider"] for o in detail["observations"]}
    assert {"alpha", "beta"} <= providers  # genuinely conflicting cross-provider values
    assert detail["lineage"]["reconciliations"][-1]["outcome"] == "require_review"

    r = client.post(f"/v1/review/tasks/{task['id']}/actions",
                    json={"action": "accept_suggested", "note": "e2e accept"},
                    headers=env.headers("reviewer"))
    assert r.status_code == 200, r.text
    entity = client.get(f"/v1/entities/contact/{task['entity_id']}",
                        headers=env.headers("reviewer")).json()
    canon = {f["field_name"]: f for f in entity["canonical_fields"]}
    assert canon["job_title"]["source_kind"] == "review"
    assert canon["job_title"]["value"] == task["suggested_value"]


def test_e2e_06_accepted_value_syncs_to_crm_simulator(client, env, helpers, world):
    task, job, contact = helpers.ensure_review_task(client, env, world)
    client.post(f"/v1/review/tasks/{task['id']}/actions",
                json={"action": "accept_suggested"}, headers=env.headers("reviewer"))
    r = client.post("/v1/crm/sync",
                    json={"entity_type": "contact", "entity_id": task["entity_id"]},
                    headers=env.headers("operator"))
    assert r.status_code == 201, r.text
    attempt = r.json()
    change = attempt["field_changes"].get("job_title")
    assert change is not None
    assert change["gate"] in ("write", "no_write")  # no_write when CRM already equivalent
    sim = client.get("/v1/crm/simulator/records", params={"limit": 200},
                     headers=env.headers("operator")).json()
    match = [x for x in sim["items"]
             if (x["properties"] or {}).get("email") == contact["truth"]["work_email"]]
    assert match, "synced record must exist in the CRM simulator"


def test_e2e_07_reversal_preserves_audit_history(client, env, helpers, world):
    task, _, _ = helpers.ensure_review_task(client, env, world)
    entity_before = client.get(f"/v1/entities/contact/{task['entity_id']}",
                               headers=env.headers("reviewer")).json()
    canon_before = {f["field_name"]: f["value"] for f in entity_before["canonical_fields"]}

    client.post(f"/v1/review/tasks/{task['id']}/actions",
                json={"action": "accept_suggested"}, headers=env.headers("reviewer"))
    r = client.post(f"/v1/review/tasks/{task['id']}/reverse",
                    headers=env.headers("reviewer"))
    assert r.status_code == 200, r.text

    detail = client.get(f"/v1/review/tasks/{task['id']}", headers=env.headers("reviewer")).json()
    actions = [d["action"] for d in detail["decisions"]]
    assert "accept_suggested" in actions and "reverse" in actions  # nothing deleted
    assert detail["task"]["status"] == "reversed"
    entity_after = client.get(f"/v1/entities/contact/{task['entity_id']}",
                              headers=env.headers("reviewer")).json()
    canon_after = {f["field_name"]: f["value"] for f in entity_after["canonical_fields"]}
    assert canon_after.get("job_title") == canon_before.get("job_title")
    audit = client.get("/v1/audit", params={"object_id": task["id"]},
                       headers=env.headers("operator")).json()
    assert any(a["action"] == "review.reverse" for a in audit["items"])


def test_e2e_08_duplicate_webhook_no_double_spend(client, env, helpers, contact_pool, sync_worker):
    contact = next(contact_pool)
    body = helpers.webhook_payload(env, contact, ["job_title"])
    delivery = f"dup-{uuid.uuid4().hex[:8]}"
    r1 = client.post("/v1/webhooks/enrichment", content=body,
                     headers=helpers.webhook_headers(body, delivery))
    assert r1.status_code == 200 and not r1.json()["duplicate"]
    job_id = r1.json()["job_id"]
    spend_1 = sum(e["actual"] for e in helpers.ledger_entries(job_id))

    r2 = client.post("/v1/webhooks/enrichment", content=body,
                     headers=helpers.webhook_headers(body, delivery))
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert r2.json()["job_id"] == job_id  # same job, no second one
    assert sum(e["actual"] for e in helpers.ledger_entries(job_id)) == spend_1


def test_e2e_09_stale_field_is_refreshed(client, env, helpers, contact_pool):
    job, contact = helpers.enrich_until(
        client, env, contact_pool, ["job_title"],
        lambda j: j["actual_cost_credits"] > 0 and j["result_summary"]["fields_filled"] > 0,
    )
    payload = helpers.contact_payload(contact)
    # Age the canonical value past every staleness threshold and drop the Redis entry
    assert helpers.expire_canonical(env.tenant_id, "contact", job["entity_id"], "job_title") > 0
    helpers.invalidate_cache(env.tenant_id, "contact", payload["work_email"])

    r2 = helpers.enrich(client, env, payload, ["job_title"])
    j2 = r2.json()
    assert j2["pre_decision"] == "enrich"  # stale → not served from store
    assert helpers.provider_request_count(env.tenant_id, job["entity_id"]) > 1


def test_e2e_10_budget_blocks_expensive_route(client, env, helpers, contact_pool):
    campaign_id = _campaign_with_budget(client, env, limit=0.5)  # < any provider call
    contact = next(contact_pool)
    r = helpers.enrich(client, env, helpers.contact_payload(contact),
                       ["job_title", "seniority", "department"], campaign_id=campaign_id)
    job = r.json()
    assert job["status"] == "blocked_budget"
    assert job["actual_cost_credits"] == 0.0
    assert sum(e["actual"] for e in helpers.ledger_entries(job["id"])) == 0


def test_e2e_11_provider_outage_falls_back(client, env, helpers, contact_pool):
    """Force an alpha outage; a company-field enrichment (alpha primary) must still fill
    via beta or record the outage without spending on alpha."""
    r = client.patch("/v1/admin/providers/alpha", json={"config": {"outage": True}},
                     headers=env.headers("admin"))
    assert r.status_code == 200, r.text
    try:
        # Fresh account so the request must hit providers
        job = None
        for _ in range(10):
            contact = next(contact_pool)
            domain = contact["truth"]["work_email"].split("@")[1]
            resp = client.post(
                "/v1/enrichment/execute",
                json={"entity_type": "account",
                      "entity": {"name": domain.split(".")[0], "root_domain": domain},
                      "requested_fields": ["industry", "employee_count"], "mode": "sync"},
                headers=env.headers("operator"),
            )
            assert resp.status_code == 201
            job = resp.json()
            if job["result_summary"].get("fields_filled", 0) > 0:
                break
        assert job is not None
        assert "alpha" not in (job["result_summary"].get("providers_used") or []), \
            "outaged provider must not serve fields"
        entries = helpers.ledger_entries(job["id"])
        assert sum(e["actual"] for e in entries if e["provider_key"] == "alpha") == 0
        if job["result_summary"].get("fields_filled", 0) > 0:
            assert "beta" in job["result_summary"]["providers_used"]  # safe fallback
    finally:
        r = client.patch("/v1/admin/providers/alpha", json={"config": {}},
                         headers=env.headers("admin"))
        assert r.status_code == 200


def test_e2e_12_low_confidence_does_not_overwrite_crm(client, env, helpers, contact_pool):
    """Pre-populate the CRM with a fresh value; a modest-confidence enrichment must not
    replace it (gate: preserve_crm / secondary_property — never a silent overwrite)."""
    from datetime import UTC, datetime

    # Need a contact whose job_title specifically was accepted AND synced.
    job = contact = None
    for _ in range(30):
        candidate = next(contact_pool)
        r = helpers.enrich(client, env, helpers.contact_payload(candidate),
                           ["job_title", "seniority"])
        assert r.status_code == 201
        j = r.json()
        if j["status"] != "completed" or j["result_summary"].get("crm_sync") != "success":
            continue
        entity = client.get(f"/v1/entities/contact/{j['entity_id']}",
                            headers=env.headers("operator")).json()
        if any(f["field_name"] == "job_title" and f["value"] for f in entity["canonical_fields"]):
            job, contact = j, candidate
            break
    assert job is not None, "no synced contact with a canonical job_title found in 30 draws"
    email = contact["truth"]["work_email"]
    session = helpers.session()
    try:
        sim = session.execute(
            select(CrmSimRecord).where(CrmSimRecord.tenant_id == env.tenant_id)
        ).scalars().all()
        row = next((x for x in sim if (x.properties or {}).get("email") == email), None)
        assert row is not None
        guarded = "Handwritten Executive Title"
        row.properties = {**row.properties, "job_title": guarded}
        row.property_updated_at = {**(row.property_updated_at or {}),
                                   "job_title": datetime.now(UTC).isoformat()}
        session.commit()
        external_id = row.external_id
    finally:
        session.close()

    r = client.post("/v1/crm/sync",
                    json={"entity_type": "contact", "entity_id": job["entity_id"],
                          "fields": ["job_title"]},
                    headers=env.headers("operator"))
    assert r.status_code == 201, r.text
    gate = r.json()["field_changes"]["job_title"]["gate"]
    # Every one of these is a protective non-overwrite outcome; which fires depends on
    # the candidate's confidence and source staleness (deterministic per seed).
    assert gate in ("preserve_crm", "secondary_property", "require_approval",
                    "no_write", "mark_refresh")
    assert gate != "write"

    session = helpers.session()
    try:
        row = session.execute(
            select(CrmSimRecord).where(
                CrmSimRecord.tenant_id == env.tenant_id,
                CrmSimRecord.external_id == external_id,
            )
        ).scalar_one()
        assert row.properties["job_title"] == "Handwritten Executive Title", \
            "fresh CRM value must never be silently overwritten"
    finally:
        session.close()
