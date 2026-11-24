"""Integration: a snapshot replay provider enabled through ProviderConfig answers real enrichment jobs end to
end (API -> routing -> provider -> observations), only when the account's own domain matches the registrant
website host. Synthetic fixtures only (*.test domains).

The provider row is global, so this test uses its own tenant and a tenant-scoped routing policy, and it
disables its provider row afterwards so other tests' routing is unaffected.
"""

import json
import uuid

from sqlalchemy import select

from relayiq.db import get_sessionmaker
from relayiq.models import FieldObservation, ProviderConfig, ProviderRequest
from relayiq.providers.registry import get_registry
from relayiq.providers.snapshot_adapter import SNAPSHOT_ROOT_ENV


def _rows(model, **where):
    session = get_sessionmaker()()
    try:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return session.execute(stmt).scalars().all()
    finally:
        session.close()


def test_snapshot_provider_end_to_end(client, make_env, tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    key = f"replay_sec_{tag}"
    name, domain = f"Acme Replay {tag}", f"acme-replay-{tag}.test"
    other, other_domain = f"Globex Replay {tag}", f"globex-replay-{tag}.test"
    snapshot = {
        "sec": {
            name: {
                "chosen": {
                    "cik": "0000000001",
                    "name": name.upper(),
                    "hq_city": "Somewhere",
                    "hq_state": "CA",
                    "website": f"https://www.{domain}/",
                    "state_of_incorporation": "DE",
                    "latest_filing_date": "2026-01-01",
                }
            },
            other: {
                "chosen": {
                    "cik": "0000000002",
                    "name": other.upper(),
                    "hq_city": "Elsewhere",
                    "hq_state": "NY",
                    "website": f"https://holdings-{other_domain}/",
                    "state_of_incorporation": "DE",
                    "latest_filing_date": "2026-01-01",
                }
            },
        }
    }
    (tmp_path / "replay.json").write_text(json.dumps(snapshot))
    monkeypatch.setenv(SNAPSHOT_ROOT_ENV, str(tmp_path))

    env = make_env("replay")
    session = get_sessionmaker()()
    row = ProviderConfig(
        key=key,
        display_name="Snapshot replay (SEC, test)",
        adapter="snapshot.sec",
        reliability_prior=0.9,
        timeout_ms=8000,
        max_retries=0,
        enabled=True,
        config={"snapshot_file": "replay.json", "as_of": "2026-11-20"},
    )
    session.add(row)
    session.commit()
    try:
        policy = {
            "defaults": {"strategy": "cheapest_capable", "fallback": True, "max_candidates": 3},
            "fields": {"account.hq_city": {"providers": [key], "strategy": "cheapest_capable"}},
        }
        r = client.post(
            "/v1/admin/routing-policies",
            json={"name": f"replay-{tag}", "document": policy},
            headers=env.headers("operator"),
        )
        assert r.status_code == 201, r.text

        def run(company, dom):
            resp = client.post(
                "/v1/enrichment/execute",
                json={
                    "entity_type": "account",
                    "entity": {"name": company, "root_domain": dom},
                    "requested_fields": ["hq_city"],
                    "mode": "sync",
                },
                headers=env.headers("operator"),
            )
            assert resp.status_code == 201, resp.text
            return resp.json()

        bound = run(name, domain)
        reqs = _rows(ProviderRequest, entity_id=bound["entity_id"], provider_key=key)
        assert reqs and reqs[-1].outcome == "success"
        obs = _rows(FieldObservation, entity_id=bound["entity_id"], provider_key=key, field_name="hq_city")
        assert [o.raw_value for o in obs] == ["Somewhere"]
        assert bound["actual_cost_credits"] == 0.0

        # registrant website host differs from the account's domain: the provider abstains, nothing is stored
        unbound = run(other, other_domain)
        reqs = _rows(ProviderRequest, entity_id=unbound["entity_id"], provider_key=key)
        assert reqs and all(q.outcome == "perm_fail" for q in reqs)
        assert _rows(FieldObservation, entity_id=unbound["entity_id"], provider_key=key) == []
    finally:
        row = session.get(ProviderConfig, row.id)
        row.enabled = False
        session.commit()
        get_registry(session, refresh=True)
        session.close()
