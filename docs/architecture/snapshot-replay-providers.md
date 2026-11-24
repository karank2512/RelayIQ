# Snapshot replay providers (SEC EDGAR, GLEIF, Wikidata)

Status: wired into the provider registry, **off by default**, tested with synthetic fixtures only. No real
snapshot ships with the repo and nothing here calls a live API.

## What it does

`relayiq/providers/snapshot_adapter.py` exposes three registry adapters, `snapshot.sec`, `snapshot.gleif`
and `snapshot.wikidata`. Each answers account fields (`hq_city`, `hq_state`, and `root_domain` /
`founded_year` where the source has them) from a dated JSON snapshot of free public sources, using the
identity-binding rules in `relayiq/providers/public_sources.py`. Replay is free (cost 0).

## Enabling one (operator)

1. Put a snapshot file in a directory and set `RELAYIQ_SNAPSHOT_ROOT` to that directory.
2. Add a `ProviderConfig` row, e.g. key `replay_sec`, adapter `snapshot.sec`,
   config `{"snapshot_file": "snapshot.json", "as_of": "2026-11-20"}`.
3. Optionally restrict fields with `ProviderCapability` rows, and route fields to it with a routing policy.

Snapshot format: `{"sec": {company name: record}, "gleif": {...}, "wikidata": {...}}`. Only these three
sections are allowed.

## Product-path rules

- **Binding:** SEC and GLEIF records are used only when the registrant's website host exactly equals the
  account's own root domain. A name match alone never answers. GLEIF records carry no website, so GLEIF
  always abstains in the product path. Wikidata answers only when a record's official website has the
  account's root domain. No account domain means no answer.
- **No identity facts in the product path:** the identity-facts sidecar (`identity_facts.py`) holds
  per-tenant assertions, and there is no per-tenant store for them yet, so the registry adapters accept none.
- **Per call, nothing shared:** every call builds a fresh provider from that call's identifiers. No binding
  log, company name or reason is kept between calls or visible to another tenant. The call's binding
  outcome (state, reason, basis) is returned in the result's `raw_payload`.
- **Never raises:** unusable input or records give a `perm_fail` result, so routing falls back.

## Load-time guards (leakage and safety)

A row is skipped, and the rest of the registry still loads, when:

- `RELAYIQ_SNAPSHOT_ROOT` is unset (the feature is off);
- `snapshot_file` is absolute, contains `..`, resolves outside the root (symlinks included), is missing or
  is larger than 50 MB;
- the file is not a JSON object, lacks the source's section, or has **any section other than
  `sec` / `gleif` / `wikidata`** — so label files, answer keys or truth tables can never be loaded as a
  provider snapshot;
- `as_of` is not an ISO date, or the config carries unknown keys (for example identity facts).

The registry logs `provider_load_failed` with the provider key, adapter and error type. It logs no file
contents and no file names.

## Holdout readiness

For an evaluation on held-out companies, the protections in the product path are:

- the snapshot file can contain only source records (the section allow-list above), never labels;
- nothing is learned or cached across calls, so one company's answers cannot influence another's;
- a name match never binds a registry record, so a similar-name company cannot leak in.

Out of scope here (owner decisions): collecting a new snapshot, choosing holdout companies, locking an
answer key, and the evaluation run itself.

## Limits

Not a working enrichment source for real accounts until a real snapshot is collected (no collector ships
here). GLEIF never binds in the product path. The registered-agent check is incomplete and US-centric.
