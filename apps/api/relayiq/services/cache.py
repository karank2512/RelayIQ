"""Redis field cache. PostgreSQL stays the source of truth (ADR-002, ADR-003).

Key layout (tenant- and schema-version-aware):
    riq:{schema_version}:{tenant_id}:f:{entity_type}:{entity_key}:{field}      — positive entry
    riq:{schema_version}:{tenant_id}:neg:{entity_type}:{entity_key}:{field}    — negative entry
    riq:{schema_version}:lock:{...}                                            — stampede lock

Positive entries carry a soft TTL for stale-while-revalidate: past soft TTL the entry is
served as STALE_HIT and the caller may refresh in the background; the hard (Redis) TTL
bounds staleness absolutely.
"""

import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

import redis

from relayiq.config import get_settings
from relayiq.enums import CacheStatus
from relayiq.observability.metrics import CACHE_OPS

_client: redis.Redis | None = None
_client_lock = threading.Lock()


def get_redis() -> redis.Redis:
    global _client
    with _client_lock:
        if _client is None:
            _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        return _client


def set_redis(client: redis.Redis) -> None:
    """Test hook (fakeredis) / worker override."""
    global _client
    with _client_lock:
        _client = client


@dataclass
class CacheEntry:
    status: CacheStatus
    value: Any = None
    normalized_value: Any = None
    provider_key: str | None = None
    confidence: float | None = None
    observation_id: str | None = None
    verified_at_epoch: float | None = None
    avoided_cost_credits: float = 0.0


class FieldCache:
    def __init__(self, client: redis.Redis | None = None):
        self.settings = get_settings()
        self.r = client or get_redis()
        self.prefix = f"riq:{self.settings.cache_schema_version}"

    # -- keys ---------------------------------------------------------------

    def _k(self, tenant_id: str, entity_type: str, entity_key: str, field: str) -> str:
        return f"{self.prefix}:{tenant_id}:f:{entity_type}:{entity_key.lower()}:{field}"

    def _nk(self, tenant_id: str, entity_type: str, entity_key: str, field: str) -> str:
        return f"{self.prefix}:{tenant_id}:neg:{entity_type}:{entity_key.lower()}:{field}"

    # -- read ----------------------------------------------------------------

    def get_field(self, tenant_id: str, entity_type: str, entity_key: str, field: str) -> CacheEntry:
        if not entity_key:
            return CacheEntry(status=CacheStatus.MISS)
        raw = cast("str | None", self.r.get(self._k(tenant_id, entity_type, entity_key, field)))
        if raw is not None:
            try:
                doc = json.loads(raw)
            except (ValueError, TypeError):
                self._count(entity_type, field, "corrupt_miss")
                return CacheEntry(status=CacheStatus.MISS)
            soft_expiry = doc.get("soft_expiry_epoch", 0)
            status = CacheStatus.HIT if time.time() <= soft_expiry else CacheStatus.STALE_HIT
            self._count(entity_type, field, status.value)
            return CacheEntry(
                status=status,
                value=doc.get("value"),
                normalized_value=doc.get("normalized_value"),
                provider_key=doc.get("provider_key"),
                confidence=doc.get("confidence"),
                observation_id=doc.get("observation_id"),
                verified_at_epoch=doc.get("verified_at_epoch"),
                avoided_cost_credits=float(doc.get("cost_credits") or 0.0),
            )
        if self.r.exists(self._nk(tenant_id, entity_type, entity_key, field)):
            self._count(entity_type, field, CacheStatus.NEGATIVE_HIT.value)
            return CacheEntry(status=CacheStatus.NEGATIVE_HIT)
        self._count(entity_type, field, CacheStatus.MISS.value)
        return CacheEntry(status=CacheStatus.MISS)

    # -- write ----------------------------------------------------------------

    def set_field(
        self,
        tenant_id: str,
        entity_type: str,
        entity_key: str,
        field: str,
        *,
        value: Any,
        normalized_value: Any,
        provider_key: str | None,
        confidence: float | None,
        observation_id: str | None,
        cost_credits: float = 0.0,
        ttl_seconds: int | None = None,
        soft_ttl_seconds: int | None = None,
        verified_at_epoch: float | None = None,
    ) -> None:
        if not entity_key:
            return
        ttl = ttl_seconds or self.settings.cache_default_ttl_seconds
        soft = soft_ttl_seconds or int(ttl * 0.5)
        doc = {
            "value": value,
            "normalized_value": normalized_value,
            "provider_key": provider_key,
            "confidence": confidence,
            "observation_id": observation_id,
            "cost_credits": cost_credits,
            "verified_at_epoch": verified_at_epoch or time.time(),
            "soft_expiry_epoch": time.time() + soft,
        }
        pipe = self.r.pipeline()
        pipe.set(self._k(tenant_id, entity_type, entity_key, field), json.dumps(doc), ex=ttl)
        pipe.delete(self._nk(tenant_id, entity_type, entity_key, field))
        pipe.execute()

    def set_negative(
        self, tenant_id: str, entity_type: str, entity_key: str, field: str, ttl_seconds: int | None = None
    ) -> None:
        if not entity_key:
            return
        ttl = ttl_seconds or self.settings.cache_negative_ttl_seconds
        self.r.set(self._nk(tenant_id, entity_type, entity_key, field), "1", ex=ttl)

    def invalidate_entity(self, tenant_id: str, entity_type: str, entity_key: str) -> int:
        """Delete all cached fields for one entity (SCAN, not KEYS — non-blocking)."""
        removed = 0
        for pattern in (
            f"{self.prefix}:{tenant_id}:f:{entity_type}:{entity_key.lower()}:*",
            f"{self.prefix}:{tenant_id}:neg:{entity_type}:{entity_key.lower()}:*",
        ):
            for key in self.r.scan_iter(match=pattern, count=200):
                removed += cast(int, self.r.delete(key))
        return removed

    def invalidate_field(self, tenant_id: str, entity_type: str, entity_key: str, field: str) -> None:
        self.r.delete(
            self._k(tenant_id, entity_type, entity_key, field),
            self._nk(tenant_id, entity_type, entity_key, field),
        )

    # -- stampede protection ---------------------------------------------------

    def acquire_refresh_lock(
        self, tenant_id: str, entity_type: str, entity_key: str, field: str, ttl_seconds: int | None = None
    ) -> str | None:
        """SET NX lock. Returns a token when acquired, else None (someone else refreshes)."""
        token = uuid.uuid4().hex
        key = f"{self.prefix}:lock:{tenant_id}:{entity_type}:{entity_key.lower()}:{field}"
        ok = self.r.set(key, token, nx=True, ex=ttl_seconds or self.settings.cache_lock_ttl_seconds)
        return token if ok else None

    def release_refresh_lock(
        self, tenant_id: str, entity_type: str, entity_key: str, field: str, token: str
    ) -> None:
        key = f"{self.prefix}:lock:{tenant_id}:{entity_type}:{entity_key.lower()}:{field}"
        # Compare-and-delete via Lua so we never release someone else's lock.
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"  # noqa: E501
        self.r.eval(script, 1, key, token)

    # -- metrics -----------------------------------------------------------------

    @staticmethod
    def _count(entity_type: str, field: str, status: str) -> None:
        # Bounded cardinality: entity_type (2) x field (~25) x status (5).
        CACHE_OPS.labels(entity_type=entity_type, field=field, status=status).inc()
