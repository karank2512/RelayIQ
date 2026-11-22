"""Optional identity-facts sidecar for registry binding (SEC/GLEIF). Stdlib only. Never reads labels,
snapshots or provider output: facts come from the CRM or from a separate checking step, with per-field
provenance.

IMPORTANT: provenance is RECORDED and CARRIED THROUGH, never treated as proof. A field marked "verified" is an
ASSERTION by whoever wrote the file ("asserted verified"); the presence of a source, date or ref does not make
it true.

File format: CSV, UTF-8, header exactly FACT_COLUMNS (order free, no extra columns). One row per company.
  company                      must equal the CRM input name (rows for unknown companies are refused)
  <field>, <field>_source, <field>_as_of, <field>_ref   for field in FACT_FIELDS
     <field>_source  : exactly "verified" (asserted verified) | "crm" (as entered in the CRM) | "" (no value)
     <field>_as_of   : ISO date YYYY-MM-DD, a real date, not after the run date; required for any value
     <field>_ref     : evidence pointer; required for "verified"; placeholders refused (see PLACEHOLDER_REFS)
Field formats (refused otherwise):
  legal_jurisdiction : namespaced, never bare: "ISO:<alpha-2>" (country) or "US-STATE:<2 letters>" (state/DC)
  hq_country         : ISO 3166-1 alpha-2 (e.g. "US", "GB")
  website            : a bare host or http(s) URL with a parseable host and no userinfo; "n/a"-style values
                       refused
Rules:
  - a value without _source or _as_of (or a "verified" value without _ref) has no provenance: ignored,
    reported;
  - only "verified" fields reach the binder, each as {value, source, as_of, ref}; "crm" fields are listed
    under `_unverified` (the rule abstains with "needs review: only unverified crm facts");
  - the ref "SYNTHETIC-TEMPLATE-NOT-TRUTH" is refused unless allow_synthetic=True; then EVERY returned company
    is marked "synthetic": True and the binder labels everything it emits as SYNTHETIC;
  - malformed input raises FactsError.
ISO 3166-1 alpha-2 list embedded below (249 codes, as listed in the tz database's iso3166.tab).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from typing import Any
from urllib.parse import urlsplit

FACT_FIELDS = ("legal_name", "legal_jurisdiction", "hq_country", "website")
FACT_COLUMNS = ("company",) + tuple(f"{f}{s}" for f in FACT_FIELDS for s in ("", "_source", "_as_of", "_ref"))
SOURCES = {"verified", "crm"}
BINDER_KEY = {
    "legal_name": "legal_name",
    "legal_jurisdiction": "jurisdiction",
    "hq_country": "country",
    "website": "website",
}
SYNTHETIC_REF = "SYNTHETIC-TEMPLATE-NOT-TRUTH"
PLACEHOLDER_REFS = {"", "x", "-", "--", "n/a", "na", "none", "null", "tbd", "todo", "?", "ref", "unknown"}
ISO_ALPHA2 = frozenset(
    (
        "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ "
        "BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM "
        "DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS "
        "GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN "
        "KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ "
        "MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM "
        "PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV "
        "SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI "
        "VN VU WF WS YE YT ZA ZM ZW"
    ).split()
)
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


class FactsError(ValueError):
    pass


def normalize_jurisdiction(v: str) -> str:
    """Typed jurisdiction: 'ISO:XX' (country) or 'US-STATE:XX'. Bare or free-text codes are refused."""
    m = re.fullmatch(r"(ISO|US-STATE):([A-Z]{2})", v.strip())
    if not m:
        raise FactsError(f"legal_jurisdiction must be 'ISO:<alpha-2>' or 'US-STATE:<XX>', got {v!r}")
    ns, code = m.groups()
    if ns == "ISO" and code not in ISO_ALPHA2:
        raise FactsError(f"legal_jurisdiction {v!r}: unknown ISO 3166-1 alpha-2 code")
    if ns == "US-STATE" and code not in US_STATES:
        raise FactsError(f"legal_jurisdiction {v!r}: unknown US state code")
    return f"{ns}:{code}"


def website_host(v: str) -> str:
    """Parseable host, lowercased, IDNA-encoded, leading 'www.' kept off; userinfo refused."""
    s = v.strip()
    if "@" in s or "\\" in s:
        raise FactsError(f"website {v!r}: userinfo or backslash not allowed")
    try:
        parts = urlsplit(s if "://" in s else "//" + s)
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        host = ""
    if not host or "." not in host or not re.fullmatch(r"[A-Za-z0-9.\-\u00a0-\uffff]+", host):
        raise FactsError(f"website {v!r}: no parseable host")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise FactsError(f"website {v!r}: host is not valid IDNA") from None
    return host[4:] if host.startswith("www.") else host


def _validate_value(f: str, val: str) -> str:
    if f == "legal_jurisdiction":
        return normalize_jurisdiction(val)
    if f == "hq_country":
        if val not in ISO_ALPHA2:
            raise FactsError(f"hq_country must be an ISO 3166-1 alpha-2 code, got {val!r}")
        return val
    if f == "website":
        website_host(val)  # validate only; the raw value is kept
    return val


def parse_identity_facts(
    text: str, known_companies, *, run_date: _dt.date | None = None, allow_synthetic: bool = False
) -> tuple[dict, dict]:
    """Returns (identity_facts for SnapshotProvider, report). Raises FactsError on malformed input."""
    run_date = run_date or _dt.date.today()
    known = set(known_companies)
    rd = csv.DictReader(io.StringIO(text, newline=""))
    hdr = rd.fieldnames or []
    if sorted(hdr) != sorted(FACT_COLUMNS) or len(hdr) != len(FACT_COLUMNS):
        raise FactsError(f"header must be exactly: {','.join(FACT_COLUMNS)}")
    facts, seen = {}, set()
    report: dict[str, Any] = {
        "rows": 0,
        "verified_fields": 0,
        "crm_fields": 0,
        "ignored": [],
        "synthetic": False,
        "note": "verified = asserted verified by the file's author; provenance is recorded, not proof",
    }
    for i, row in enumerate(rd, start=2):
        if None in row or any(v is None for v in row.values()):
            raise FactsError(f"line {i}: wrong number of columns")
        name = (row["company"] or "").strip()
        if not name:
            raise FactsError(f"line {i}: empty company")
        if name in seen:
            raise FactsError(f"line {i}: duplicate company {name!r}")
        if name not in known:
            raise FactsError(f"line {i}: company {name!r} is not in the CRM input")
        seen.add(name)
        report["rows"] += 1
        fields, unverified, synthetic = {}, [], False
        for f in FACT_FIELDS:
            val, src = (row[f] or "").strip(), (row[f"{f}_source"] or "").strip()
            as_of, ref = (row[f"{f}_as_of"] or "").strip(), (row[f"{f}_ref"] or "").strip()
            if src and src not in SOURCES:
                raise FactsError(f"line {i}: {f}_source must be exactly verified|crm|empty, got {src!r}")
            if as_of:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
                    raise FactsError(f"line {i}: {f}_as_of must be YYYY-MM-DD")
                try:
                    d = _dt.date.fromisoformat(as_of)
                except ValueError:
                    raise FactsError(f"line {i}: {f}_as_of is not a real date") from None
                if d > run_date:
                    raise FactsError(f"line {i}: {f}_as_of {as_of} is after the run date {run_date}")
            if ref == SYNTHETIC_REF:
                if not allow_synthetic:
                    raise FactsError(
                        f"line {i}: {f}_ref is the synthetic placeholder; set allow_synthetic to use it"
                    )
                synthetic = True
            elif src == "verified" and ref.lower() in PLACEHOLDER_REFS | {""} and ref:
                raise FactsError(f"line {i}: {f}_ref {ref!r} is a placeholder")
            if not val:
                continue
            val = _validate_value(f, val)
            if not src or not as_of or (src == "verified" and not ref):
                report["ignored"].append(f"{name}:{f} (no provenance)")
                continue
            if src == "verified":
                fields[BINDER_KEY[f]] = {"value": val, "source": src, "as_of": as_of, "ref": ref}
                report["verified_fields"] += 1
            else:
                unverified.append(f)
                report["crm_fields"] += 1
        if fields or unverified:
            facts[name] = {
                "fields": fields,
                "_unverified": unverified,
                "synthetic": synthetic or allow_synthetic,
            }
            report["synthetic"] = report["synthetic"] or synthetic or allow_synthetic
    return facts, report


def load_identity_facts(path, known_companies, **kw) -> tuple[dict, dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return parse_identity_facts(fh.read(), known_companies, **kw)
