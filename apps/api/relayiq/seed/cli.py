"""Database seeder: demo tenant, users (one per role), providers, campaign + budget,
CRM simulator connection, suppressions, and canonical entities from the synthetic world.

Idempotent: `--if-empty` seeds only when no tenant exists (used by docker-compose);
`--reset` wipes and re-seeds. Passwords are read from env (defaults are dev-only and
printed once — never committed as real secrets).
"""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import delete, select

from relayiq.canonical.normalize import extract_root_domain, normalize_company_name
from relayiq.config import get_settings
from relayiq.db import get_sessionmaker
from relayiq.enums import Role
from relayiq.models import (
    Account,
    Base,
    Budget,
    Campaign,
    Contact,
    CrmConnection,
    ProviderConfig,
    Suppression,
    Tenant,
    User,
)
from relayiq.security import hash_password
from relayiq.seed.worldgen import generate_world, load_world, write_world

DEMO_USERS = [
    ("admin@demo.relayiq.test", Role.ADMIN, "RELAYIQ_SEED_ADMIN_PASSWORD"),
    ("operator@demo.relayiq.test", Role.OPERATOR, "RELAYIQ_SEED_OPERATOR_PASSWORD"),
    ("reviewer@demo.relayiq.test", Role.REVIEWER, "RELAYIQ_SEED_REVIEWER_PASSWORD"),
    ("analyst@demo.relayiq.test", Role.ANALYST, "RELAYIQ_SEED_ANALYST_PASSWORD"),
]
DEFAULT_DEV_PASSWORD = "relayiq-demo-password"  # noqa: S105 — documented dev-only default


def _looks_like_production_db(database_url: str) -> bool:
    """A DB is 'clearly local' only when its host is localhost/127.* or the compose service
    name 'postgres'. Anything else is treated as potentially production — so the guard fires
    even when RELAYIQ_ENV is left unset while pointed at a remote database (L9)."""
    from urllib.parse import urlparse

    try:
        host = (urlparse(database_url.replace("+psycopg", "")).hostname or "").lower()
    except ValueError:
        return True
    return host not in ("localhost", "127.0.0.1", "::1", "postgres", "")


def _production_seed_guard(settings) -> None:
    """Demo data carries documented default passwords — never let it near production
    unless explicitly forced AND every seed password is supplied via the environment.
    Fires on RELAYIQ_ENV=production OR a non-local DATABASE_URL (defense in depth)."""
    guarded = settings.is_production or _looks_like_production_db(settings.database_url)
    if not guarded:
        return
    if os.environ.get("RELAYIQ_SEED_ALLOW_PRODUCTION") != "1":
        print(  # noqa: T201
            "seed: refusing to seed demo data — RELAYIQ_ENV=production or DATABASE_URL points "
            "at a non-local host. This creates demo users with documented default passwords. "
            "If you really want this (staging smoke test), set RELAYIQ_SEED_ALLOW_PRODUCTION=1 "
            "and provide RELAYIQ_SEED_*_PASSWORD for every role."
        )
        sys.exit(2)
    missing = [env for _, _, env in DEMO_USERS if not os.environ.get(env)]
    if missing:
        print(f"seed: forced production seeding requires explicit passwords; missing: {missing}")  # noqa: T201, E501
        sys.exit(2)


def seed(reset: bool = False, if_empty: bool = False, world_path: str | None = None) -> None:
    settings = get_settings()
    _production_seed_guard(settings)
    session = get_sessionmaker()()
    try:
        existing = session.execute(select(Tenant)).scalars().first()
        if existing and if_empty:
            print("seed: tenant exists, skipping (--if-empty)")  # noqa: T201
            return
        if existing and reset:
            print("seed: resetting all data")  # noqa: T201
            for table in reversed(Base.metadata.sorted_tables):
                session.execute(delete(table))
            session.commit()

        # World file
        wp = world_path or settings.synthetic_world_path
        if not Path(wp).exists():
            print(f"seed: generating synthetic world at {wp}")  # noqa: T201
            write_world(generate_world(seed=settings.provider_sim_seed, n_companies=120), wp)
        world = load_world(wp)

        tenant = Tenant(name="Demo RevOps Team", slug="demo", settings={})
        session.add(tenant)
        session.flush()

        for email, role, env_var in DEMO_USERS:
            password = os.environ.get(env_var, DEFAULT_DEV_PASSWORD)
            session.add(User(
                tenant_id=tenant.id, email=email, password_hash=hash_password(password),
                role=role.value, full_name=email.split("@")[0].title(),
            ))

        session.add_all([
            ProviderConfig(
                key="alpha", display_name="Provider Alpha (simulated)",
                adapter="simulator.alpha", reliability_prior=0.86,
                timeout_ms=5000, max_retries=2,
            ),
            ProviderConfig(
                key="beta", display_name="Provider Beta (simulated)",
                adapter="simulator.beta", reliability_prior=0.9,
                timeout_ms=8000, max_retries=2,
            ),
        ])

        campaign = Campaign(
            tenant_id=tenant.id, name="Q3 Outbound — NA/EU",
            filters={
                "allowed_countries": ["United States", "Canada", "United Kingdom", "Germany"],
                "min_employee_count": 5,
            },
            required_fields=["job_title", "seniority"],
            min_confidence=0.6,
        )
        session.add(campaign)
        session.flush()
        session.add(Budget(
            tenant_id=tenant.id, campaign_id=campaign.id, name="Q3 lifetime budget",
            kind="hard", period="lifetime", limit_credits=2000, warning_threshold=0.8,
            degradation_mode="cheapest",
        ))
        session.add(CrmConnection(
            tenant_id=tenant.id, system="simulator", display_name="CRM Simulator",
            mode="simulator",
        ))

        # Suppressions from world tags
        for company in world["companies"]:
            if "suppressed" in company["tags"]:
                domain = extract_root_domain(company["truth"].get("root_domain"))
                if domain:
                    session.add(Suppression(
                        tenant_id=tenant.id, kind="domain", value=domain,
                        reason="synthetic suppression list",
                    ))
        for contact in world["contacts"]:
            if "suppressed" in contact["tags"] and contact["truth"].get("work_email"):
                session.add(Suppression(
                    tenant_id=tenant.id, kind="email",
                    value=contact["truth"]["work_email"].lower(),
                    reason="synthetic suppression list",
                ))

        # Canonical entities (identifiers only — enrichment fills the rest)
        accounts_by_world: dict[str, str] = {}
        for company in world["companies"]:
            t = company["truth"]
            account = Account(
                tenant_id=tenant.id,
                name=t["name"],
                normalized_name=normalize_company_name(t["name"]),
                website=t.get("website"),
                root_domain=extract_root_domain(t.get("root_domain")),
            )
            session.add(account)
            session.flush()
            accounts_by_world[company["world_id"]] = account.id
        for contact in world["contacts"]:
            t = contact["truth"]
            session.add(Contact(
                tenant_id=tenant.id,
                first_name=t["first_name"], last_name=t["last_name"], full_name=t["full_name"],
                work_email=t.get("work_email"),
                account_id=accounts_by_world.get(contact["company_world_id"]),
                company_domain=extract_root_domain(
                    t.get("work_email", "").split("@")[-1] if t.get("work_email") else None
                ),
                country=t.get("country"),
            ))
        session.commit()
        n_acc = len(world["companies"])
        n_con = len(world["contacts"])
        print(  # noqa: T201
            f"seed: tenant 'demo' with {len(DEMO_USERS)} users, 2 providers, 1 campaign, "
            f"{n_acc} accounts, {n_con} contacts.\n"
            f"seed: demo logins use password from env or the documented dev default "
            f"(see docs/deployment.md)."
        )
    finally:
        session.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the RelayIQ database")
    ap.add_argument("--reset", action="store_true", help="wipe all data first")
    ap.add_argument("--if-empty", action="store_true", help="only seed when DB has no tenant")
    ap.add_argument("--world", default=None, help="path to synthetic world JSON")
    args = ap.parse_args()
    if args.reset and args.if_empty:
        print("choose one of --reset / --if-empty")  # noqa: T201
        sys.exit(2)
    seed(reset=args.reset, if_empty=args.if_empty, world_path=args.world)


if __name__ == "__main__":
    main()
