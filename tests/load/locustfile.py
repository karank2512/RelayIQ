"""Locust load test for the RelayIQ API.

Run (API must be up with seeded data):
    cd apps/api && .venv/bin/locust -f ../../tests/load/locustfile.py --headless \
        -u 25 -r 5 -t 60s --host http://localhost:8000 --csv ../../docs/benchmarks/load

Mix: mostly read traffic (jobs, metrics, entities) + a stream of enrichment executes with
rotating synthetic identities (cache hits and misses both exercised) + idempotent replays.
Results reflect THIS machine only — never quote them as production capacity.
"""

import itertools
import random

from locust import HttpUser, between, task

PASSWORD = "relayiq-demo-password"  # dev-only seeded credential
_counter = itertools.count()


class RelayIQUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        resp = self.client.post(
            "/v1/auth/login",
            json={"email": "operator@demo.relayiq.test", "password": PASSWORD},
        )
        resp.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        self.rng = random.Random(next(_counter))

    @task(4)
    def overview(self) -> None:
        self.client.get("/v1/metrics/overview", name="/v1/metrics/overview")

    @task(3)
    def list_jobs(self) -> None:
        self.client.get("/v1/enrichment/jobs?limit=25", name="/v1/enrichment/jobs")

    @task(2)
    def list_contacts(self) -> None:
        self.client.get("/v1/contacts?limit=25", name="/v1/contacts")

    @task(2)
    def review_queue(self) -> None:
        self.client.get("/v1/review/queue?status=pending", name="/v1/review/queue")

    @task(3)
    def enrich_new(self) -> None:
        """Fresh synthetic identity → full pipeline (provider sim calls, no cache)."""
        i = next(_counter)
        body = {
            "entity_type": "contact",
            "entity": {
                "work_email": f"load.user{i}@loadtest{i % 50}.test",
                "full_name": f"Load User{i}",
                "company_domain": f"loadtest{i % 50}.test",
            },
            "requested_fields": ["job_title", "seniority", "department"],
            "mode": "sync",
        }
        self.client.post("/v1/enrichment/execute", json=body, name="/v1/enrichment/execute [new]")

    @task(2)
    def enrich_repeat(self) -> None:
        """Repeats a small identity pool → exercises canonical-store/cache path."""
        k = self.rng.randint(0, 9)
        body = {
            "entity_type": "contact",
            "entity": {
                "work_email": f"repeat.user{k}@repeatpool.test",
                "full_name": f"Repeat User{k}",
                "company_domain": "repeatpool.test",
            },
            "requested_fields": ["job_title", "seniority"],
            "mode": "sync",
        }
        self.client.post("/v1/enrichment/execute", json=body,
                         name="/v1/enrichment/execute [repeat]")

    @task(1)
    def idempotent_replay(self) -> None:
        body = {
            "entity_type": "contact",
            "entity": {"work_email": "idem.user@repeatpool.test", "full_name": "Idem User",
                       "company_domain": "repeatpool.test"},
            "requested_fields": ["job_title"],
            "idempotency_key": "load-idem-constant",
            "mode": "sync",
        }
        self.client.post("/v1/enrichment/execute", json=body,
                         name="/v1/enrichment/execute [idempotent replay]")
