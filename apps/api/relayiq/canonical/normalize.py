"""Canonical normalization primitives for entity fields.

Pure, dependency-free helpers used by the routing and reconciliation engines to
compare provider observations against CRM values. Everything here is deterministic
and local — no network, no database.

Design notes:
- Company names strip *trailing* legal suffixes only ("SA Recycling" keeps "sa");
  a name that is nothing but a suffix ("Company") is preserved as-is.
- Root-domain extraction uses a small builtin set of two-part public suffixes
  rather than the full PSL — sufficient for the synthetic world and CRM data.
- ``titles_equivalent`` deliberately treats "VP Sales" and "Vice President of
  Revenue" as NOT equivalent: even at the same seniority, the function tokens
  ("sales" vs "revenue") differ, and conflating revenue leadership with sales
  leadership would cause false-positive reconciliation merges.
"""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

__all__ = [
    "EMPLOYEE_RANGES",
    "derive_department",
    "derive_seniority",
    "employee_count_to_range",
    "extract_root_domain",
    "industries_equivalent",
    "is_valid_domain",
    "is_valid_email_syntax",
    "normalize_company_name",
    "normalize_email",
    "normalize_industry",
    "normalize_job_title",
    "normalize_value",
    "ranges_adjacent",
    "titles_equivalent",
    "validate_field",
    "values_equivalent",
]

# ---------------------------------------------------------------------------
# Company names
# ---------------------------------------------------------------------------

# Matched against whole tokens (word-boundary safe) and only stripped from the tail.
# Dots/apostrophes are removed before tokenizing, so "l.l.c." -> "llc", "s.a." -> "sa".
_COMPANY_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "llc",
        "ltd",
        "limited",
        "gmbh",
        "sa",
        "plc",
        "co",
        "company",
        "holdings",
        "group",
    }
)

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_company_name(name: str | None) -> str | None:
    """Lowercase, strip punctuation, collapse whitespace, strip trailing legal suffixes.

    "Acme Inc." and "Acme Corporation" both normalize to "acme". Suffixes are
    stripped repeatedly ("Acme Holdings Inc" -> "acme") but never below one token.
    """
    if name is None:
        return None
    text = name.casefold().replace(".", "").replace("'", "")
    tokens = _NON_WORD_RE.sub(" ", text).split()
    while len(tokens) > 1 and tokens[-1] in _COMPANY_LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or None


# ---------------------------------------------------------------------------
# Domains and URLs
# ---------------------------------------------------------------------------

# Common two-part public suffixes; a domain ending in one of these needs three
# labels to be registrable ("acme.co.uk", not "co.uk").
_TWO_PART_PUBLIC_SUFFIXES: frozenset[str] = frozenset(
    {"co.uk", "com.au", "co.jp", "com.br", "co.in", "org.uk", "ac.uk", "com.mx", "co.nz", "com.sg"}
)

_DOMAIN_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD_RE = re.compile(r"[a-z]{2,}")
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def is_valid_domain(domain: str | None) -> bool:
    """Pragmatic DNS hostname validation.

    Requires at least one dot, labels of 1-63 chars ([a-z0-9-], no edge hyphens),
    total length <= 253, and an alphabetic TLD of >= 2 chars. Reserved TLDs such
    as .test/.example pass (synthetic data uses them); all-numeric TLDs (and thus
    IPv4 addresses) fail.
    """
    if domain is None:
        return False
    candidate = domain.strip().casefold()
    if not candidate or len(candidate) > 253 or "." not in candidate:
        return False
    labels = candidate.split(".")
    if any(not _DOMAIN_LABEL_RE.fullmatch(label or "") for label in labels):
        return False
    return _TLD_RE.fullmatch(labels[-1]) is not None


def _extract_hostname(url_or_domain: str) -> str | None:
    """Pull the hostname out of a URL or bare domain (handles port/path/query)."""
    raw = url_or_domain.strip().casefold()
    if not raw:
        return None
    candidate = raw if (_URL_SCHEME_RE.match(raw) or raw.startswith("//")) else f"//{raw}"
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return None
    return host or None


def extract_root_domain(url_or_domain: str | None) -> str | None:
    """Return the lowercase registrable root domain, or None for invalid input.

    Accepts bare domains and URLs with scheme/port/path/query; strips "www.".
    "https://app.staging.acme.com/x?y=1" -> "acme.com"; "sub.acme.co.uk" ->
    "acme.co.uk". IP addresses, bare public suffixes ("co.uk"), and hostnames
    with spaces or bad chars return None.
    """
    if url_or_domain is None:
        return None
    host = _extract_hostname(url_or_domain)
    if host is None:
        return None
    host = host.rstrip(".").removeprefix("www.")
    if not is_valid_domain(host):
        return None
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_PART_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    if len(labels) == 2 and host in _TWO_PART_PUBLIC_SUFFIXES:
        return None  # a public suffix alone has no registrable domain
    return ".".join(labels[-2:])


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

_EMAIL_LOCAL_RE = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*")


def is_valid_email_syntax(email: str | None) -> bool:
    """Pragmatic RFC-lite check: single @, dot-atom local part, valid domain part."""
    if email is None:
        return False
    candidate = email.strip().casefold()
    if not candidate or len(candidate) > 254 or candidate.count("@") != 1:
        return False
    local, _, domain = candidate.partition("@")
    if not local or len(local) > 64 or not _EMAIL_LOCAL_RE.fullmatch(local):
        return False
    return is_valid_domain(domain)


def normalize_email(email: str | None) -> str | None:
    """Trim and lowercase; None if the syntax is invalid."""
    if email is None:
        return None
    candidate = email.strip().casefold()
    return candidate if is_valid_email_syntax(candidate) else None


# ---------------------------------------------------------------------------
# Job titles
# ---------------------------------------------------------------------------

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")

# Applied per-token after dot removal ("v.p." -> "vp"), so expansion is word-boundary safe.
_TITLE_ABBREVIATIONS: dict[str, str] = {
    "vp": "vice president",
    "svp": "senior vice president",
    "evp": "executive vice president",
    "sr": "senior",
    "jr": "junior",
    "eng": "engineering",
    "mgr": "manager",
    "dir": "director",
}

# Kept verbatim (never expanded); also lets "c.e.o." normalize to "ceo".
_C_SUITE_TOKENS: frozenset[str] = frozenset(
    {"ceo", "cto", "cfo", "coo", "cmo", "cro", "ciso", "cio", "chro", "cpo"}
)


def normalize_job_title(title: str | None) -> str | None:
    """Casefold, drop parenthetical suffixes, collapse whitespace, expand abbreviations.

    "VP Sales (EMEA)" -> "vice president sales"; "Sr. Eng Mgr" -> "senior
    engineering manager". C-suite acronyms (ceo/cto/...) are kept as-is.
    """
    if title is None:
        return None
    text = _PARENTHETICAL_RE.sub(" ", title).casefold()
    text = text.replace(",", " ").replace(";", " ").replace("/", " ")
    tokens: list[str] = []
    for token in text.split():
        dotless = token.replace(".", "")
        if not dotless:
            continue
        if dotless in _TITLE_ABBREVIATIONS:
            tokens.append(_TITLE_ABBREVIATIONS[dotless])
        elif dotless in _C_SUITE_TOKENS:
            tokens.append(dotless)
        else:
            tokens.append(token.strip("."))
    return " ".join(tokens) or None


_SENIOR_IC_TOKENS: frozenset[str] = frozenset({"senior", "staff", "principal", "lead"})


def derive_seniority(title: str | None) -> str:
    """Bucket a title into a seniority level, checking the most-senior patterns first."""
    normalized = normalize_job_title(title)
    if normalized is None:
        return "unknown"
    tokens = set(normalized.split())
    if "chief" in tokens or tokens & _C_SUITE_TOKENS or ("president" in tokens and "vice" not in tokens):
        return "c_level"
    if "founder" in normalized:  # substring: matches founder, co-founder, cofounder
        return "founder"
    if re.search(r"\bvice president\b", normalized):
        return "vp"
    if "director" in tokens or "head" in tokens:
        return "director"
    if "manager" in tokens:
        return "manager"
    if tokens & _SENIOR_IC_TOKENS:
        return "senior_ic"
    return "ic"


# Ordered: first match wins. revops must precede sales/operations so that
# "sales operations" and "revenue operations" don't fall through.
_DEPARTMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("revops", re.compile(r"\brevenue operations\b|\brev ops\b|\brevops\b|\bsales operations\b|\bsales ops\b")),  # noqa: E501
    ("customer_success", re.compile(r"\bcustomer success\b|\bcustomer experience\b|\bcsm\b")),
    ("sales", re.compile(r"\bsales\b|\baccount executive\b|\bsdr\b|\bbdr\b|\bbusiness development\b|\bcro\b")),  # noqa: E501
    ("marketing", re.compile(r"\bmarketing\b|\bgrowth\b|\bbrand\b|\bcontent\b|\bdemand generation\b|\bcmo\b|\bseo\b")),  # noqa: E501
    ("engineering", re.compile(r"\bengineering\b|\bengineer\b|\bdeveloper\b|\bsoftware\b|\bdevops\b|\bsre\b|\bcto\b|\bqa\b")),  # noqa: E501
    ("product", re.compile(r"\bproduct\b|\bdesign\b|\bux\b|\bcpo\b")),
    ("finance", re.compile(r"\bfinance\b|\bfinancial\b|\baccounting\b|\baccountant\b|\bcontroller\b|\bcfo\b|\btreasury\b")),  # noqa: E501
    ("hr", re.compile(r"\bhuman resources\b|\bhr\b|\bpeople\b|\btalent\b|\brecruit(?:er|ing|ment)\b|\bchro\b")),  # noqa: E501
    ("operations", re.compile(r"\boperations\b|\bops\b|\bsupply chain\b|\blogistics\b|\bcoo\b")),
    ("it", re.compile(r"\binformation technology\b|\bit\b|\bsecurity\b|\binfosec\b|\bciso\b|\bcio\b|\bsysadmin\b")),  # noqa: E501
    ("legal", re.compile(r"\blegal\b|\bcounsel\b|\battorney\b|\bcompliance\b|\bparalegal\b")),
)


def derive_department(title: str | None) -> str:
    """Bucket a title into a functional department; "unknown" when nothing matches."""
    normalized = normalize_job_title(title)
    if normalized is None:
        return "unknown"
    for department, pattern in _DEPARTMENT_PATTERNS:
        if pattern.search(normalized):
            return department
    return "unknown"


_TITLE_STOPWORDS: frozenset[str] = frozenset({"of", "the", "and", "&", ","})

# Seniority-flavored tokens are excluded from the "significant token" overlap so
# that shared rank words ("vice", "president") never make two different
# functions look equivalent.
_SENIORITY_FLAVOR_TOKENS: frozenset[str] = (
    frozenset(
        {
            "vice",
            "president",
            "senior",
            "junior",
            "executive",
            "chief",
            "officer",
            "head",
            "director",
            "manager",
            "lead",
            "staff",
            "principal",
            "founder",
            "cofounder",
            "co-founder",
            "vp",
            "svp",
            "evp",
        }
    )
    | _C_SUITE_TOKENS
)


def _significant_title_tokens(normalized_title: str) -> set[str]:
    return {
        token
        for token in normalized_title.split()
        if token not in _TITLE_STOPWORDS and token not in _SENIORITY_FLAVOR_TOKENS
    }


def titles_equivalent(a: str | None, b: str | None) -> bool:
    """True when two titles refer to the same role.

    Either the normalized titles match exactly, or the titles share the same
    seniority AND the same department AND at least one significant (function)
    token after dropping stopwords and rank words.

    Deliberate choice: "VP Sales" vs "Vice President of Revenue" is False —
    the function tokens ("sales" vs "revenue") differ, and revenue leadership
    is not assumed to be the same role as sales leadership. Missing titles
    (None/empty) are never equivalent to anything, including each other.
    """
    norm_a = normalize_job_title(a)
    norm_b = normalize_job_title(b)
    if norm_a is None or norm_b is None:
        return False
    if norm_a == norm_b:
        return True
    if derive_seniority(norm_a) != derive_seniority(norm_b):
        return False
    if derive_department(norm_a) != derive_department(norm_b):
        return False
    sig_a = _significant_title_tokens(norm_a)
    sig_b = _significant_title_tokens(norm_b)
    if not sig_a and not sig_b:
        return True  # pure-rank titles, e.g. "ceo" vs "chief executive officer"
    return bool(sig_a & sig_b)


# ---------------------------------------------------------------------------
# Industry taxonomy
# ---------------------------------------------------------------------------

# Keyed by slug (lowercase, non-alphanumeric runs collapsed to "_").
_INDUSTRY_ALIASES: dict[str, str] = {
    "software": "software",
    "information_technology": "software",
    "it_services": "software",
    "saas": "software",
    "computer_software": "software",
    "financial_services": "financial_services",
    "banking": "financial_services",
    "fintech": "financial_services",
    "healthcare": "healthcare",
    "health_care": "healthcare",
    "hospitals": "healthcare",
    "medical": "healthcare",
    "manufacturing": "manufacturing",
    "industrial": "manufacturing",
    "retail": "retail",
    "e_commerce": "retail",
    "ecommerce": "retail",
    "consulting": "professional_services",
    "professional_services": "professional_services",
    "education": "education",
    "real_estate": "real_estate",
    "media": "media",
    "entertainment": "media",
    "publishing": "media",
}


def _slugify(text: str) -> str:
    return _NON_WORD_RE.sub("_", text.casefold()).strip("_")


def normalize_industry(raw: str | None) -> str | None:
    """Map a raw industry label onto the canonical taxonomy.

    Known aliases collapse to canonical values ("SaaS" -> "software"); unknown
    inputs pass through as a slug ("Oil & Gas" -> "oil_gas").
    """
    if raw is None:
        return None
    slug = _slugify(raw)
    if not slug:
        return None
    return _INDUSTRY_ALIASES.get(slug, slug)


def industries_equivalent(a: str | None, b: str | None) -> bool:
    """True when both industries normalize to the same canonical value (None never matches)."""
    norm_a = normalize_industry(a)
    norm_b = normalize_industry(b)
    if norm_a is None or norm_b is None:
        return False
    return norm_a == norm_b


# ---------------------------------------------------------------------------
# Employee ranges
# ---------------------------------------------------------------------------

EMPLOYEE_RANGES: list[tuple[int, int | None, str]] = [
    (1, 10, "1-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1000"),
    (1001, 5000, "1001-5000"),
    (5001, 10000, "5001-10000"),
    (10001, None, "10001+"),
]

_RANGE_LABELS: list[str] = [label for _, _, label in EMPLOYEE_RANGES]


def employee_count_to_range(n: int | None) -> str | None:
    """Bucket a headcount into its canonical range label; None for missing/non-positive."""
    if n is None or n < 1:
        return None
    for low, high, label in EMPLOYEE_RANGES:
        if n >= low and (high is None or n <= high):
            return label
    return None  # unreachable for n >= 1, kept for totality


def ranges_adjacent(a: str | None, b: str | None) -> bool:
    """True when two range labels are the same bucket or immediate neighbors."""
    if a is None or b is None:
        return False
    try:
        index_a = _RANGE_LABELS.index(a.strip())
        index_b = _RANGE_LABELS.index(b.strip())
    except ValueError:
        return False
    return abs(index_a - index_b) <= 1


# ---------------------------------------------------------------------------
# Field-level dispatch
# ---------------------------------------------------------------------------

_COMPANY_NAME_FIELDS: frozenset[str] = frozenset({"name", "company_name"})
_DOMAIN_FIELDS: frozenset[str] = frozenset({"root_domain", "company_domain", "website"})
_EMAIL_FIELDS: frozenset[str] = frozenset({"work_email", "email"})

_EMPLOYEE_COUNT_TOLERANCE = 0.15
_MIN_FOUNDED_YEAR = 1600


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _default_equal(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def values_equivalent(field_name: str, a: str | None, b: str | None) -> bool:
    """Field-aware equivalence used by reconciliation to decide provider agreement.

    Two None values agree; a None against a value does not. Normalizer-based
    comparisons fall back to casefolded string equality when either side fails
    to normalize (so garbage-vs-garbage still compares textually).
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    field = field_name.strip().casefold()
    if field == "job_title":
        return titles_equivalent(a, b)
    if field == "industry":
        return industries_equivalent(a, b)
    if field in _COMPANY_NAME_FIELDS:
        norm_a, norm_b = normalize_company_name(a), normalize_company_name(b)
        if norm_a is not None and norm_b is not None:
            return norm_a == norm_b
        return _default_equal(a, b)
    if field in _DOMAIN_FIELDS:
        root_a, root_b = extract_root_domain(a), extract_root_domain(b)
        if root_a is not None and root_b is not None:
            return root_a == root_b
        return _default_equal(a, b)
    if field in _EMAIL_FIELDS:
        email_a, email_b = normalize_email(a), normalize_email(b)
        if email_a is not None and email_b is not None:
            return email_a == email_b
        return _default_equal(a, b)
    if field == "employee_range":
        return a == b  # bucket labels compare exactly
    if field == "employee_count":
        count_a, count_b = _parse_int(a), _parse_int(b)
        if count_a is not None and count_b is not None:
            if count_a == count_b:
                return True
            return abs(count_a - count_b) <= _EMPLOYEE_COUNT_TOLERANCE * max(abs(count_a), abs(count_b))
        return _default_equal(a, b)  # non-parseable numerics fall back to string equality
    return _default_equal(a, b)


def normalize_value(field_name: str, value: str | None) -> str | None:
    """Per-field canonical normalizer. Empty/whitespace-only input becomes None."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    field = field_name.strip().casefold()
    if field == "job_title":
        return normalize_job_title(stripped)
    if field == "industry":
        return normalize_industry(stripped)
    if field in _COMPANY_NAME_FIELDS:
        return normalize_company_name(stripped)
    if field in _DOMAIN_FIELDS:
        return extract_root_domain(stripped)
    if field in _EMAIL_FIELDS:
        return normalize_email(stripped)
    if field == "employee_count":
        return stripped  # kept as the raw (stripped) string; parsing is the caller's concern
    return stripped


def _is_linkedin_url(value: str | None) -> bool:
    if value is None:
        return False
    host = _extract_hostname(value)
    if host is None:
        return False
    host = host.rstrip(".").removeprefix("www.")
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def validate_field(field_name: str, value: str | None) -> dict[str, bool | dict[str, bool]]:
    """Run per-field validity checks.

    Returns {"valid": bool, "checks": {check_name: bool}}. Every field gets a
    "non_empty" check; known fields add a type-specific check. The founded_year
    upper bound is the current year (the spec's 1600..2026 window, kept fresh).
    """
    field = field_name.strip().casefold()
    checks: dict[str, bool] = {"non_empty": value is not None and value.strip() != ""}
    if field in _DOMAIN_FIELDS:
        checks["domain_valid"] = value is not None and extract_root_domain(value) is not None
    elif field in _EMAIL_FIELDS:
        checks["email_syntax"] = is_valid_email_syntax(value)
    elif field == "employee_count":
        parsed = _parse_int(value)
        checks["positive_integer"] = parsed is not None and parsed > 0
    elif field == "founded_year":
        parsed = _parse_int(value)
        max_year = datetime.now(tz=UTC).year
        checks["year_in_range"] = parsed is not None and _MIN_FOUNDED_YEAR <= parsed <= max_year
    elif field == "linkedin_url":
        checks["linkedin_host"] = _is_linkedin_url(value)
    return {"valid": all(checks.values()), "checks": checks}
