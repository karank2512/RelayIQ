"""Registry identity binding (SEC/GLEIF) with the identity-facts sidecar: offline unit tests.

All inputs are INLINE SYNTHETIC fixtures (Acme/Globex-style names; hosts under the reserved .example and
example.com names, except the shared-suffix test, which uses fictional labels under real public suffixes such
as co.za, com.cn and github.io as plain strings that are never contacted). They are test fixtures only, never
truth, and nothing here says anything about any real company, registry record or HQ.
"verified" in a facts file means ASSERTED verified by its author; provenance is recorded, never treated as
proof. Network: socket.socket, socket.create_connection and urllib.request.urlopen are patched to raise in
every test.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relayiq.providers import identity_facts as IF
from relayiq.providers import public_sources as PS
from relayiq.providers.public_sources import SnapshotProvider

AS_OF = "2026-11-21"
V = ("verified", "2026-11-20", "registry-extract-fixture-001")
C = ("crm", "2026-11-20", "")
NOT_COLLECTED = "needs review: jurisdiction fact supplied; GLEIF jurisdiction not collected"


def _no_net(*a, **k):
    raise AssertionError("network attempted in an offline test")


def csv_rows(rows):
    """rows: {company: {field: (value, source, as_of, ref)}} -> facts CSV text with the exact header."""
    lines = [",".join(IF.FACT_COLUMNS)]
    for company, fields in rows.items():
        cells = {"company": company}
        for f, (v, s, a, r) in fields.items():
            cells.update({f: v, f"{f}_source": s, f"{f}_as_of": a, f"{f}_ref": r})
        lines.append(",".join('"' + str(cells.get(c, "")).replace('"', '""') + '"' for c in IF.FACT_COLUMNS))
    return "\n".join(lines) + "\n"


def csv1(company, **fields):
    return csv_rows({company: fields})


def sec_rec(name, state, website, city="Somewhere", st="CA"):
    return {
        "chosen": {
            "cik": "0000000001",
            "name": name,
            "hq_city": city,
            "hq_state": st,
            "website": website,
            "state_of_incorporation": state,
            "latest_filing_date": "2026-01-01",
        }
    }


def cand(
    lei, name, country, status="ACTIVE", city="Wilmington", region="US-DE", lines=None, jurisdiction=None
):
    c = {
        "name": name,
        "status": status,
        "last_update": "2026-01-01",
        "hq": {
            "city": city,
            "region": region,
            "country": country,
            "addressLines": lines or ["1 Example Way"],
        },
    }
    if lei:
        c["lei"] = lei
    if jurisdiction:
        c["jurisdiction"] = jurisdiction
    return c


def listed_facts(prov):
    """Fact keys named in the emitted "asserted verified (provenance: ...)" segment, in order ([] if no
    segment)."""
    head = "asserted verified (provenance: "
    seg = prov.split(head, 1)[1] if head in prov else ""
    return re.findall(r"(?:^|; )([a-z_]+) source=", seg)


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("socket.socket", "socket.create_connection", "urllib.request.urlopen"):
            p = mock.patch(t, side_effect=_no_net)
            p.start()
            self.addCleanup(p.stop)

    def snap(self, sec=None, gleif=None):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, f.name)
        json.dump({"sec": sec or {}, "gleif": gleif or {}, "wikidata": {}}, f)
        f.close()
        return f.name

    def facts(self, company, **fields):
        return IF.parse_identity_facts(csv1(company, **fields), [company])[0]

    def run1(self, key, name, snap, facts=None, domains=None, field="hq_city"):
        kw = {"input_domains": domains}
        if facts is not None:
            kw["identity_facts"] = facts
        p = SnapshotProvider(key, key, snap, AS_OF, **kw)
        r = p.enrich("account", {"company_name": name}, [field])
        val = r.fields[field].value if r.ok and field in r.fields else None
        return val, p.binding_log.get(name, {}), r


# ---------------------------------------------------------------- website facts never override the input
# domain
class WebsiteFacts(Base):
    def test_fact_website_never_overrides_input_domain(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://acme-holdings.example/")})
        val, b, _ = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", website=("acme-holdings.example",) + V),
            {"Acme": "acme.example"},
        )
        self.assertIsNone(val)
        self.assertIn("CRM input domain acme.example", b["reason"])

    def test_unparseable_fact_website_refused_by_loader(self):
        for bad in ("n/a", "none", "-", "not a url"):
            with self.assertRaises(IF.FactsError, msg=bad):
                self.facts("Acme", website=(bad,) + V)

    def test_honest_label_names_the_compared_domain(self):
        sp = self.snap(sec={"Acme": sec_rec("Acme Corp", "DE", "https://www.acme.example/")})
        val, b, _ = self.run1("sec", "Acme", sp, self.facts("Acme", website=("acme.example",) + V))
        self.assertEqual(val, "Somewhere")
        self.assertEqual(b["basis"], "registrant website host equals asserted-verified fact website")

    def test_both_targets_must_match(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://acme.example/")})
        val, b, _ = self.run1(
            "sec", "Acme", sp, self.facts("Acme", website=("acme.example",) + V), {"Acme": "acme.example"}
        )
        self.assertEqual(val, "Somewhere")
        self.assertEqual(
            b["basis"], "registrant website host equals CRM input domain and asserted-verified fact website"
        )

    def test_sec_website_corroboration_binds_and_conflict_abstains(self):
        rec = sec_rec("Example Corp", "DE", "https://www.example.com/", city="X")
        sp = self.snap(sec={"Example": rec})
        self.assertEqual(self.run1("sec", "Example", sp, domains={"Example": "example.com"})[0], "X")
        self.assertIsNone(self.run1("sec", "Example", sp, domains={"Example": "other.example"})[0])


# ---------------------------------------------------------------- exact host only, no userinfo
class WebsiteRoot(Base):
    W = {
        "co.za": ("acme.co.za", "https://www.globex.co.za/"),
        "com.cn": ("acme.com.cn", "http://globex.com.cn"),
        "co.il": ("acme.co.il", "https://globex.co.il"),
        "github.io": ("acme.github.io", "https://globex.github.io/"),
        "herokuapp": ("acme.herokuapp.com", "https://globex.herokuapp.com"),
        "myshopify": ("acme.myshopify.com", "https://globex.myshopify.com"),
        "azurewebsites": ("acme.azurewebsites.net", "https://globex.azurewebsites.net"),
        "vercel.app": ("acme.vercel.app", "https://globex.vercel.app"),
        "blogspot": ("acme.blogspot.com", "https://globex.blogspot.com"),
    }

    def test_shared_suffix_roots_never_bind(self):
        for label, (inp, site) in self.W.items():
            sp = self.snap(sec={"Acme": sec_rec("GLOBEX CORP", "DE", site)})
            val, b, _ = self.run1("sec", "Acme", sp, domains={"Acme": inp})
            self.assertIsNone(val, label)
            self.assertIn("unverifiable", b.get("reason", ""), label)

    def test_credentials_in_url_refused_or_abstain(self):
        for site in (
            "https://evil.example\\@acme.example/",
            "https://user:pass@acme.example/",
            "https://evil.example@acme.example/",
        ):
            sp = self.snap(sec={"Acme": sec_rec("GLOBEX CORP", "DE", site)})
            val, b, _ = self.run1("sec", "Acme", sp, domains={"Acme": "acme.example"})
            self.assertIsNone(val, site)
            self.assertIn("userinfo", b.get("reason", ""), site)
            with self.assertRaises(IF.FactsError, msg=site):
                self.facts("Acme", website=(site,) + V)

    def test_subdomain_of_same_root_is_unverifiable(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://investors.acme.example/")})
        val, b, _ = self.run1("sec", "Acme", sp, domains={"Acme": "acme.example"})
        self.assertIsNone(val)
        self.assertIn("website root unverifiable", b["reason"])

    def test_exact_host_still_binds(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://www.acme.example/")})
        val, _, _ = self.run1("sec", "Acme", sp, domains={"Acme": "acme.example"})
        self.assertEqual(val, "Somewhere")


# ---------------------------------------------------------------- provenance (loader + provider)
class Provenance(Base):
    def test_synthetic_ref_refused_without_flag_and_marked_with_flag(self):
        txt = csv1(
            "Acme",
            legal_name=("ACME, INC.", "verified", "2026-11-20", IF.SYNTHETIC_REF),
            hq_country=("US", "verified", "2026-11-20", IF.SYNTHETIC_REF),
        )
        with self.assertRaises(IF.FactsError):
            IF.parse_identity_facts(txt, ["Acme"])
        facts, rep = IF.parse_identity_facts(txt, ["Acme"], allow_synthetic=True)
        self.assertTrue(facts["Acme"]["synthetic"] and rep["synthetic"])
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        _, b, r = self.run1("gleif", "Acme", sp, facts)
        self.assertTrue(b["synthetic"])
        self.assertIn("SYNTHETIC", r.fields["hq_city"].provenance)

    def test_synthetic_marker_is_emitted_independently_of_ref_text(self):
        txt = csv1("Acme", legal_name=("ACME, INC.",) + V, hq_country=("US",) + V)
        facts, _ = IF.parse_identity_facts(txt, ["Acme"], allow_synthetic=True)
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        _, _, r = self.run1("gleif", "Acme", sp, facts)
        self.assertNotIn("SYNTHETIC", V[2])
        self.assertTrue(r.fields["hq_city"].provenance.endswith(" | SYNTHETIC facts, not truth"))
        plain, _ = IF.parse_identity_facts(txt, ["Acme"])
        _, _, r2 = self.run1("gleif", "Acme", sp, plain)
        self.assertNotIn("SYNTHETIC", r2.fields["hq_city"].provenance)

    def test_loader_rejects_bad_provenance_values(self):
        cases = {
            "capitalised source": dict(legal_name=("Acme Inc.", "Verified", "2026-11-20", "registry-ref-1")),
            "future as_of": dict(legal_name=("Acme Inc.", "verified", "2099-01-01", "registry-ref-1")),
            "placeholder ref": dict(legal_name=("Acme Inc.", "verified", "2026-11-20", "x")),
            "free-text country": dict(
                hq_country=("United States", "verified", "2026-11-20", "registry-ref-1")
            ),
        }
        for label, fields in cases.items():
            with self.assertRaises(IF.FactsError, msg=label):
                IF.parse_identity_facts(csv1("Acme", **fields), ["Acme"])

    def test_values_without_provenance_are_ignored(self):
        txt = csv_rows(
            {
                "Acme": {
                    "legal_name": ("ACME, INC.", "verified", "", "registry-ref-1"),
                    "hq_country": ("US", "verified", "2026-11-20", ""),
                },
                "Globex": {"legal_name": ("GLOBEX LIMITED", "", "2026-11-20", "registry-ref-1")},
            }
        )
        facts, rep = IF.parse_identity_facts(txt, ["Acme", "Globex"])
        self.assertEqual(facts, {})
        self.assertEqual(
            sorted(rep["ignored"]),
            [
                "Acme:hq_country (no provenance)",
                "Acme:legal_name (no provenance)",
                "Globex:legal_name (no provenance)",
            ],
        )
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        self.assertIsNone(self.run1("gleif", "Acme", sp, facts)[0])

    def test_crm_facts_abstain_needs_review(self):
        facts, rep = IF.parse_identity_facts(
            csv1("Acme", legal_name=("ACME, INC.",) + C, hq_country=("US",) + C), ["Acme"]
        )
        self.assertEqual((rep["verified_fields"], rep["crm_fields"]), (0, 2))
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        val, b, _ = self.run1("gleif", "Acme", sp, facts)
        self.assertIsNone(val)
        self.assertTrue(b["reason"].startswith("needs review: only unverified crm facts"))

    def test_provider_refuses_facts_without_provenance(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        with self.assertRaises(ValueError):
            SnapshotProvider(
                "gleif",
                "gleif",
                sp,
                AS_OF,
                identity_facts={"Acme": {"legal_name": "Acme, Inc.", "country": "US"}},
            )

    def test_provenance_carried_to_log_and_value(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        val, b, r = self.run1(
            "gleif", "Acme", sp, self.facts("Acme", legal_name=("ACME, INC.",) + V, hq_country=("US",) + V)
        )
        self.assertEqual(val, "Wilmington")
        self.assertEqual(
            b["facts_provenance"]["legal_name"],
            {"source": "verified", "as_of": "2026-11-20", "ref": "registry-extract-fixture-001"},
        )
        self.assertIn("asserted verified (provenance:", r.fields["hq_city"].provenance)
        self.assertIn("ref=registry-extract-fixture-001", r.fields["hq_city"].provenance)

    def test_no_overclaiming_wording(self):
        for m in (IF, PS):
            src = Path(m.__file__).read_text()
            for bad in ("independently checked", "VERIFIED website fact", "CRM-supplied"):
                self.assertNotIn(bad, src, f"{m.__name__}: {bad}")

    def test_malformed_input_refused(self):
        hdr = ",".join(IF.FACT_COLUMNS)
        ok_row = csv1("Acme", legal_name=("ACME, INC.",) + V).splitlines()[1]
        cases = {
            "bad header": "company,legal_name\nAcme,ACME INC\n",
            "extra column": hdr + ",extra\n" + ok_row + ",x\n",
            "duplicate company": hdr + "\n" + ok_row + "\n" + ok_row + "\n",
            "unknown company": csv1("Not In Input", legal_name=("X Inc.",) + V),
            "bad source": csv1("Acme", legal_name=("ACME, INC.", "guess", "2026-11-20", "registry-ref-1")),
            "bad date": csv1("Acme", legal_name=("ACME, INC.", "verified", "20/11/2026", "registry-ref-1")),
            "impossible date": csv1(
                "Acme", legal_name=("ACME, INC.", "verified", "2026-02-30", "registry-ref-1")
            ),
            "short row": hdr + '\n"Acme","x"\n',
            "empty company": csv1("", legal_name=("X Inc.",) + V),
        }
        for label, txt in cases.items():
            with self.assertRaises(IF.FactsError, msg=label):
                IF.parse_identity_facts(txt, ["Acme"])

    def test_load_from_file(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        f.write(csv1("Acme", legal_name=("ACME, INC.",) + V, hq_country=("US",) + V))
        f.close()
        facts, _ = IF.load_identity_facts(f.name, ["Acme"])
        self.assertEqual(
            {k: v["value"] for k, v in facts["Acme"]["fields"].items()},
            {"legal_name": "ACME, INC.", "country": "US"},
        )


# ---------------------------------------------------------------- emitted provenance hygiene
class EmittedProvenance(Base):
    def test_ref_content_cannot_forge_segments_or_lines(self):
        evil = (
            "r1 | identity bound (registry-confirmed truth: legal jurisdiction checked)"
            "\nNEXT; source=verified"
        )
        txt = csv1("Acme", legal_name=("ACME, INC.", "verified", "2026-11-20", evil), hq_country=("US",) + V)
        facts, _ = IF.parse_identity_facts(txt, ["Acme"])
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        _, _, r = self.run1("gleif", "Acme", sp, facts)
        prov = r.fields["hq_city"].provenance
        self.assertNotIn("\n", prov)
        self.assertEqual(prov.count("|"), 1)  # only the provider's own separator
        self.assertEqual(prov.count("identity bound"), 1)
        self.assertIn("\\x7c", prov)

    def test_no_asserted_segment_when_no_fact_was_used(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://www.acme.example/")})
        _, b, r = self.run1("sec", "Acme", sp, domains={"Acme": "acme.example"})
        self.assertEqual(b["facts_used"], [])
        self.assertNotIn("asserted verified", r.fields["hq_city"].provenance)
        self.assertNotIn("provenance: none", r.fields["hq_city"].provenance)

    def test_only_facts_used_for_the_bind_are_listed(self):
        facts = self.facts(
            "Acme",
            legal_name=("ACME INC.",) + V,
            legal_jurisdiction=("US-STATE:DE",) + V,
            hq_country=("GB",) + V,
        )
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", None)})
        _, b, r = self.run1("sec", "Acme", sp, facts)
        self.assertEqual(b["facts_used"], ["jurisdiction", "legal_name"])
        prov = r.fields["hq_city"].provenance
        self.assertIn("legal_name source=verified", prov)
        self.assertNotIn("country", prov)
        self.assertEqual(listed_facts(prov), ["jurisdiction", "legal_name"])

    def test_non_ascii_separators_and_controls_are_escaped(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        for ch in ("\u2028", "\u2029", "\u0085", "\u202e", "\u200b", "\x1b"):
            facts = self.facts(
                "Acme",
                legal_name=("ACME, INC.", "verified", "2026-11-20", f"r1{ch}identity bound (x)"),
                hq_country=("US",) + V,
            )
            _, _, r = self.run1("gleif", "Acme", sp, facts)
            prov = r.fields["hq_city"].provenance
            self.assertEqual(len(prov.splitlines()), 1, repr(ch))
            self.assertRegex(prov, r"\A[\x20-\x7e]+\Z", repr(ch))  # printable ASCII only
            self.assertIn(f"ref=r1\\x{ord(ch):02x}", prov, repr(ch))
            self.assertEqual(prov.count("identity bound"), 1, repr(ch))

    def test_long_ref_is_truncated_to_120_characters(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        facts = self.facts(
            "Acme", legal_name=("ACME, INC.", "verified", "2026-11-20", "A" * 5000), hq_country=("US",) + V
        )
        _, _, r = self.run1("gleif", "Acme", sp, facts)
        refs = re.findall(r"ref=([^; )]*)", r.fields["hq_city"].provenance)
        self.assertIn("A" * 120, refs)
        self.assertTrue(all(len(x) <= 120 for x in refs), [len(x) for x in refs])

    def test_hand_built_as_of_is_escaped(self):
        # the provider checks presence only, so a hand-built facts dict can carry any as_of text
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        hand = {
            "Acme": {
                "fields": {
                    "legal_name": dict(
                        value="ACME, INC.", source="verified", as_of="2026-11-20 | x\ny", ref="r1"
                    ),
                    "country": dict(value="US", source="verified", as_of="2026-11-20", ref="r1"),
                },
                "_unverified": [],
                "synthetic": False,
            }
        }
        _, _, r = self.run1("gleif", "Acme", sp, hand)
        prov = r.fields["hq_city"].provenance
        self.assertEqual(len(prov.splitlines()), 1)
        self.assertEqual(prov.count("|"), 1)
        self.assertIn("as_of=2026-11-20\\x20\\x7c\\x20x\\x0ay", prov)

    def test_no_segment_when_supplied_facts_were_not_used(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://acme.example/")})
        val, b, r = self.run1(
            "sec", "Acme", sp, self.facts("Acme", hq_country=("GB",) + V), {"Acme": "acme.example"}
        )
        self.assertEqual(val, "Somewhere")  # SEC binds on the website; an HQ country fact is not used by SEC
        self.assertEqual(b["facts_used"], [])
        self.assertNotIn("asserted verified", r.fields["hq_city"].provenance)
        self.assertNotIn("provenance:", r.fields["hq_city"].provenance)

    def test_unchecked_jurisdiction_is_not_listed_on_the_website_path(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", None, "https://acme.example/")})
        val, b, r = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", legal_jurisdiction=("US-STATE:NV",) + V),
            {"Acme": "acme.example"},
        )
        self.assertEqual(val, "Somewhere")  # no registrant jurisdiction to check against: website bind only
        self.assertEqual(b["facts_used"], [])
        self.assertNotIn("jurisdiction", r.fields["hq_city"].provenance)


# ---------------------------------------------------------------- jurisdiction, HQ
class Jurisdiction(Base):
    def test_bare_or_free_text_jurisdiction_refused(self):
        for bad in ("DE", "CA", "Delaware", "US-DE", "ISO:XX", "US-STATE:ZZ"):
            with self.assertRaises(IF.FactsError, msg=bad):
                self.facts("Acme", legal_jurisdiction=(bad,) + V)

    def test_country_codes_never_equal_us_states(self):
        for reg_name, state, legal, iso in (
            ("ACME INC.", "CA", "Acme Inc.", "ISO:CA"),
            ("ACME HOLDINGS LTD", "KY", "Acme Holdings Ltd", "ISO:KY"),
            ("GLOBEX LTD", "IL", "Globex Ltd", "ISO:IL"),
        ):
            sp = self.snap(sec={"Acme": sec_rec(reg_name, state, None)})
            val, _, _ = self.run1(
                "sec", "Acme", sp, self.facts("Acme", legal_name=(legal,) + V, legal_jurisdiction=(iso,) + V)
            )
            self.assertIsNone(val, iso)

    def test_typed_us_state_binds(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", None)})
        val, b, _ = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", legal_name=("Acme Inc.",) + V, legal_jurisdiction=("US-STATE:DE",) + V),
        )
        self.assertEqual(val, "Somewhere")
        self.assertEqual(b["basis"], "exact legal name + jurisdiction US-STATE:DE")

    def test_unmapped_edgar_code_never_binds(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME HOLDINGS INC.", "A1", None)})
        for j in ("ISO:CA", "US-STATE:DE"):
            val, b, _ = self.run1(
                "sec",
                "Acme",
                sp,
                self.facts("Acme", legal_name=("ACME HOLDINGS INC.",) + V, legal_jurisdiction=(j,) + V),
            )
            self.assertIsNone(val, j)
            self.assertIn("EDGAR:A1", b["reason"])

    def test_gleif_abstains_when_jurisdiction_fact_cannot_be_checked(self):
        sp = self.snap(
            gleif={
                "Acme": {
                    "candidates": [
                        cand("LEI-1", "ACME HOLDINGS LIMITED", "US", city="New York", region="US-NY")
                    ],
                    "chosen": None,
                }
            }
        )
        for j in ("ISO:IE", "ISO:KY", "ISO:JP", "ISO:NL", "US-STATE:DE"):
            val, b, _ = self.run1(
                "gleif",
                "Acme",
                sp,
                self.facts(
                    "Acme",
                    legal_name=("Acme Holdings Limited",) + V,
                    hq_country=("US",) + V,
                    legal_jurisdiction=(j,) + V,
                ),
            )
            self.assertIsNone(val, j)
            self.assertEqual(b["reason"], NOT_COLLECTED, j)
        val, b, _ = self.run1(
            "gleif",
            "Acme",
            sp,
            self.facts("Acme", legal_name=("Acme Holdings Limited",) + V, hq_country=("US",) + V),
        )
        self.assertEqual(
            val, "New York"
        )  # without a jurisdiction fact the exact-name + HQ-country bind stays allowed

    def test_gleif_same_name_twin_is_not_bound_on_an_unchecked_jurisdiction(self):
        cands = [
            cand("LEI-X1", "ACME GROUP PLC", "GB", region="GB-LND"),
            cand("LEI-X2", "ACME B.V.", "NL", region="NL-NH"),
            cand("LEI-CA", "ACME, INC.", "US", city="San Jose", region="US-CA"),
        ]
        sp = self.snap(gleif={"Acme": {"candidates": cands, "chosen": None}})
        val, b, _ = self.run1(
            "gleif",
            "Acme",
            sp,
            self.facts(
                "Acme",
                legal_name=("Acme, Inc.",) + V,
                hq_country=("US",) + V,
                legal_jurisdiction=("US-STATE:DE",) + V,
            ),
        )
        self.assertIsNone(val)
        self.assertEqual(b["reason"], NOT_COLLECTED)

    def test_gleif_record_jurisdiction_is_compared_when_present(self):
        sp = self.snap(
            gleif={
                "Acme": {
                    "candidates": [cand("LEI-1", "ACME, INC.", "US", jurisdiction="US-DE")],
                    "chosen": None,
                }
            }
        )
        ok = self.facts(
            "Acme",
            legal_name=("Acme, Inc.",) + V,
            hq_country=("US",) + V,
            legal_jurisdiction=("US-STATE:DE",) + V,
        )
        bad = self.facts(
            "Acme", legal_name=("Acme, Inc.",) + V, hq_country=("US",) + V, legal_jurisdiction=("ISO:IE",) + V
        )
        self.assertEqual(self.run1("gleif", "Acme", sp, ok)[0], "Wilmington")
        val, b, _ = self.run1("gleif", "Acme", sp, bad)
        self.assertIsNone(val)
        self.assertIn("contradicts GLEIF jurisdiction US-STATE:DE", b["reason"])

    def test_gleif_record_jurisdiction_keeps_its_namespace(self):
        # a bare two-letter GLEIF jurisdiction is a country (DE = Germany), never a US state; "US-DE" is
        # Delaware
        for rec_j, fact_j, typed in (
            ("DE", "US-STATE:DE", "ISO:DE"),
            ("US-DE", "ISO:DE", "US-STATE:DE"),
            ("KY", "US-STATE:KY", "ISO:KY"),
            ("CA", "US-STATE:CA", "ISO:CA"),
        ):
            sp = self.snap(
                gleif={
                    "Acme": {
                        "candidates": [cand("LEI-1", "ACME, INC.", "US", jurisdiction=rec_j)],
                        "chosen": None,
                    }
                }
            )
            val, b, _ = self.run1(
                "gleif",
                "Acme",
                sp,
                self.facts(
                    "Acme",
                    legal_name=("Acme, Inc.",) + V,
                    hq_country=("US",) + V,
                    legal_jurisdiction=(fact_j,) + V,
                ),
            )
            self.assertIsNone(val, rec_j)
            self.assertEqual(
                b["reason"], f"asserted jurisdiction {fact_j} contradicts GLEIF jurisdiction {typed}", rec_j
            )

    def test_gleif_lists_exactly_the_facts_used(self):
        sp = self.snap(
            gleif={
                "Acme": {
                    "candidates": [cand("LEI-1", "ACME, INC.", "US", jurisdiction="US-DE")],
                    "chosen": None,
                }
            }
        )
        val, b, r = self.run1(
            "gleif",
            "Acme",
            sp,
            self.facts(
                "Acme",
                legal_name=("Acme, Inc.",) + V,
                hq_country=("US",) + V,
                legal_jurisdiction=("US-STATE:DE",) + V,
                website=("acme.example",) + V,
            ),
        )
        self.assertEqual(val, "Wilmington")
        self.assertEqual(b["facts_used"], ["country", "jurisdiction", "legal_name"])
        prov = r.fields["hq_city"].provenance
        self.assertEqual(listed_facts(prov), ["country", "jurisdiction", "legal_name"])
        self.assertNotIn("website", prov)
        no_j = self.facts(
            "Acme", legal_name=("Acme, Inc.",) + V, hq_country=("US",) + V, website=("acme.example",) + V
        )
        val, b, r = self.run1("gleif", "Acme", sp, no_j)
        self.assertEqual(val, "Wilmington")
        self.assertEqual(b["facts_used"], ["country", "legal_name"])
        self.assertEqual(listed_facts(r.fields["hq_city"].provenance), ["country", "legal_name"])

    def test_sec_website_match_does_not_override_contradicting_facts(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://acme.example/")})
        cases = {
            "both": dict(legal_name=("Globex Corporation",) + V, legal_jurisdiction=("US-STATE:NV",) + V),
            "name only": dict(legal_name=("Globex Corporation",) + V),
            "jurisdiction only": dict(legal_jurisdiction=("US-STATE:NV",) + V),
        }
        for label, fields in cases.items():
            val, b, _ = self.run1("sec", "Acme", sp, self.facts("Acme", **fields), {"Acme": "acme.example"})
            self.assertIsNone(val, label)
            self.assertIn("contradicts the registrant", b["reason"], label)
        val, b, _ = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", legal_name=("Acme Inc.",) + V, legal_jurisdiction=("US-STATE:DE",) + V),
            {"Acme": "acme.example"},
        )
        self.assertEqual(val, "Somewhere")
        self.assertEqual(b["facts_used"], ["jurisdiction", "legal_name"])

    def test_sec_website_path_compares_typed_jurisdiction_and_exact_legal_form(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://acme.example/")})
        cases = {
            "ISO:DE is a country, not the US state DE": dict(legal_jurisdiction=("ISO:DE",) + V),
            "a different legal form is a different name": dict(legal_name=("Acme LLC",) + V),
        }
        for label, fields in cases.items():
            val, b, _ = self.run1("sec", "Acme", sp, self.facts("Acme", **fields), {"Acme": "acme.example"})
            self.assertIsNone(val, label)
            self.assertIn("contradicts the registrant", b["reason"], label)

    def test_registered_agent_is_not_an_hq(self):
        sp = self.snap(
            gleif={
                "Globex Labs": {
                    "candidates": [
                        cand(
                            "LEI-RA",
                            "GLOBEX LABS, INC.",
                            "US",
                            city="Dover",
                            lines=["EXAMPLE REGISTERED AGENT SERVICE, INC."],
                        )
                    ],
                    "chosen": None,
                }
            }
        )
        facts = self.facts("Globex Labs", legal_name=("Globex Labs, Inc.",) + V, hq_country=("US",) + V)
        for field in ("hq_city", "hq_state"):
            val, b, r = self.run1("gleif", "Globex Labs", sp, facts, field=field)
            self.assertIsNone(val, field)
            self.assertIn("registered agent", r.error or "")
            self.assertIn("registered agent", b["hq_withheld"])

    def test_street_address_in_city_field_is_withheld(self):  # pins the digits-in-city rule
        sp = self.snap(
            gleif={
                "Acme": {
                    "candidates": [cand("LEI-1", "ACME, INC.", "US", city="12 Example Street")],
                    "chosen": None,
                }
            }
        )
        val, b, r = self.run1(
            "gleif", "Acme", sp, self.facts("Acme", legal_name=("ACME, INC.",) + V, hq_country=("US",) + V)
        )
        self.assertIsNone(val)
        self.assertEqual(b["hq_withheld"], "HQ city field contains a street address")


# ---------------------------------------------------------------- binding rules and low severity
class Pins(Base):
    def test_sec_jurisdiction_is_enforced(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", None)})
        val, _, _ = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", legal_name=("Acme Inc.",) + V, legal_jurisdiction=("US-STATE:NY",) + V),
        )
        self.assertIsNone(val)

    def test_inactive_candidate_never_binds(self):
        sp = self.snap(
            gleif={"Acme": {"candidates": [cand("L", "ACME, INC.", "US", status="INACTIVE")], "chosen": None}}
        )
        self.assertIsNone(
            self.run1(
                "gleif",
                "Acme",
                sp,
                self.facts("Acme", legal_name=("Acme, Inc.",) + V, hq_country=("US",) + V),
            )[0]
        )

    def test_two_bindable_candidates_abstain_as_ambiguous(self):
        c = cand("L1", "Twin, Inc.", "US", city="A", region="US-NY")
        sp = self.snap(gleif={"Twin": {"candidates": [c, dict(c, lei="L2")], "chosen": None}})
        val, b, _ = self.run1(
            "gleif", "Twin", sp, self.facts("Twin", legal_name=("Twin, Inc.",) + V, hq_country=("US",) + V)
        )
        self.assertIsNone(val)
        self.assertEqual(b["reason"], "ambiguous: 2 candidates bind")

    def test_gleif_country_must_match(self):
        sp = self.snap(
            gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "NL", region="NL-NH")], "chosen": None}}
        )
        self.assertIsNone(
            self.run1(
                "gleif",
                "Acme",
                sp,
                self.facts("Acme", legal_name=("Acme, Inc.",) + V, hq_country=("US",) + V),
            )[0]
        )

    def test_legal_form_words_are_kept(self):
        sp = self.snap(
            gleif={"Globex Labs": {"candidates": [cand("L1", "GLOBEX LABS, INC.", "US")], "chosen": None}}
        )
        val, b, _ = self.run1(
            "gleif",
            "Globex Labs",
            sp,
            self.facts("Globex Labs", legal_name=("Globex Labs, LLC",) + V, hq_country=("US",) + V),
        )
        self.assertIsNone(val)
        self.assertEqual(b["state"], "abstain")

    def test_gleif_bind_is_labelled_not_legal_jurisdiction(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}})
        _, b, _ = self.run1(
            "gleif", "Acme", sp, self.facts("Acme", legal_name=("ACME, INC.",) + V, hq_country=("US",) + V)
        )
        self.assertIn("not legal jurisdiction", b["basis"])

    def test_website_conflict_is_decisive(self):
        sp = self.snap(sec={"Acme": sec_rec("ACME INC.", "DE", "https://other-company.example/")})
        val, b, _ = self.run1(
            "sec",
            "Acme",
            sp,
            self.facts("Acme", legal_name=("Acme Inc.",) + V, legal_jurisdiction=("US-STATE:DE",) + V),
            {"Acme": "acme.example"},
        )
        self.assertIsNone(val)
        self.assertIn("CRM input domain acme.example", b["reason"])

    def test_candidate_without_lei_abstains_with_log(self):
        sp = self.snap(gleif={"Acme": {"candidates": [cand(None, "ACME, INC.", "US")], "chosen": None}})
        val, b, _ = self.run1(
            "gleif", "Acme", sp, self.facts("Acme", legal_name=("Acme, Inc.",) + V, hq_country=("US",) + V)
        )
        self.assertIsNone(val)
        self.assertEqual(b["state"], "abstain")
        self.assertIn("without LEI", b["reason"])

    def test_no_facts_always_abstains(self):
        sp = self.snap(
            sec={"Acme": sec_rec("ACME INC.", "DE", None)},
            gleif={"Acme": {"candidates": [cand("L1", "ACME, INC.", "US")], "chosen": None}},
        )
        for key in ("sec", "gleif"):
            self.assertIsNone(self.run1(key, "Acme", sp)[0], key)


if __name__ == "__main__":
    unittest.main()
