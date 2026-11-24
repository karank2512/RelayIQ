# Snapshot replay providers (SEC EDGAR, GLEIF, Wikidata)

Status: wired into the provider registry, **off by default**, tested with synthetic fixtures only. No real
snapshot ships with the repo and nothing here calls a live API.

## What it does

`relayiq/providers/snapshot_adapter.py` exposes three registry adapters, `snapshot.sec`, `snapshot.gleif`
and `snapshot.wikidata`. Each answers account fields (`hq_city`, `hq_state`, and `founded_year` from
Wikidata) from a dated JSON snapshot of free public sources, using the identity-binding rules in
`relayiq/providers/public_sources.py`. `root_domain` is not offered: the account's own domain is a required
input, and the sources hold raw website URLs rather than normalised domains. Replay is free (cost 0).

## Enabling one (operator)

1. Put a snapshot file in a directory and set `RELAYIQ_SNAPSHOT_ROOT` to that directory.
2. Add a `ProviderConfig` row, e.g. key `replay_sec`, adapter `snapshot.sec`,
   config `{"snapshot_file": "snapshot.json", "as_of": "2026-11-20"}`.
3. Optionally restrict fields with `ProviderCapability` rows, and route fields to it with a routing policy.

Provider rows are global. An enabled row is a candidate for every tenant on any supported field their
routing policy does not pin to other providers, and at cost 0 it ranks ahead of paid providers there.
Pin fields in tenant policies if that is not wanted.

Snapshot format: `{"sec": {company name: record}, "gleif": {...}, "wikidata": {...}}`. Only these three
top-level sections are allowed. `as_of` is an ISO 8601 date.

## Product-path rules

- **Binding:** SEC and GLEIF records are used only when the registrant's website host exactly equals the
  account's own root domain. A name match alone never answers. GLEIF records carry no website, so GLEIF
  always abstains in the product path. Wikidata answers only when a record's official website host equals
  the account's domain exactly. No account domain means no answer. Values that would not fit the account
  columns (over 120 characters, or containing a NUL byte) are dropped, never truncated.
- **No identity facts in the product path:** the identity-facts sidecar (`identity_facts.py`) holds
  per-tenant assertions, and there is no per-tenant store for them yet, so the registry adapters accept none.
- **Per call, nothing shared:** every call builds a fresh provider from that call's identifiers. No binding
  log, company name or reason is kept between calls or visible to another tenant. The call's binding
  outcome (state, reason, basis) is returned in the result's `raw_payload`.
- **Never raises:** unusable input or records give a `perm_fail` result, so routing falls back.

## Load-time guards

A row is skipped, and the rest of the registry still loads, when:

- `RELAYIQ_SNAPSHOT_ROOT` is unset (the feature is off). The root is read from the environment only;
  provider config cannot set or change it, and any config key other than `snapshot_file`, `as_of` and
  `display_name` is refused (so identity facts cannot be supplied through config either);
- `snapshot_file` is absolute, contains `..`, is not a valid name, resolves outside the root (symlinks
  included), is missing, or is larger than 50 MB;
- the file is not a JSON object, lacks the source's section, or has any section other than
  `sec` / `gleif` / `wikidata`, so a file with any other top-level section (for example labels) is not
  loaded as a provider snapshot. The check is on top-level sections only; keep snapshot files to source
  records;
- `as_of` is not an ISO date.

The registry logs `provider_load_failed` with the provider key, adapter, error type and, for configuration
errors, a fixed reason. It logs no file contents, file names or section names. A parsed snapshot is cached
by path, modification time and size, so it is re-read only when the file changes. The checks and the read
are separate steps, so anyone able to write inside the snapshot root can change the file between them:
keep the root writable only by the operator.

## Scope of the isolation

The adapter itself keeps no state between calls: each call builds a fresh provider from that call's
identifiers. The rest of the enrichment pipeline works as for any provider: accepted values go through the
field cache, the canonical store, reconciliation and routing history as usual.

## Limits

Not a working enrichment source for real accounts until a real snapshot is collected (no collector ships
here). GLEIF never binds in the product path. The registered-agent check is incomplete and US-centric.
