"""Registry-facing adapter for the public-source snapshot replay providers (SEC EDGAR, GLEIF, Wikidata).

An operator enables one by adding a ProviderConfig row with adapter "snapshot.sec", "snapshot.gleif" or
"snapshot.wikidata" and config {"snapshot_file": "<name>.json", "as_of": "YYYY-MM-DD"} (optionally
"display_name"; any other config key is refused). It is off unless RELAYIQ_SNAPSHOT_ROOT names a directory;
the root comes from the environment only, never from provider config, and the file is resolved inside it.

Product-path rules (stricter than the offline SnapshotProvider):
- Per call, nothing shared: every call builds a fresh SnapshotProvider from this call's identifiers, so no
  binding log, company name or reason is kept between calls or visible to another tenant.
- No identity facts in the product path: facts are per-tenant assertions and there is no per-tenant store
  for them yet. Registry sources therefore bind only on an exact registrant website host equal to the
  account's own root domain; GLEIF records carry no website, so GLEIF always abstains here.
- Wikidata answers only when a record's official website host equals the account's domain exactly.
- root_domain is never offered (the account's domain is an input); values that would not fit the account
  columns are dropped, never truncated.
- No account domain means no answer.
- Leakage guard: a snapshot file may contain only the source sections {"sec", "gleif", "wikidata"};
  anything else (labels, answer keys, truth tables) is refused at load time, and so is any path outside
  the snapshot root, symlinks included.
"""

from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from relayiq.enums import EntityType, ProviderOutcome
from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter
from relayiq.providers.public_sources import ACCOUNT_FIELDS_BY_SOURCE, SnapshotProvider, _safe_host

SNAPSHOT_ROOT_ENV = "RELAYIQ_SNAPSHOT_ROOT"
SOURCES = ("sec", "gleif", "wikidata")
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
MAX_VALUE_CHARS = 120  # Account.hq_city / hq_state column width
# The only ProviderConfig.config keys a snapshot row may carry. The snapshot root comes from the
# environment only, never from config (admins can edit config through the API).
CONFIG_KEYS = frozenset({"snapshot_file", "as_of", "display_name"})
_REGISTRY_KEYS = frozenset({"capabilities", "field_costs", "provider_key"})  # added by the registry


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
    try:
        base = Path(root).resolve()
        path = (base / rel).resolve()
    except (OSError, ValueError):  # e.g. a NUL byte in the name
        raise SnapshotConfigError("config.snapshot_file is not a valid file name") from None
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
    st = path.stat()
    doc = _parse_snapshot(str(path), st.st_mtime_ns, st.st_size)
    records = doc.get(source)
    if not isinstance(records, dict):
        raise SnapshotConfigError(f"snapshot file has no {source!r} section")
    return records


@lru_cache(maxsize=8)
def _parse_snapshot(path: str, mtime_ns: int, size: int) -> dict:
    """Parsed, validated snapshot document. Cached by (path, mtime, size): the registry rebuilds adapters
    on every job, and the file is only re-parsed when it changes. Records are never mutated."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise SnapshotConfigError("snapshot file is larger than the allowed maximum")
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotConfigError(f"snapshot file is not readable JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict):
        raise SnapshotConfigError("snapshot file must be a JSON object")
    extra = set(doc) - set(SOURCES)
    if extra:  # count only: key names come from the file and are not echoed into logs
        raise SnapshotConfigError(f"snapshot file has {len(extra)} section(s) other than {list(SOURCES)}")
    return doc


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
        # root_domain is not offered: the account's own domain is a required input here, and the source's
        # value is a raw website URL, not a normalised domain.
        supported = set(ACCOUNT_FIELDS_BY_SOURCE[source]) - {"root_domain"}
        allowed = (
            supported
            if capabilities is None
            else set(capabilities.get(EntityType.ACCOUNT.value, ())) & supported
        )
        self._fields = allowed

    def capabilities(self) -> dict[str, set[str]]:
        return {EntityType.ACCOUNT.value: set(self._fields)}

    def field_cost(self, entity_type: str, field_name: str) -> float:
        return 0.0

    def enrich(self, entity_type, identifiers, fields, *, timeout_ms: int = 8000) -> EnrichmentCallResult:
        try:
            return self._enrich(entity_type, identifiers, fields, timeout_ms)
        except Exception as exc:  # noqa: BLE001 - the adapter contract is "never raises"
            return self._fail(entity_type, f"snapshot replay failed ({type(exc).__name__})")

    def _fail(self, entity_type, error: str) -> EnrichmentCallResult:
        return EnrichmentCallResult(self.key, entity_type, ProviderOutcome.PERM_FAIL, error=error)

    def _enrich(self, entity_type, identifiers, fields, timeout_ms: int) -> EnrichmentCallResult:
        if entity_type != EntityType.ACCOUNT.value:
            return self._fail(entity_type, "unsupported entity type")
        name = str(identifiers.get("company_name") or identifiers.get("name") or "").strip()
        domain = str(identifiers.get("root_domain") or identifiers.get("website") or "").strip()
        wanted = [f for f in list(fields or []) if f in self._fields]
        if not name or not domain:
            return self._fail(entity_type, "company name and account domain are required")
        if not wanted:
            return self._fail(entity_type, "no supported fields requested")
        rec = self._records.get(name)
        if rec is None:
            return self._fail(entity_type, "no matching record")
        if self._source == "wikidata":
            # Exact host only: the offline provider compares root domains, which can treat unrelated sites
            # under a shared suffix (e.g. two hosts under github.io) as the same company.
            want, _ = _safe_host(domain)
            details = [
                d
                for d in (rec.get("details") or [])
                if want and isinstance(d, dict) and _safe_host(d.get("website"))[0] == want
            ]
            rec = dict(rec, details=details)
        inner = SnapshotProvider(
            self._source,
            self.display_name,
            None,
            self._as_of,
            records={name: rec},
            input_domains={name: domain},
        )
        result = inner.enrich(entity_type, {"company_name": name}, wanted, timeout_ms=timeout_ms)
        binding = inner.binding_log.get(name)
        result.provider_key = self.key
        # Values must fit the account columns they are written to; never truncate (that would invent a value).
        result.fields = {
            f: v
            for f, v in result.fields.items()
            if len(str(v.value)) <= MAX_VALUE_CHARS and "\x00" not in str(v.value)
        }
        if result.ok and not result.fields:
            result.outcome = ProviderOutcome.PERM_FAIL
            result.error = "no usable value"
        if not result.ok and result.error != "no usable value":
            # Short, stable error (stored in a bounded column); the detailed reason stays in raw_payload.
            if binding is not None and binding.get("state") == "abstain":
                result.error = "identity not bound; needs review"
            elif result.error and result.error.startswith("no matching record"):
                result.error = "no matching record"
            else:
                result.error = "snapshot replay failed"
        if binding is not None:
            result.raw_payload = {
                "identity_binding": {k: binding[k] for k in ("state", "reason", "basis") if k in binding}
            }
        return result


def _factory(source: str):
    def make(**overrides) -> SnapshotReplayAdapter:
        unknown = set(overrides) - CONFIG_KEYS - _REGISTRY_KEYS
        if unknown:  # e.g. snapshot_root or identity facts smuggled in through provider config
            raise SnapshotConfigError(
                f"config has {len(unknown)} unsupported key(s); allowed: {sorted(CONFIG_KEYS)}"
            )
        return SnapshotReplayAdapter(source, **overrides)

    make.__name__ = f"make_snapshot_{source}"
    return make


make_snapshot_sec = _factory("sec")
make_snapshot_gleif = _factory("gleif")
make_snapshot_wikidata = _factory("wikidata")
