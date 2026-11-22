"""Entity checks used by the public-source replay adapters.

Inline synthetic fixtures only (*.example hosts)."""

import json

from relayiq.providers.entity_match import gnorm, name_matches, org_like
from relayiq.providers.public_sources import SnapshotProvider


def test_name_matches_ignores_legal_and_generic_words() -> None:
    assert name_matches("Acme", "Acme Labs, Inc.")
    assert name_matches("Globex", "GLOBEX INC.")
    assert not name_matches("Initech", "EJEMPLO CONSTRUCCIONES Y OBRAS S.L.")
    assert not name_matches("Acme", "Acme Office Supplies")
    assert not name_matches("", "")


def test_org_like_descriptions() -> None:
    assert org_like("American public developer of analytics software")
    assert org_like("software business, developer of a search engine")
    assert not org_like("2003 video game")
    assert not org_like("village in an example county")
    assert not org_like("jazz album by an example artist")
    assert not org_like(None)


def _snap(tmp_path, rec):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"wikidata": {"Acme": rec}, "sec": {}, "gleif": {}}))
    return str(p)


def test_entity_check_skips_game_and_takes_company(tmp_path) -> None:
    rec = {
        "candidates": [],
        "chosen": {"id": "Q1", "label": "Acme", "website": "https://www.acme-game.example"},
        "details": [
            {
                "id": "Q1",
                "label": "Acme",
                "desc": "2016 video game remake",
                "website": "https://acme-game.example",
            },
            {
                "id": "Q2",
                "label": "Acme",
                "desc": "American public developer of analytics software",
                "website": "https://acme.example",
                "hq_city": "San Francisco",
                "hq_state": "CA",
            },
        ],
    }
    naive = SnapshotProvider("wikidata", "wikidata", _snap(tmp_path, rec), "2026-11-09")
    fixed = SnapshotProvider("wikidata", "wikidata", _snap(tmp_path, rec), "2026-11-09", entity_check=True)
    assert (
        naive.enrich("account", {"company_name": "Acme"}, ["root_domain"]).fields["root_domain"].value
        == "https://www.acme-game.example"
    )
    assert (
        fixed.enrich("account", {"company_name": "Acme"}, ["root_domain"]).fields["root_domain"].value
        == "https://acme.example"
    )


def test_entity_check_gives_no_answer_without_description(tmp_path) -> None:
    rec = {
        "candidates": [],
        "chosen": None,
        "details": [{"id": "Q9", "label": "Acme", "desc": None, "website": "https://x.example"}],
    }
    fixed = SnapshotProvider("wikidata", "wikidata", _snap(tmp_path, rec), "2026-11-09", entity_check=True)
    assert fixed.enrich("account", {"company_name": "Acme"}, ["root_domain"]).fields == {}


def test_gnorm_keeps_distinct_words() -> None:
    assert gnorm("Acme Video Communications") == "acme video"


def test_known_domain_variant_ties_record_to_input_domain(tmp_path) -> None:
    rec = {
        "candidates": [],
        "chosen": None,
        "details": [
            {"id": "Q1", "label": "Globex", "desc": "Roman god", "website": "https://globexrecords.example"},
            {
                "id": "Q2",
                "label": "Globex Technologies",
                "desc": "American fintech company",
                "website": "https://globex.example/",
                "hq_city": "San Francisco",
                "hq_state": "CA",
                "founded_year": 2017,
            },
        ],
    }
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"wikidata": {"Globex": rec}, "sec": {}, "gleif": {}}))
    d = SnapshotProvider(
        "wikidata", "wikidata", str(p), "2026-11-09", input_domains={"Globex": "www.globex.example"}
    )
    out = d.enrich("account", {"company_name": "Globex"}, ["hq_city", "founded_year"])
    assert out.fields["hq_city"].value == "San Francisco"
    wrong = SnapshotProvider(
        "wikidata", "wikidata", str(p), "2026-11-09", input_domains={"Globex": "other.example"}
    )
    assert wrong.enrich("account", {"company_name": "Globex"}, ["hq_city"]).fields == {}
    missing = SnapshotProvider("wikidata", "wikidata", str(p), "2026-11-09", input_domains={})
    assert missing.enrich("account", {"company_name": "Globex"}, ["hq_city"]).fields == {}


def test_known_domain_variant_does_not_use_gleif(tmp_path) -> None:
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            {
                "wikidata": {},
                "sec": {},
                "gleif": {
                    "X": {
                        "candidates": [
                            {"lei": "L", "name": "X Inc", "status": "ACTIVE", "hq": {"city": "Paris"}}
                        ],
                        "chosen": None,
                    }
                },
            }
        )
    )
    g = SnapshotProvider("gleif", "gleif", str(p), "2026-11-09", input_domains={"X": "x.example"})
    assert g.enrich("account", {"company_name": "X"}, ["hq_city"]).fields == {}
