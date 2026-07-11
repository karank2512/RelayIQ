"""Provider SDK: the adapter interface every enrichment provider implements.

Real provider integrations and simulators share this contract (ADR-009). The router,
orchestrator, ledger, and health tracker only ever see these types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from relayiq.enums import EntityType, ProviderOutcome


@dataclass(frozen=True)
class ProviderFieldValue:
    """A single field value returned by a provider, with provenance."""

    field_name: str
    value: Any
    provider_confidence: float | None = None
    source_age_days: int | None = None  # provider-reported freshness when available
    provenance: str = ""


@dataclass
class EnrichmentCallResult:
    """Normalized result of one provider call (error-normalized, never raises)."""

    provider_key: str
    entity_type: str
    outcome: ProviderOutcome
    fields: dict[str, ProviderFieldValue] = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_credits: float = 0.0
    error: str | None = None
    raw_payload: dict = field(default_factory=dict)
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome == ProviderOutcome.SUCCESS


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_base_seconds: float = 0.2
    retry_on: tuple[ProviderOutcome, ...] = (ProviderOutcome.TEMP_FAIL, ProviderOutcome.TIMEOUT)


class ProviderAdapter(ABC):
    """Base adapter. Implementations must be side-effect free apart from the remote call."""

    key: str = "base"
    version: str = "1"
    display_name: str = "Base provider"
    simulation_mode: bool = True

    @abstractmethod
    def capabilities(self) -> dict[str, set[str]]:
        """{entity_type: {field_name, ...}} the provider can enrich."""

    @abstractmethod
    def field_cost(self, entity_type: str, field_name: str) -> float:
        """Estimated cost in credits for one field."""

    @abstractmethod
    def enrich(
        self,
        entity_type: str,
        identifiers: dict[str, str],
        fields: list[str],
        *,
        timeout_ms: int = 8000,
    ) -> EnrichmentCallResult:
        """Execute one enrichment call. Must normalize all errors into the result."""

    # Shared helpers -------------------------------------------------------

    def supports(self, entity_type: str, field_name: str) -> bool:
        return field_name in self.capabilities().get(entity_type, set())

    def estimate_cost(self, entity_type: str, fields: list[str]) -> float:
        return round(
            sum(self.field_cost(entity_type, f) for f in fields if self.supports(entity_type, f)), 4
        )

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy()

    def health(self) -> dict:
        return {"provider": self.key, "version": self.version, "simulation": self.simulation_mode}

    @staticmethod
    def entity_key(entity_type: str, identifiers: dict[str, str]) -> str:
        """Stable lookup key for an entity as providers see it."""
        if entity_type == EntityType.CONTACT.value:
            return (identifiers.get("work_email") or identifiers.get("world_id") or "").lower()
        return (identifiers.get("root_domain") or identifiers.get("world_id") or "").lower()
