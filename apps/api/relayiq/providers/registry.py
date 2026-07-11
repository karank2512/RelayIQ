"""Provider registry: builds adapter instances from DB ProviderConfig rows.

Adapter construction is data-driven so operators can tune simulator knobs (or swap in a
real adapter) without code changes. Circuit-breaker state lives here too.
"""

import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.models import ProviderCapability, ProviderConfig
from relayiq.providers.base import ProviderAdapter
from relayiq.providers.simulators import make_alpha, make_beta

_FACTORIES = {
    "simulator.alpha": make_alpha,
    "simulator.beta": make_beta,
}


class CircuitBreaker:
    """Simple failure-threshold breaker: opens after `threshold` consecutive retryable
    failures, half-opens after `cooldown_seconds`. Prevents retry storms (threat model §11)."""

    def __init__(self, threshold: int = 5, cooldown_seconds: float = 30.0):
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                return "half_open"
            return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._opened_at = time.monotonic()


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        self._configs: dict[str, ProviderConfig] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def load(self, session: Session) -> None:
        rows = session.execute(select(ProviderConfig).where(ProviderConfig.enabled.is_(True))).scalars()
        with self._lock:
            self._adapters.clear()
            self._configs.clear()
            for cfg in rows:
                factory = _FACTORIES.get(cfg.adapter)
                if factory is None:
                    continue
                caps = session.execute(
                    select(ProviderCapability).where(ProviderCapability.provider_id == cfg.id)
                ).scalars().all()
                overrides = dict(cfg.config or {})
                if caps:
                    field_costs = {c.field_name: float(c.cost_credits) for c in caps}
                    capabilities: dict[str, set[str]] = {}
                    for c in caps:
                        capabilities.setdefault(c.entity_type, set()).add(c.field_name)
                    overrides.setdefault("field_costs", field_costs)
                    overrides.setdefault("capabilities", capabilities)
                self._adapters[cfg.key] = factory(**overrides)
                self._configs[cfg.key] = cfg
                self._breakers.setdefault(cfg.key, CircuitBreaker())

    def get(self, key: str) -> ProviderAdapter | None:
        with self._lock:
            return self._adapters.get(key)

    def config(self, key: str) -> ProviderConfig | None:
        with self._lock:
            return self._configs.get(key)

    def breaker(self, key: str) -> CircuitBreaker:
        with self._lock:
            return self._breakers.setdefault(key, CircuitBreaker())

    def all(self) -> dict[str, ProviderAdapter]:
        with self._lock:
            return dict(self._adapters)

    def available(self, key: str) -> bool:
        return self.get(key) is not None and self.breaker(key).allow()


_registry: ProviderRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(session: Session | None = None, refresh: bool = False) -> ProviderRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ProviderRegistry()
            refresh = True
    if refresh and session is not None:
        _registry.load(session)
    return _registry


def reset_registry() -> None:
    global _registry
    with _registry_lock:
        _registry = None
