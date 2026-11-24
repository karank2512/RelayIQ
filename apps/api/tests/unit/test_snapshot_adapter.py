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

    def test_capability_rows_for_other_entity_types_grant_no_account_fields(self, root):
        a = make("sec", root, capabilities={"contact": {"job_title"}})
        assert a.capabilities() == {"account": set()}
        assert a.enrich("account", ACME, ["hq_city"]).error == "no supported fields requested"

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
        with pytest.raises(SA.SnapshotConfigError, match=r"section\(s\) other than"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="leaky.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
            )

    def test_invalid_file_name_is_a_config_error(self, root):
        with pytest.raises(SA.SnapshotConfigError, match="not a valid file name"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="snap\x00.json", as_of="2026-11-20", snapshot_root=root
            )

    def test_parsed_snapshot_is_cached_until_the_file_changes(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps(SNAP))
        SA._parse_snapshot.cache_clear()
        a = SA.SnapshotReplayAdapter(
            "sec", snapshot_file="c.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        b = SA.SnapshotReplayAdapter(
            "gleif", snapshot_file="c.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        assert SA._parse_snapshot.cache_info().misses == 1 and SA._parse_snapshot.cache_info().hits == 1
        changed = dict(SNAP, sec={"Acme": dict(SNAP["sec"]["Acme"])})
        changed["sec"]["Acme"]["chosen"] = dict(SNAP["sec"]["Acme"]["chosen"], hq_city="Newtown")
        f.write_text(json.dumps(changed) + "\n")  # size changes, so the file is re-parsed
        c = SA.SnapshotReplayAdapter(
            "sec", snapshot_file="c.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        assert a.enrich("account", ACME, ["hq_city"]).fields["hq_city"].value == "Somewhere"
        assert c.enrich("account", ACME, ["hq_city"]).fields["hq_city"].value == "Newtown"
        assert b.capabilities() == {"account": {"hq_city", "hq_state"}}

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

    def test_config_cannot_set_the_root_or_smuggle_other_keys(self, root, tmp_path, monkeypatch):
        # admins can PATCH provider config through the API: it must not choose which directory is read
        monkeypatch.setenv("RELAYIQ_SHARED_BREAKER", "0")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "other.json").write_text(json.dumps(SNAP))
        base = {"snapshot_file": "other.json", "as_of": "2026-11-20"}
        rows = [
            _cfg("p1", "via_root", "snapshot.sec", dict(base, snapshot_root=str(elsewhere))),
            _cfg("p2", "via_facts", "snapshot.sec", dict(base, identity_facts={"Acme": {}})),
        ]
        for env_root in (None, root):  # feature off, and feature on with a different root
            if env_root is None:
                monkeypatch.delenv(SA.SNAPSHOT_ROOT_ENV, raising=False)
            else:
                monkeypatch.setenv(SA.SNAPSHOT_ROOT_ENV, env_root)
            reg = registry_mod.ProviderRegistry()
            with capture_logs() as logs:
                reg.load(_Session(rows, []))
            assert reg.all() == {}
            failed = {e["provider"]: e for e in logs if e.get("event") == "provider_load_failed"}
            assert set(failed) == {"via_root", "via_facts"}
            assert all(e["error"] == "SnapshotConfigError" for e in failed.values())
            assert "elsewhere" not in json.dumps(failed)

    def test_extra_section_names_are_not_logged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RELAYIQ_SHARED_BREAKER", "0")
        monkeypatch.setenv(SA.SNAPSHOT_ROOT_ENV, str(tmp_path))
        (tmp_path / "s.json").write_text(json.dumps(dict(SNAP, private_section_name={})))
        reg = registry_mod.ProviderRegistry()
        with capture_logs() as logs:
            reg.load(
                _Session(
                    [_cfg("p1", "r", "snapshot.sec", {"snapshot_file": "s.json", "as_of": "2026-11-20"})], []
                )
            )
        assert reg.all() == {}
        assert "private_section_name" not in json.dumps(logs)

    def test_config_cannot_widen_capabilities_change_costs_or_rename_the_key(self, root, monkeypatch):
        monkeypatch.setenv(SA.SNAPSHOT_ROOT_ENV, root)
        monkeypatch.setenv("RELAYIQ_SHARED_BREAKER", "0")
        base = {"snapshot_file": "snap.json", "as_of": "2026-11-20"}
        rows = [
            _cfg(
                "p1", "widen", "snapshot.sec", dict(base, capabilities={"account": ["hq_city", "hq_state"]})
            ),
            _cfg("p2", "rename", "snapshot.sec", dict(base, provider_key="alpha")),
            _cfg("p3", "costs", "snapshot.sec", dict(base, field_costs={"hq_city": 5.0})),
            _cfg("p4", "clean", "snapshot.sec", dict(base)),
        ]
        caps = [
            ProviderCapability(provider_id=pid, entity_type="account", field_name="hq_city", cost_credits=1)
            for pid in ("p1", "p2", "p3", "p4")
        ]
        reg = registry_mod.ProviderRegistry()
        with capture_logs() as logs:
            reg.load(_Session(rows, caps))
        assert sorted(reg.all()) == ["clean"]
        clean = reg.get("clean")
        assert clean is not None and clean.key == "clean"
        assert clean.capabilities() == {"account": {"hq_city"}}
        failed = {e["provider"]: e["error"] for e in logs if e.get("event") == "provider_load_failed"}
        assert failed == {
            "widen": "SnapshotConfigError",
            "rename": "SnapshotConfigError",
            "costs": "SnapshotConfigError",
        }

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


# ---------------------------------------------------------------- output safety
class TestOutputSafety:
    def test_root_domain_is_not_offered(self, root):
        # the account's domain is an input here, and the source value is a raw website URL
        assert make("sec", root).capabilities() == {"account": {"hq_city", "hq_state"}}
        assert make("wikidata", root).capabilities() == {"account": {"hq_city", "hq_state", "founded_year"}}
        r = make("sec", root).enrich("account", ACME, ["root_domain"])
        assert r.error == "no supported fields requested"

    def test_wikidata_needs_the_exact_host_not_a_shared_suffix(self, tmp_path):
        snap = {
            "wikidata": {
                "Acme": {
                    "candidates": [],
                    "chosen": None,
                    "details": [
                        {
                            "id": "Q7",
                            "label": "Acme",
                            "desc": "software company",
                            "website": "https://unrelated-site.hosting.example",
                            "hq_city": "Wrongville",
                            "hq_state": "TX",
                        }
                    ],
                }
            }
        }
        (tmp_path / "w.json").write_text(json.dumps(snap))
        a = SA.SnapshotReplayAdapter(
            "wikidata", snapshot_file="w.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        r = a.enrich("account", {"company_name": "Acme", "root_domain": "acme.hosting.example"}, ["hq_city"])
        assert r.outcome == ProviderOutcome.PERM_FAIL and r.fields == {}
        ok = a.enrich(
            "account",
            {"company_name": "Acme", "root_domain": "www.unrelated-site.hosting.example"},
            ["hq_city"],
        )
        assert ok.fields["hq_city"].value == "Wrongville"  # exact host (www. ignored) still binds

    def test_values_that_do_not_fit_are_dropped_not_truncated(self, tmp_path):
        chosen = dict(SNAP["sec"]["Acme"]["chosen"], hq_city="x" * 121, hq_state="C\x00A")
        (tmp_path / "v.json").write_text(json.dumps({"sec": {"Acme": {"chosen": chosen}}}))
        a = SA.SnapshotReplayAdapter(
            "sec", snapshot_file="v.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        r = a.enrich("account", ACME, ["hq_city", "hq_state"])
        assert r.outcome == ProviderOutcome.PERM_FAIL and r.fields == {} and r.error == "no usable value"
        chosen["hq_state"] = "CA"
        (tmp_path / "v.json").write_text(json.dumps({"sec": {"Acme": {"chosen": chosen}}}) + " ")
        a = SA.SnapshotReplayAdapter(
            "sec", snapshot_file="v.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
        )
        r = a.enrich("account", ACME, ["hq_city", "hq_state"])
        assert r.ok and set(r.fields) == {"hq_state"}

    def test_value_length_boundary_and_control_characters(self, tmp_path):
        def run(city):
            chosen = dict(SNAP["sec"]["Acme"]["chosen"], hq_city=city)
            f = tmp_path / f"b{len(city)}{ord(city[-1])}.json"
            f.write_text(json.dumps({"sec": {"Acme": {"chosen": chosen}}}))
            a = SA.SnapshotReplayAdapter(
                "sec", snapshot_file=f.name, as_of="2026-11-20", snapshot_root=str(tmp_path)
            )
            return a.enrich("account", ACME, ["hq_city"])

        assert run("y" * 120).fields["hq_city"].value == "y" * 120
        for bad in ("Some\nwhere", "Some\twhere", "Some\x7fwhere"):
            assert run(bad).error == "no usable value", repr(bad)

    def test_absent_company_is_no_matching_record(self, root):
        for source in ("sec", "gleif", "wikidata"):
            r = make(source, root).enrich(
                "account", {"company_name": "Nobody", "root_domain": "nobody.example"}, ["hq_city"]
            )
            assert r.error == "no matching record", source

    def test_never_raises_on_odd_inputs(self, root):
        a = make("sec", root)
        assert a.enrich("account", ACME, None).error == "no supported fields requested"
        r = a.enrich("account", {"company_name": 123, "root_domain": ["x"]}, ["hq_city"])
        assert r.outcome == ProviderOutcome.PERM_FAIL
        for ids, fields in ((None, ["hq_city"]), (ACME, 5), (ACME, [["hq_city"]])):
            r = a.enrich("account", ids, fields)
            assert r.outcome == ProviderOutcome.PERM_FAIL and r.error.startswith("snapshot replay failed")

    def test_raw_payload_carries_only_state_reason_basis(self, root):
        r = make("sec", root).enrich("account", ACME, ["hq_city"])
        assert set(r.raw_payload) == {"identity_binding"}
        assert set(r.raw_payload["identity_binding"]) <= {"state", "reason", "basis"}

    def test_non_object_snapshot_is_refused(self, tmp_path):
        (tmp_path / "l.json").write_text("[1, 2]")
        with pytest.raises(SA.SnapshotConfigError, match="must be a JSON object"):
            SA.SnapshotReplayAdapter(
                "sec", snapshot_file="l.json", as_of="2026-11-20", snapshot_root=str(tmp_path)
            )

    def test_offline_provider_needs_exactly_one_source(self, root):
        from relayiq.providers.public_sources import SnapshotProvider

        with pytest.raises(ValueError, match="exactly one"):
            SnapshotProvider("sec", "sec", None, "2026-11-20")
        with pytest.raises(ValueError, match="exactly one"):
            SnapshotProvider("sec", "sec", os.path.join(root, "snap.json"), "2026-11-20", records={})


# ---------------------------------------------------------------- stored error text (provider_exec call site)
class TestStoredErrorIsBounded:
    def test_long_provider_error_is_bounded_where_the_request_row_is_built(self, monkeypatch):
        from relayiq.enums import ProviderOutcome as PO
        from relayiq.models import ProviderRequest
        from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter

        class LongError(ProviderAdapter):
            key = "long"

            def capabilities(self):
                return {"account": {"hq_city"}}

            def field_cost(self, entity_type, field_name):
                return 0.0

            def enrich(self, entity_type, identifiers, fields, *, timeout_ms=8000):
                return EnrichmentCallResult("long", entity_type, PO.PERM_FAIL, error="e" * 500)

        class Breaker:
            def allow(self):
                return True

            def record_success(self):
                pass

            def record_failure(self):
                pass

        class Reg:
            def breaker(self, key):
                return Breaker()

        class Sess:
            def __init__(self):
                self.added = []

            def add(self, obj):
                self.added.append(obj)

            def flush(self):
                pass

        monkeypatch.setattr(provider_exec, "_record_health", lambda *a, **k: None)
        s = Sess()
        result, req = provider_exec.execute_with_retries(
            s,
            Reg(),
            LongError(),
            tenant_id="t",
            job_id=None,
            entity_type="account",
            entity_id="e",
            identifiers={},
            fields=["hq_city"],
        )
        assert isinstance(req, ProviderRequest) and req in s.added
        assert len(req.error_type) == provider_exec.ERROR_TYPE_MAX
