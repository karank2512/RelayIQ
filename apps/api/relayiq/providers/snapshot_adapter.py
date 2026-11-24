"""Registry-facing adapter for the public-source snapshot replay providers (SEC EDGAR, GLEIF, Wikidata).

An operator enables one by adding a ProviderConfig row with adapter "snapshot.sec", "snapshot.gleif" or
"snapshot.wikidata" and config {"snapshot_file": "<name>.json", "as_of": "YYYY-MM-DD"}. It is off unless
RELAYIQ_SNAPSHOT_ROOT names a directory; the file is resolved inside that directory only.

Product-path rules (stricter than the offline SnapshotProvider):
- Per call, nothing shared: every call builds a fresh SnapshotProvider from this call's identifiers, so no
  binding log, company name or reason is kept between calls or visible to another tenant.
- No identity facts in the product path: facts are per-tenant assertions and there is no per-tenant store
  for them yet. Registry sources therefore bind only on an exact registrant website host equal to the
  account's own root domain; GLEIF records carry no website, so GLEIF always abstains here.
- Wikidata answers only when a record's official website has the account's root domain.
- No account domain means no answer.
- Leakage guard: a snapshot file may contain only the source sections {"sec", "gleif", "wikidata"};
  anything else (labels, answer keys, truth tables) is refused at load time, and so is any path outside
  the snapshot root, symlinks included.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from relayiq.enums import EntityType, ProviderOutcome
from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter
from relayiq.providers.public_sources import ACCOUNT_FIELDS_BY_SOURCE, SnapshotProvider

SNAPSHOT_ROOT_ENV = "RELAYIQ_SNAPSHOT_ROOT"
SOURCES = ("sec", "gleif", "wikidata")
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024


class SnapshotConfigError(ValueError):
    """The provider config or snapshot file is unusable; the provider is not loaded."""


def _resolve_snapshot(snapshot_file: str, root: str | None) -> Path:
    if not root:
        raise SnapshotConfigError(f"{SNAPSHOT_ROOT_ENV} is not set; snapshot providers are disabled")
    if not isinstance(snapshot_file, str) or not snapshot_file.strip():
        raise SnapshotConfigError("config.snapshot_file is required")
    rel = Path(snapshot_file)
    if rel.is_absolute() or ".." in rel.parts:
        raise SnapshotConfigError("config.snapshot_file must be a relative name inside the snapshot root")
    base = Path(root).resolve()
    path = (base / rel).resolve()
    if base not in path.parents:
        raise SnapshotConfigError("config.snapshot_file resolves outside the snapshot root")
    if not path.is_file():
        raise SnapshotConfigError("config.snapshot_file does not exist in the snapshot root")
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise SnapshotConfigError("snapshot file is larger than the allowed maximum")
    return path


def load_snapshot_records(source: str, snapshot_file: str, root: str | None) -> dict:
    """This source's {company name: record} mapping from a validated snapshot file."""
    if source not in SOURCES:
        raise SnapshotConfigError(f"unknown snapshot source {source!r}")
    path = _resolve_snapshot(snapshot_file, root)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotConfigError(f"snapshot file is not readable JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict):
        raise SnapshotConfigError("snapshot file must be a JSON object")
    extra = sorted(set(doc) - set(SOURCES))
    if extra:
        raise SnapshotConfigError(f"snapshot file has sections other than {list(SOURCES)}: {extra}")
    records = doc.get(source)
    if not isinstance(records, dict):
        raise SnapshotConfigError(f"snapshot file has no {source!r} section")
    return records


class SnapshotReplayAdapter(ProviderAdapter):
    simulation_mode = False

    def __init__(
        self,
        source: str,
        *,
        snapshot_file: str,
        as_of: str,
        snapshot_root: str | None = None,
        capabilities: dict[str, set[str]] | None = None,
        field_costs: dict[str, float] | None = None,  # accepted from registry rows; replay is free (0)
        display_name: str | None = None,
        provider_key: str | None = None,
    ):
        try:
            date.fromisoformat(str(as_of))
        except ValueError:
            raise SnapshotConfigError("config.as_of must be an ISO date (YYYY-MM-DD)") from None
        root = snapshot_root if snapshot_root is not None else os.environ.get(SNAPSHOT_ROOT_ENV)
        self._records = load_snapshot_records(source, snapshot_file, root)
        self._source = source
        self._as_of = str(as_of)
        self.key = provider_key or source
        self.display_name = display_name or f"Snapshot replay ({source})"
        supported = set(ACCOUNT_FIELDS_BY_SOURCE[source])
        allowed = set((capabilities or {}).get(EntityType.ACCOUNT.value, supported)) & supported
        self._fields = allowed

    def capabilities(self) -> dict[str, set[str]]:
        return {EntityType.ACCOUNT.value: set(self._fields)}

    def field_cost(self, entity_type: str, field_name: str) -> float:
        return 0.0

    def enrich(self, entity_type, identifiers, fields, *, timeout_ms: int = 8000) -> EnrichmentCallResult:
        if entity_type != EntityType.ACCOUNT.value:
            return EnrichmentCallResult(
                self.key, entity_type, ProviderOutcome.PERM_FAIL, error="unsupported entity type"
            )
        name = (identifiers.get("company_name") or identifiers.get("name") or "").strip()
        domain = (identifiers.get("root_domain") or identifiers.get("website") or "").strip()
        wanted = [f for f in fields if f in self._fields]
        if not name or not domain:
            return EnrichmentCallResult(
                self.key,
                entity_type,
                ProviderOutcome.PERM_FAIL,
                error="company name and account domain are required",
            )
        if not wanted:
            return EnrichmentCallResult(
                self.key, entity_type, ProviderOutcome.PERM_FAIL, error="no supported fields requested"
            )
        try:
            inner = SnapshotProvider(
                self._source,
                self.display_name,
                None,
                self._as_of,
                records=self._records,
                input_domains={name: domain},
            )
            result = inner.enrich(entity_type, {"company_name": name}, wanted, timeout_ms=timeout_ms)
            binding = inner.binding_log.get(name)
        except Exception as exc:  # noqa: BLE001 — the adapter contract is "never raises"
            return EnrichmentCallResult(
                self.key,
                entity_type,
                ProviderOutcome.PERM_FAIL,
                error=f"snapshot replay failed ({type(exc).__name__})",
            )
        result.provider_key = self.key
        if binding is not None:
            result.raw_payload = {
                "identity_binding": {k: binding[k] for k in ("state", "reason", "basis") if k in binding}
            }
        return result


def _factory(source: str):
    def make(**overrides) -> SnapshotReplayAdapter:
        return SnapshotReplayAdapter(source, **overrides)

    make.__name__ = f"make_snapshot_{source}"
    return make


make_snapshot_sec = _factory("sec")
make_snapshot_gleif = _factory("gleif")
make_snapshot_wikidata = _factory("wikidata")
