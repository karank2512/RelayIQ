"""Replay adapters for free public company sources (Wikidata, SEC EDGAR, GLEIF).

They implement the same ProviderAdapter contract as the simulators but answer from a dated
JSON snapshot ({source: {company name: record}}) written by a separate collection step, so a run is
repeatable and makes no network calls. Costs are 0 credits: these sources are free, so cost claims cannot
be tested.

enrich() never raises. An entity with no answer returns PERM_FAIL with an error string.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from relayiq.canonical.normalize import extract_root_domain
from relayiq.enums import EntityType, ProviderOutcome
from relayiq.providers.base import EnrichmentCallResult, ProviderAdapter, ProviderFieldValue
from relayiq.providers.entity_match import name_matches, org_like

ACCOUNT_FIELDS_BY_SOURCE = {
    "wikidata": {"root_domain", "hq_city", "hq_state", "founded_year"},
    "sec": {"root_domain", "hq_city", "hq_state"},
    "gleif": {"hq_city", "hq_state"},
}


def _age_days(date_str: str | None, today: datetime) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return max(0, (today - d).days)


class SnapshotProvider(ProviderAdapter):
    simulation_mode = False

    def __init__(
        self,
        key: str,
        display: str,
        snapshot_path: str,
        as_of: str,
        *,
        name_guard: bool = False,
        entity_check: bool = False,
        input_domains: dict[str, str] | None = None,
        identity_facts: dict[str, dict] | None = None,
    ):
        self.key = key
        self.display_name = display
        self._snap = json.loads(Path(snapshot_path).read_text())[key]
        self._as_of = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
        self._guard = name_guard
        self._entity = entity_check
        self._domains = input_domains  # variant D: known input domain per company name
        # Identity binding (registry sources): per-company facts in the format produced by
        # identity_facts.parse_identity_facts / load_identity_facts: {"fields": {key: {value, source, as_of,
        # ref}}, "_unverified": [...], "synthetic": bool}. Facts without provenance are refused. "verified"
        # means ASSERTED verified (provenance recorded, not proof). Never taken from labels or snapshots.
        self._facts = _check_facts(identity_facts or {})
        self.binding_log: dict[str, dict] = {}  # company name -> {"state": bound|abstain, "reason": ..., ids}

    def capabilities(self) -> dict[str, set[str]]:
        return {EntityType.ACCOUNT.value: set(ACCOUNT_FIELDS_BY_SOURCE[self.key])}

    def field_cost(self, entity_type: str, field_name: str) -> float:
        return 0.0

    def _entity_choice(self, name: str, rec: dict) -> dict | None:
        """Variant C: first candidate whose name equals the query and that reads like an organisation."""
        if self.key == "wikidata":
            for d in rec.get("details") or []:
                if (
                    name_matches(name, d.get("label"))
                    and org_like(d.get("desc"))
                    and (d.get("website") or d.get("hq_city"))
                ):
                    return d
            return None
        if self.key == "gleif":
            for c in rec.get("candidates") or []:
                if c.get("status") == "ACTIVE" and name_matches(name, c.get("name")):
                    h = c.get("hq") or {}
                    reg = h.get("region") or ""
                    return {
                        "lei": c["lei"],
                        "name": c["name"],
                        "hq_city": (h.get("city") or "").title() or None,
                        "hq_state": reg.split("-")[-1] if reg.startswith("US-") else None,
                        "last_update": c.get("last_update"),
                    }
            return None
        ch = rec.get("chosen")  # sec: the collector already matches the exact registrant name
        return ch if ch is not None and name_matches(name, ch.get("name")) else None

    def _domain_choice(self, name: str, rec: dict) -> dict | None:
        """Variant D (known-domain task): the contact's own domain is an input, so a source record is used
        only if it can be tied to that domain. No domain given means no answer.

        Wikidata: first candidate whose official website has the same root domain. GLEIF records carry no
        website, so they cannot be verified and are not used. SEC records carry no website either in the data
        collected; they are used on an exact registrant name match, which is weaker evidence.
        """
        want = extract_root_domain((self._domains or {}).get(name))
        if want is None:
            return None
        if self.key == "wikidata":
            for d in rec.get("details") or []:
                site = d.get("website")
                if site and extract_root_domain(site) == want:
                    return d
            return None
        if self.key == "sec":
            ch = rec.get("chosen")
            if ch is None:
                return None
            site = ch.get("website")
            if site:
                return ch if extract_root_domain(site) == want else None
            return ch if name_matches(name, ch.get("name")) else None
        return None

    def _chosen(self, name: str) -> dict | None:
        rec = self._snap.get(name) or {}
        if self.key in REGISTRY_SOURCES:
            # Identity binding supersedes every name-based choice (variants A/B/C/D) for registry sources:
            # a name match alone never binds a registry record to the CRM company.
            return self._bound_choice(name, rec)
        if self._domains is not None:
            return self._domain_choice(name, rec)
        if self._entity:
            return self._entity_choice(name, rec)
        ch = rec.get("chosen")
        if ch is None:
            return None
        if self._guard and not _guard_ok(self.key, name, ch):
            return None
        return ch

    def _bound_choice(self, name: str, rec: dict) -> dict | None:
        fct = self._facts.get(name) or {}
        flds = fct.get("fields") or {}
        flat = {k: v["value"] for k, v in flds.items()}
        # The CRM input-domain check always applies when an input domain is given; an asserted website fact is
        # an ADDITIONAL target, never a replacement. Both are compared by exact host.
        targets = []
        if self._domains is not None and (self._domains or {}).get(name):
            targets.append(("CRM input domain", self._domains[name]))
        if "website" in flat:
            targets.append(("asserted-verified fact website", flat["website"]))
        if self.key == "sec":
            ch, why, used = _bind_sec(rec, flat, targets)
        else:
            ch, why, used = _bind_gleif(rec, flat)
        if ch is None and fct.get("_unverified"):
            why = f"needs review: only unverified crm facts ({', '.join(fct['_unverified'])}); {why}"
        entry = {
            "state": "bound" if ch is not None else "abstain",
            "reason": why,
            "facts_provenance": {k: {x: v[x] for x in ("source", "as_of", "ref")} for k, v in flds.items()},
            "facts_note": "asserted verified (provenance recorded, not proof)",
        }
        if fct.get("synthetic"):
            entry["synthetic"] = True
        if ch is not None:
            entry["basis"] = why.replace("bound: ", "")
            entry["facts_used"] = sorted(used)  # only these are listed in the emitted provenance
            entry.update({k: ch[k] for k in ("cik", "lei") if k in ch})
            if ch.get("_hq_withheld"):
                entry["hq_withheld"] = ch["_hq_withheld"]
        self.binding_log[name] = entry
        return ch

    def enrich(self, entity_type, identifiers, fields, *, timeout_ms: int = 8000) -> EnrichmentCallResult:
        name = identifiers.get("company_name", "")
        try:
            ch = self._chosen(name)
            if ch is None:
                b = self.binding_log.get(name)
                err = f"identity not bound ({b['reason']}); needs review" if b else "no matching record"
                return EnrichmentCallResult(self.key, entity_type, ProviderOutcome.PERM_FAIL, error=err)
            b = self.binding_log.get(name) or {}
            if self.key == "sec":
                age = _age_days(ch.get("latest_filing_date"), self._as_of)
                prov = f"sec:submissions cik={ch['cik']} (age = days since latest filing, a proxy)"
            elif self.key == "gleif":
                age = _age_days(ch.get("last_update"), self._as_of)
                prov = f"gleif:lei={ch['lei']}"
            else:
                age = None  # Wikidata statements carry no retrieval age
                prov = f"wikidata:{ch['id']}"
            if self.key in REGISTRY_SOURCES and b.get("state") == "bound":
                prov += f" | identity bound ({b.get('basis')})"
                fp = b.get("facts_provenance", {})
                used = [k for k in b.get("facts_used", []) if k in fp]
                if used:  # only facts used for the bind; content neutralised; nothing printed if none
                    pv = "; ".join(
                        f"{k} source={_prov_safe(fp[k]['source'])} as_of={_prov_safe(fp[k]['as_of'])} "
                        f"ref={_prov_safe(fp[k]['ref'])}"
                        for k in used
                    )
                    prov += f"; asserted verified (provenance: {pv})"
                if b.get("synthetic"):
                    prov += " | SYNTHETIC facts, not truth"
            out: dict[str, ProviderFieldValue] = {}
            for f in fields:
                if f not in ACCOUNT_FIELDS_BY_SOURCE[self.key]:
                    continue
                v = ch.get("website") if f == "root_domain" else ch.get(f)
                if v in (None, ""):
                    continue
                out[f] = ProviderFieldValue(
                    f, str(v), provider_confidence=None, source_age_days=age, provenance=prov
                )
            if not out:
                err = "record has none of the requested fields"
                if b.get("hq_withheld"):
                    err = f"hq fields withheld ({b['hq_withheld']})"
                return EnrichmentCallResult(self.key, entity_type, ProviderOutcome.PERM_FAIL, error=err)
            return EnrichmentCallResult(self.key, entity_type, ProviderOutcome.SUCCESS, fields=out)
        except Exception as exc:  # contract: never raise
            return EnrichmentCallResult(
                self.key, entity_type, ProviderOutcome.PERM_FAIL, error=repr(exc)[:200]
            )


REGISTRY_SOURCES = ("sec", "gleif")
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
_RA_PATTERNS = (
    "registered agent",
    "c/o ",
    "corporation service company",
    "ct corporation",
    "corporation trust",
    "incorp services",
    "8 the green",
    "1209 orange st",
    "251 little falls",
)


def _check_facts(facts: dict) -> dict:
    """The provider refuses identity facts without provenance (only the identity_facts loader format is
    accepted)."""
    for name, f in facts.items():
        if not isinstance(f, dict) or not isinstance(f.get("fields"), dict):
            raise ValueError(
                f"identity_facts[{name!r}] lacks provenance; "
                "build it with identity_facts.parse_identity_facts"
            )
        for k, v in f["fields"].items():
            if not (
                isinstance(v, dict)
                and v.get("source") == "verified"
                and v.get("as_of")
                and v.get("ref")
                and "value" in v
            ):
                raise ValueError(f"identity_facts[{name!r}][{k!r}] lacks provenance (source/as_of/ref)")
    return facts


def _prov_safe(v) -> str:
    """Neutralise asserted provenance text before it is embedded in the emitted provenance string. Anything
    outside [A-Za-z0-9._:/=+-] (including '|', ';', '(', ')', spaces and newlines) becomes \\xNN, so a ref can
    neither forge a segment nor start a new line. Truncated to 120 characters."""
    import re

    return re.sub(r"[^A-Za-z0-9._:/=+-]", lambda m: f"\\x{ord(m.group()):02x}", str(v))[:120]


def _gleif_jurisdiction(v) -> str | None:
    """Typed form of a GLEIF legal jurisdiction if a record ever carries one ('US-DE' -> 'US-STATE:DE', 'IE'
    -> 'ISO:IE'). The current collector does not record it, so in practice this returns None."""
    c = str(v or "").strip().upper()
    if not c:
        return None
    if c.startswith("US-") and c[3:] in _US_STATES:
        return f"US-STATE:{c[3:]}"
    return f"ISO:{c}" if len(c) == 2 and c.isalpha() else f"GLEIF:{c}"


def _edgar_jurisdiction(code: str | None) -> str | None:
    """EDGAR stateOfIncorporation -> typed. Two-letter US state/DC codes -> 'US-STATE:XX'. Every other EDGAR
    code (e.g. 'A1', 'E9') stays 'EDGAR:<code>' and so can never equal an 'ISO:' fact: abstain rather than
    guess."""
    c = (code or "").strip().upper()
    if not c:
        return None
    return f"US-STATE:{c}" if c in _US_STATES else f"EDGAR:{c}"


def _safe_host(v: str | None):
    """Exact host (lowercased, IDNA, leading www. removed) or (None, reason). Userinfo/backslash refused."""
    import re
    from urllib.parse import urlsplit

    s = (v or "").strip()
    if not s:
        return None, "empty"
    if "@" in s or "\\" in s:
        return None, "userinfo or backslash in URL"
    try:
        host = (urlsplit(s if "://" in s else "//" + s).hostname or "").rstrip(".")
    except ValueError:
        host = ""
    if not host or "." not in host:
        return None, "no parseable host"
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None, "invalid IDNA host"
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return None, "invalid host characters"
    return (host[4:] if host.startswith("www.") else host), None


def _hq_withheld(h: dict) -> str | None:
    """A registered-agent address is not an HQ. Withhold hq fields when the address looks like one."""
    text = " ".join([*(h.get("addressLines") or []), h.get("city") or ""]).lower()
    if any(p in text for p in _RA_PATTERNS):
        return "HQ address looks like a registered agent, not an HQ"
    if any(ch.isdigit() for ch in (h.get("city") or "")):
        return "HQ city field contains a street address"
    return None


def _legal_norm(s: str | None) -> str:
    """Exact legal-name comparison: NFKC, casefold, '.' and ',' removed, whitespace collapsed. Legal-form
    tokens (Inc, Ltd, B.V., ...) are KEPT, so a subsidiary or a different legal form never equals the stated
    legal name."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", t)).strip()


def _bind_sec(rec: dict, facts: dict, targets: list):
    """SEC registrant binds only with corroboration beyond the name:
    (a) website: the registrant's website HOST equals EVERY website target (the CRM input domain when given,
        plus an asserted-verified fact website when given). Exact host only (no public-suffix list available):
        a shared root with a different host abstains as "website root unverifiable". Any conflict abstains and
        is decisive. Or
    (b) when the registrant has no website: the asserted exact legal name equals the registrant name AND the
        asserted typed jurisdiction equals the typed stateOfIncorporation (see _edgar_jurisdiction). Otherwise
        abstain."""
    ch = rec.get("chosen")
    if not ch:
        return None, "no registry record", []
    site = ch.get("website")
    ln, jur = facts.get("legal_name"), facts.get("jurisdiction")
    if site and targets:
        rhost, rerr = _safe_host(site)
        if rhost is None:
            return None, f"registrant website unusable ({rerr})", []
        for label, t in targets:
            thost, terr = _safe_host(t)
            if thost is None:
                return None, f"{label} unusable ({terr})", []
            if rhost != thost:
                if extract_root_domain(rhost) and extract_root_domain(rhost) == extract_root_domain(thost):
                    return (
                        None,
                        "website root unverifiable (no public-suffix list): "
                        f"registrant host {rhost} vs {label} {thost}",
                        [],
                    )
                return None, f"registrant website host {rhost} differs from {label} {thost}", []
        # A website match never overrides a CHECKABLE asserted fact that contradicts the registrant
        used = ["website"] if "website" in facts else []
        if ln and ch.get("name"):
            if _legal_norm(ln) != _legal_norm(ch.get("name")):
                return None, "asserted legal name contradicts the registrant (website matched)", []
            used.append("legal_name")
        reg_j = _edgar_jurisdiction(ch.get("state_of_incorporation"))
        if jur and reg_j:
            if jur != reg_j:
                return (
                    None,
                    f"asserted jurisdiction {jur} contradicts the registrant {reg_j} (website matched)",
                    [],
                )
            used.append("jurisdiction")
        return ch, "bound: registrant website host equals " + " and ".join(lbl for lbl, _ in targets), used
    if ln and jur:
        reg_j = _edgar_jurisdiction(ch.get("state_of_incorporation"))
        if _legal_norm(ln) == _legal_norm(ch.get("name")) and jur == reg_j:
            return ch, f"bound: exact legal name + jurisdiction {jur}", ["legal_name", "jurisdiction"]
        return (
            None,
            f"legal name or jurisdiction does not match the registrant (fact {jur}, registrant {reg_j})",
            [],
        )
    return None, "no corroboration beyond the name (needs legal name + jurisdiction, or a website)", []


def _bind_gleif(rec: dict, facts: dict):
    """GLEIF: consider ALL collected candidates (not just the top hit). A candidate binds only if ACTIVE, has
    an LEI, its legal name equals the asserted exact legal name, and its HQ country equals the asserted HQ
    country. Exactly one must bind. When a jurisdiction fact is supplied, the record must carry a GLEIF legal
    jurisdiction that equals it (typed); the current collector does not record one, so the result is ABSTAIN
    ("needs review: jurisdiction fact supplied; GLEIF jurisdiction not collected"). Without a jurisdiction
    fact, binding on exact legal name + HQ country stays allowed. Registered-agent addresses: identity may
    bind, HQ withheld."""
    ln, country = facts.get("legal_name"), facts.get("country")
    cands = rec.get("candidates") or []
    if not cands:
        return None, "no registry record", []
    if not (ln and country):
        return None, "no corroboration beyond the name (needs exact legal name + HQ country)", []
    hits = [
        c
        for c in cands
        if c.get("status") == "ACTIVE"
        and _legal_norm(c.get("name")) == _legal_norm(ln)
        and str(((c.get("hq") or {}).get("country")) or "").upper() == str(country).upper()
    ]
    no_lei = [c for c in hits if not c.get("lei")]
    hits = [c for c in hits if c.get("lei")]
    if len(hits) > 1:
        return None, f"ambiguous: {len(hits)} candidates bind", []
    if not hits:
        return (
            None,
            (
                "matching candidate without LEI ignored"
                if no_lei
                else "no candidate matches exact legal name + HQ country"
            ),
            [],
        )
    c = hits[0]
    used = ["legal_name", "country"]
    jur = facts.get("jurisdiction")
    if jur:
        rj = _gleif_jurisdiction(c.get("jurisdiction"))
        if rj is None:
            return None, "needs review: jurisdiction fact supplied; GLEIF jurisdiction not collected", []
        if rj != jur:
            return None, f"asserted jurisdiction {jur} contradicts GLEIF jurisdiction {rj}", []
        used.append("jurisdiction")
    h = c.get("hq") or {}
    reg = h.get("region") or ""
    withheld = _hq_withheld(h)
    basis = "exact legal name + HQ country (not legal jurisdiction)"
    if jur:
        basis = f"exact legal name + HQ country + GLEIF jurisdiction {jur}"
    return (
        {
            "lei": c["lei"],
            "name": c["name"],
            "hq_city": None if withheld else ((h.get("city") or "").title() or None),
            "hq_state": None if withheld else (reg.split("-")[-1] if reg.startswith("US-") else None),
            "hq_country": h.get("country"),
            "last_update": c.get("last_update"),
            "_hq_withheld": withheld,
        },
        "bound: " + basis,
        used,
    )


_SUFFIX = {
    "inc",
    "llc",
    "corp",
    "corporation",
    "ltd",
    "limited",
    "co",
    "company",
    "holdings",
    "bv",
    "nv",
    "plc",
    "lp",
    "the",
}


def _gnorm(s: str | None) -> str:
    import re

    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t not in _SUFFIX]
    return " ".join(toks)


def _guard_ok(key: str, query: str, ch: dict) -> bool:
    """Post-hoc name guard (variant B only): accept a record only if its name equals the query."""
    label = ch.get("label") if key == "wikidata" else ch.get("name")
    return _gnorm(label) == _gnorm(query)
