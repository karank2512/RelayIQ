"""Snapshot replay adapters wired into the provider registry: product-path behaviour, config refusals,
leakage guard, per-call isolation and registry error isolation.

Inline synthetic fixtures only (Acme/Globex names, *.example hosts); nothing here is real data.
"""

import json
import os

import pytest
from structlog.testing import capture_logs

from relayiq.enums import ProviderOutcome
from relayiq.models import ProviderCapability, ProviderConfig
from relayiq.providers import registry as registry_mod
from relayiq.providers import snapshot_adapter as SA
from relayiq.services import provider_exec

SNAP = {
    "sec": {
        "Acme": {
            "chosen": {
                "cik": "0000000001",
                "name": "ACME INC.",
                "hq_city": "Somewhere",
                "hq_state": "CA",
                "website": "https://www.acme.example/",
                "state_of_incorporation": "DE",
                "latest_filing_date": "2026-01-01",
            }
        },
        "Globex": {
            "chosen": {
                "cik": "0000000002",
                "name": "GLOBEX CORP",
                "hq_city": "Elsewhere",
                "hq_state": "NY",
                "website": "https://globex-holdings.example/",
                "state_of_incorporation": "DE",
                "latest_filing_date": "2026-01-01",
            }
        },
    },
    "gleif": {
        "Acme": {
            "candidates": [
                {
                    "lei": "LEI-1",
                    "name": "ACME, INC.",
                    "status": "ACTIVE",
                    "last_update": "2026-01-01",
                    "hq": {
                        "city": "Wilmington",
                        "region": "US-DE",
                        "country": "US",
                        "addressLines": ["1 Example Way"],
                    },
                }
            ],
            "chosen": None,
        },
    },
    "wikidata": {
        "Acme": {
            "candidates": [],
            "chosen": None,
            "details": [
                {
                    "id": "Q1",
                    "label": "Acme",
                    "desc": "2016 video game",
                    "website": "https://acme-game.example",
                },
                {
                    "id": "Q2",
                    "label": "Acme",
                    "desc": "software company",
                    "website": "https://acme.example",
                    "hq_city": "Springfield",
                    "hq_state": "IL",
                    "founded_year": 2001,
                },
            ],
        },
    },
}
ACME = {"company_name": "Acme", "name": "Acme", "root_domain": "acme.example"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "snap.json").write_text(json.dumps(SNAP))
    return str(tmp_path)


def make(source, root, **kw):
    return SA.SnapshotReplayAdapter(
        source, snapshot_file="snap.json", as_of="2026-11-20", snapshot_root=root, **kw
    )


# ---------------------------------------------------------------- product-path behaviour
class TestProductPath:
    def test_sec_binds_only_on_the_accounts_exact_website_host(self, root):
        a = make("sec", root, provider_key="replay_sec")
        r = a.enrich("account", ACME, ["hq_city", "hq_state"])
        assert r.outcome == ProviderOutcome.SUCCESS
        assert r.provider_key == "replay_sec"
        assert r.fields["hq_city"].value == "Somewhere"
        assert (
            "identity bound (registrant website host equals CRM input domain)"
            in r.fields["hq_city"].provenance
        )
        assert r.raw_payload["identity_binding"]["state"] == "bound"

    def test_sec_different_host_abstains_with_reason(self, root):
        a = make("sec", root)
        r = a.enrich("account", {"company_name": "Globex", "root_domain": "globex.example"}, ["hq_city"])
        assert r.outcome == ProviderOutcome.PERM_FAIL and r.fields == {}
        assert "differs" in r.raw_payload["identity_binding"]["reason"]
        assert "identity not bound" in (r.error or "")

    def test_name_match_alone_never_answers(self, root):
        for source in ("sec", "gleif", "wikidata"):
            r = make(source, root).enrich("account", {"company_name": "Acme"}, ["hq_city"])
            assert r.outcome == ProviderOutcome.PERM_FAIL, source
            assert r.error == "company name and account domain are required", source

    def test_gleif_abstains_in_the_product_path(self, root):
        # GLEIF records carry no website and the product path loads no identity facts: never bound.
        r = make("gleif", root).enrich("account", ACME, ["hq_city"])
        assert r.outcome == ProviderOutcome.PERM_FAIL and r.fields == {}
        assert r.raw_payload["identity_binding"]["state"] == "abstain"

    def test_wikidata_needs_the_accounts_domain(self, root):
        ok = make("wikidata", root).enrich("account", ACME, ["hq_city", "founded_year"])
        assert ok.fields["hq_city"].value == "Springfield"
        assert ok.fields["founded_year"].value == "2001"
        wrong = make("wikidata", root).enrich("account", dict(ACME, root_domain="other.example"), ["hq_city"])
        assert wrong.outcome == ProviderOutcome.PERM_FAIL and wrong.fields == {}

    def test_contacts_and_unsupported_fields_are_refused(self, root):
        a = make("sec", root)
        assert a.enrich("contact", ACME, ["job_title"]).error == "unsupported entity type"
        assert a.enrich("account", ACME, ["employee_count"]).error == "no supported fields requested"

    def test_capability_rows_restrict_fields(self, root):
        a = make("sec", root, capabilities={"account": {"hq_city", "employee_count"}})
        assert a.capabilities() == {"account": {"hq_city"}}
        r = a.enrich("account", ACME, ["hq_city", "hq_state"])
        assert set(r.fields) == {"hq_city"}

    def test_replay_is_free(self, root):
        a = make("sec", root, field_costs={"hq_city": 9.0})
        assert a.field_cost("account", "hq_city") == 0.0


# ---------------------------------------------------------------- per-call isolation (no cross-tenant state)
class TestIsolation:
    def test_no_state_is_kept_between_calls(self, root):
        a = make("sec", root)
        before = dict(vars(a))
        a.enrich("account", ACME, ["hq_city"])
        r2 = a.enrich("account", {"company_name": "Globex", "root_domain": "globex.example"}, ["hq_city"])
        assert vars(a).keys() == before.keys()
        assert not hasattr(a, "binding_log")
        assert "acme" not in json.dumps(r2.raw_payload).lower()

    def test_identity_facts_cannot_be_injected_from_config(self, root):
        with pytest.raises(TypeError):
            SA.SnapshotReplayAdapter(
                "sec",
                snapshot_file="snap.json",
                as_of="2026-11-20",
                snapshot_root=root,
                identity_facts={"Acme": {}},
            )

    def test_never_raises_on_malformed_records(self, tmp_path):
        (tmp_path / "bad.json").write_text(json.dumps({"sec": {"Acme": "not a record"}}))
        a = SA.SnapshotReplayAdapter(
            "sec", snapshot_file="bad.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        r = a.enrich("account", ACME, ["hq_city"])
        assert r.outcome == ProviderOutcome.PERM_FAIL
        assert r.fields == {} and r.error == "snapshot replay failed"

    def test_errors_are_short_and_stable(self, root):
        # errors are stored in a bounded column; the detailed reason stays in raw_payload
        cases = [
            ("sec", {"company_name": "Globex", "root_domain": "globex.example"}, ["hq_city"]),
            ("gleif", ACME, ["hq_city"]),
            ("wikidata", dict(ACME, root_domain="other.example"), ["hq_city"]),
            ("sec", {"company_name": "Nobody", "root_domain": "nobody.example"}, ["hq_city"]),
            ("sec", {"company_name": "Acme"}, ["hq_city"]),
            ("sec", ACME, ["employee_count"]),
        ]
        errors = set()
        for source, ids, fields in cases:
            r = make(source, root).enrich("account", ids, fields)
            assert r.outcome == ProviderOutcome.PERM_FAIL
            assert r.error and len(r.error) <= provider_exec.ERROR_TYPE_MAX, r.error
            errors.add(r.error)
        assert "identity not bound; needs review" in errors
        r = make("sec", root).enrich(
            "account", {"company_name": "Globex", "root_domain": "globex.example"}, ["hq_city"]
        )
        assert "differs" in r.raw_payload["identity_binding"]["reason"]

    def test_provider_error_text_is_bounded_before_it_is_stored(self):
        assert provider_exec._error_type(None) is None
        assert provider_exec._error_type("short") == "short"
        long = "x" * 500
        bounded = provider_exec._error_type(long)
        assert (
            bounded is not None and len(bounded) == provider_exec.ERROR_TYPE_MAX and bounded.endswith("...")
        )


# ---------------------------------------------------------------- config refusals and leakage guard
class TestConfigGuards:
    def test_disabled_without_a_snapshot_root(self, root, monkeypatch):
        monkeypatch.delenv(SA.SNAPSHOT_ROOT_ENV, raising=False)
        with pytest.raises(SA.SnapshotConfigError, match="disabled"):
            SA.SnapshotReplayAdapter("sec", snapshot_file="snap.json", as_of="2026-11-20")

    def test_root_is_read_from_the_environment(self, root, monkeypatch):
        monkeypatch.setenv(SA.SNAPSHOT_ROOT_ENV, root)
        a = SA.SnapshotReplayAdapter("sec", snapshot_file="snap.json", as_of="2026-11-20")
        assert a.enrich("account", ACME, ["hq_city"]).ok

    @pytest.mark.parametrize(
        "name", ["/etc/hosts", "../snap.json", "sub/../../snap.json", "", "missing.json"]
    )
    def test_paths_outside_the_root_or_missing_are_refused(self, root, name):
        with pytest.raises(SA.SnapshotConfigError):
            SA.SnapshotReplayAdapter("sec", snapshot_file=name, as_of="2026-11-20", snapshot_root=root)

    def test_symlink_escaping_the_root_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "snap.json").write_text(json.dumps(SNAP))
        inside = tmp_path / "root"
        inside.mkdir()
        os.symlink(outside / "snap.json", inside / "link.json")
        with pytest.raises(SA.SnapshotConfigError, match="outside the snapshot root"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="link.json", as_of="2026-11-20", snapshot_root=str(inside)
            )

    @pytest.mark.parametrize("extra", ["labels", "answer_key", "truth"])
    def test_files_with_non_source_sections_are_refused(self, tmp_path, extra):
        (tmp_path / "leaky.json").write_text(json.dumps(dict(SNAP, **{extra: {"Acme": {"hq_city": "X"}}})))
        with pytest.raises(SA.SnapshotConfigError, match="sections other than"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="leaky.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
            )

    def test_bad_json_missing_section_bad_date_unknown_source_and_size(self, root, tmp_path, monkeypatch):
        (tmp_path / "broken.json").write_text("{not json")
        with pytest.raises(SA.SnapshotConfigError, match="not readable JSON"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="broken.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
            )
        (tmp_path / "only_sec.json").write_text(json.dumps({"sec": {}}))
        with pytest.raises(SA.SnapshotConfigError, match="no 'gleif' section"):
            SA.SnapshotReplayAdapter(
                "gleif", snapshot_file="only_sec.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
            )
        with pytest.raises(SA.SnapshotConfigError, match="ISO date"):
            SA.SnapshotReplayAdapter("sec", snapshot_file="snap.json", as_of="last week", snapshot_root=root)
        with pytest.raises(SA.SnapshotConfigError, match="unknown snapshot source"):
            SA.SnapshotReplayAdapter("crm", snapshot_file="snap.json", as_of="2026-11-20", snapshot_root=root)
        monkeypatch.setattr(SA, "MAX_SNAPSHOT_BYTES", 10)
        with pytest.raises(SA.SnapshotConfigError, match="larger than"):
            SA.SnapshotReplayAdapter("sec", snapshot_file="snap.json", as_of="2026-11-20", snapshot_root=root)


# ---------------------------------------------------------------- registry wiring (stub session, no database)
class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, configs, caps):
        self.configs, self.caps = configs, caps

    def execute(self, stmt):
        if "provider_capabilities" in str(stmt):
            pid = stmt.compile().params.get("provider_id_1")
            return _Rows([c for c in self.caps if c.provider_id == pid])
        return _Rows(self.configs)


def _cfg(pid, key, adapter, config):
    return ProviderConfig(id=pid, key=key, display_name=key, adapter=adapter, enabled=True, config=config)


class TestRegistryWiring:
    def test_snapshot_rows_load_and_one_bad_row_does_not_disable_the_rest(self, root, tmp_path, monkeypatch):
        monkeypatch.setenv(SA.SNAPSHOT_ROOT_ENV, root)
        monkeypatch.setenv("RELAYIQ_SHARED_BREAKER", "0")
        (tmp_path / "leaky.json").write_text(json.dumps(dict(SNAP, labels={})))
        configs = [
            _cfg("p1", "replay_sec", "snapshot.sec", {"snapshot_file": "snap.json", "as_of": "2026-11-20"}),
            _cfg(
                "p2",
                "replay_bad",
                "snapshot.wikidata",
                {"snapshot_file": "leaky.json", "as_of": "2026-11-20"},
            ),
            _cfg("p3", "replay_typo", "snapshot.sec", {"snapshot_file": "snap.json"}),
            _cfg(
                "p4",
                "replay_wiki",
                "snapshot.wikidata",
                {"snapshot_file": "snap.json", "as_of": "2026-11-20"},
            ),
        ]
        caps = [
            ProviderCapability(provider_id="p1", entity_type="account", field_name="hq_city", cost_credits=1)
        ]
        reg = registry_mod.ProviderRegistry()
        with capture_logs() as logs:
            reg.load(_Session(configs, caps))
        assert sorted(reg.all()) == ["replay_sec", "replay_wiki"]
        sec = reg.get("replay_sec")
        assert sec is not None and sec.key == "replay_sec"
        assert sec.capabilities() == {"account": {"hq_city"}}
        assert sec.field_cost("account", "hq_city") == 0.0
        r = sec.enrich("account", ACME, ["hq_city", "hq_state"])
        assert r.ok and set(r.fields) == {"hq_city"}
        failed = {e["provider"]: e for e in logs if e.get("event") == "provider_load_failed"}
        assert sorted(failed) == ["replay_bad", "replay_typo"]
        assert failed["replay_bad"]["error"] == "SnapshotConfigError"
        assert "leaky.json" not in json.dumps(failed)  # file names are not logged
        assert failed["replay_typo"]["error"] == "TypeError" and failed["replay_typo"]["reason"] is None

    def test_without_a_root_snapshot_rows_are_skipped(self, monkeypatch):
        monkeypatch.delenv(SA.SNAPSHOT_ROOT_ENV, raising=False)
        monkeypatch.setenv("RELAYIQ_SHARED_BREAKER", "0")
        reg = registry_mod.ProviderRegistry()
        reg.load(
            _Session(
                [
                    _cfg(
                        "p1",
                        "replay_sec",
                        "snapshot.sec",
                        {"snapshot_file": "snap.json", "as_of": "2026-11-20"},
                    )
                ],
                [],
            )
        )
        assert reg.all() == {}
