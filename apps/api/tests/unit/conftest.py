"""Shared unit-test fixtures: dedicated in-memory SQLite engine (independent of env/config)
and fakeredis. Services under test all accept an explicit Session, so no global state."""

import fakeredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from relayiq.models import Base


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = maker()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def tenant(session):
    from relayiq.models import Tenant

    t = Tenant(name="Unit Test Tenant", slug="unit-test", settings={})
    session.add(t)
    session.commit()
    return t
