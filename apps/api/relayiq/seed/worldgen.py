"""Deterministic synthetic world generator.

Produces the ground-truth universe (companies + contacts with known true values) plus
per-provider distorted views. Provider simulators serve values from this file; quality
metrics compare selections against `truth` — so precision/recall are measured, never
fabricated. All data is synthetic: names come from builtin wordlists and every domain
uses the RFC-2606-reserved `.test` TLD.

Usage:
    python -m relayiq.seed.worldgen --out data/synthetic_world.json --companies 150 --seed 42
"""

import argparse
import json
import random
from pathlib import Path

ADJECTIVES = [
    "Meridian", "Northwind", "Bluepeak", "Cinder", "Harbor", "Quartz", "Vertex", "Lumen",
    "Cascade", "Ironleaf", "Summit", "Pinnacle", "Copperline", "Silverbrook", "Redwood",
    "Atlas", "Beacon", "Crescent", "Drift", "Ember", "Falcon", "Granite", "Horizon",
    "Juniper", "Keystone", "Larkspur", "Monarch", "Nimbus", "Orchard", "Prairie",
]
NOUNS = [
    "Robotics", "Analytics", "Logistics", "Systems", "Dynamics", "Labs", "Networks",
    "Software", "Biotech", "Manufacturing", "Payments", "Security", "Health", "Energy",
    "Media", "Foods", "Freight", "Insurance", "Learning", "Retail", "Micro", "Data",
]
LEGAL_SUFFIXES = ["Inc.", "LLC", "Corporation", "Ltd.", "Co.", "Holdings", "Group", ""]
FIRST_NAMES = [
    "Jordan", "Avery", "Riley", "Morgan", "Casey", "Quinn", "Rowan", "Skyler", "Emerson",
    "Finley", "Harper", "Kendall", "Logan", "Marlow", "Nico", "Parker", "Reese", "Sawyer",
    "Tatum", "Blake", "Cameron", "Devon", "Ellis", "Frankie", "Greer", "Hollis",
]
LAST_NAMES = [
    "Calloway", "Ashford", "Brennan", "Castellan", "Dunmore", "Ellery", "Fairbank",
    "Granger", "Hollowell", "Iverson", "Jennings", "Kingsley", "Lockhart", "Merriweather",
    "Northcott", "Oakes", "Pemberton", "Quimby", "Rutherford", "Saxton", "Thornbury",
    "Underhill", "Vance", "Whitfield", "Yardley", "Zeller",
]
INDUSTRIES = [
    ("Software", "SaaS"), ("Software", "Developer Tools"), ("Financial Services", "Payments"),
    ("Healthcare", "Digital Health"), ("Manufacturing", "Industrial Automation"),
    ("Retail", "E-commerce"), ("Professional Services", "Consulting"), ("Media", "Publishing"),
    ("Education", "EdTech"), ("Real Estate", "PropTech"),
]
WRONG_INDUSTRY = {"Software": "Consulting", "Financial Services": "Software", "Healthcare": "Manufacturing",
                  "Manufacturing": "Retail", "Retail": "Media", "Professional Services": "Software",
                  "Media": "Education", "Education": "Media", "Real Estate": "Financial Services"}
NAME_VARIANTS = ["{n} Inc.", "{n} Corporation", "The {n} Company", "{n}"]
CITIES = [
    ("Madison", "WI", "United States"), ("Austin", "TX", "United States"),
    ("Denver", "CO", "United States"), ("Toronto", "ON", "Canada"),
    ("London", "England", "United Kingdom"), ("Berlin", "BE", "Germany"),
    ("Sydney", "NSW", "Australia"), ("Bangalore", "KA", "India"), ("Paris", "IDF", "France"),
]
FILTERED_COUNTRIES = {"Australia", "India", "France"}  # outside default campaign allowlist
COMPANY_TYPES = ["Private", "Public", "Nonprofit", "Subsidiary"]
TECH_SIGNALS = ["salesforce", "hubspot", "aws", "gcp", "azure", "snowflake", "stripe", "shopify",
                "kubernetes", "datadog", "segment", "marketo"]
TITLES = [
    ("VP Sales", "vp", "sales"), ("Vice President of Sales", "vp", "sales"),
    ("Director of Marketing", "director", "marketing"), ("Head of Revenue Operations", "director", "revops"),
    ("Chief Revenue Officer", "c_level", "sales"), ("Senior Sales Engineer", "senior_ic", "sales"),
    ("Account Executive", "ic", "sales"), ("Growth Marketing Manager", "manager", "marketing"),
    ("CTO", "c_level", "engineering"), ("Engineering Manager", "manager", "engineering"),
    ("Chief Financial Officer", "c_level", "finance"), ("RevOps Analyst", "ic", "revops"),
    ("Director of Sales Development", "director", "sales"), ("CEO", "c_level", "operations"),
]
TITLE_CONFUSIONS = {
    "VP Sales": "Director of Sales",
    "Vice President of Sales": "VP Marketing",
    "Director of Marketing": "Marketing Manager",
    "Head of Revenue Operations": "Sales Operations Manager",
    "Chief Revenue Officer": "VP Sales",
    "Senior Sales Engineer": "Solutions Architect",
    "Account Executive": "Senior Account Executive",
    "Growth Marketing Manager": "Demand Generation Lead",
    "CTO": "VP Engineering",
    "Engineering Manager": "Senior Software Engineer",
    "Chief Financial Officer": "VP Finance",
    "RevOps Analyst": "Revenue Operations Manager",
    "Director of Sales Development": "SDR Manager",
    "CEO": "Founder",
}
EMPLOYEE_BUCKETS = [(1, 10), (11, 50), (51, 200), (201, 500), (501, 1000), (1001, 5000)]

COMPANY_FIELDS = ["name", "website", "root_domain", "linkedin_url", "industry", "sub_industry",
                  "employee_count", "annual_revenue_usd", "hq_city", "hq_state", "hq_country",
                  "company_type", "founded_year", "technology_signals"]
CONTACT_FIELDS = ["first_name", "last_name", "full_name", "work_email", "job_title", "seniority",
                  "department", "country", "linkedin_url"]

# Provider personalities: (accuracy, age_range, extra_missing) per field group
PERSONALITIES: dict[str, dict[str, dict]] = {
    "alpha": {
        "company": {"accuracy": 0.92, "age": (5, 120), "missing_extra": 0.0},
        "contact": {"accuracy": 0.75, "age": (60, 400), "missing_extra": 0.10},
    },
    "beta": {
        "company": {"accuracy": 0.85, "age": (10, 90), "missing_extra": 0.15},
        "contact": {"accuracy": 0.93, "age": (1, 45), "missing_extra": 0.0},
    },
}


def _range_label(n: int) -> str:
    for lo, hi in EMPLOYEE_BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}"
    return "5001-10000" if n <= 10000 else "10001+"


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _wrong_value(rng: random.Random, field: str, truth: dict, is_contact: bool) -> object:
    """A plausible but incorrect value for a field (drives measurable conflicts)."""
    v = truth.get(field)
    if field == "employee_count" and isinstance(v, int):
        buckets = [b for b in EMPLOYEE_BUCKETS if not (b[0] <= v <= b[1])]
        lo, hi = rng.choice(buckets[:3] if v > 200 else buckets[-3:])
        return rng.randint(lo, hi)
    if field == "job_title":
        return TITLE_CONFUSIONS.get(str(v), "Operations Manager")
    if field == "seniority":
        return rng.choice([s for s in ["vp", "director", "manager", "ic"] if s != v])
    if field == "department":
        return rng.choice([d for d in ["sales", "marketing", "operations", "engineering"] if d != v])
    if field in ("root_domain", "website"):
        dom = truth.get("root_domain") or "example.test"
        wrong = f"get{dom}"
        return f"https://{wrong}" if field == "website" else wrong
    if field == "industry":
        return WRONG_INDUSTRY.get(str(v), "Consulting")
    if field == "sub_industry":
        return "General"
    if field == "annual_revenue_usd" and isinstance(v, int):
        return int(v * rng.choice([0.4, 2.5]))
    if field == "founded_year" and isinstance(v, int):
        return v + rng.choice([-7, -3, 4, 9])
    if field in ("hq_city", "hq_state", "hq_country", "country"):
        city = rng.choice(CITIES)
        return {"hq_city": city[0], "hq_state": city[1], "hq_country": city[2], "country": city[2]}[field]
    if field == "company_type":
        return rng.choice([t for t in COMPANY_TYPES if t != v])
    if field == "linkedin_url":
        base = str(v or "https://www.linkedin.com/in/unknown-demo")
        return base.replace("-demo", "-alt-demo")
    if field == "work_email":
        email = str(v or "person@example.test")
        return email.replace("@", ".x@", 1)
    if field == "technology_signals":
        return rng.sample(TECH_SIGNALS, k=3)
    if field == "name":
        return str(v)  # name variants handled separately (correct=true variants)
    return v


def _provider_view(rng: random.Random, provider: str, kind: str, truth: dict, fields: list[str],
                   conflict_rate: float, missing_rate: float, staleness_rate: float,
                   stale_title_override: str | None = None) -> dict:
    p = PERSONALITIES[provider][kind]
    view: dict[str, dict] = {}
    for f in fields:
        tv = truth.get(f)
        if tv is None or rng.random() < missing_rate + p["missing_extra"]:
            continue
        lo, hi = p["age"]
        age = rng.randint(lo, hi)
        if rng.random() < staleness_rate:
            age = int(age * 2.5) + 90
        # name variants: string differs but normalizes equal -> correct conflict-that-agrees
        if f == "name" and rng.random() < 0.5:
            base = str(tv)
            for suf in ["Inc.", "LLC", "Corporation", "Ltd.", "Co.", "Holdings", "Group", "The "]:
                base = base.replace(f" {suf}", "").replace(suf, "")
            variant = rng.choice(NAME_VARIANTS).format(n=base.strip())
            view[f] = {"value": variant, "age_days": age, "correct": True}
            continue
        wrong = rng.random() < (1 - p["accuracy"]) or rng.random() < conflict_rate * 0.5
        if f == "job_title" and stale_title_override and provider == "alpha":
            view[f] = {"value": stale_title_override, "age_days": max(age, 200), "correct": False}
            continue
        if wrong:
            wv = _wrong_value(rng, f, truth, kind == "contact")
            view[f] = {"value": wv, "age_days": age, "correct": wv == tv}
        else:
            view[f] = {"value": tv, "age_days": age, "correct": True}
    return view


def generate_world(
    seed: int = 42,
    n_companies: int = 150,
    contacts_per_company_avg: float = 2.5,
    conflict_rate: float = 0.25,
    missing_rate: float = 0.12,
    staleness_rate: float = 0.2,
    duplicate_rate: float = 0.06,
    wrong_account_rate: float = 0.04,
    suppressed_rate: float = 0.03,
    invalid_domain_rate: float = 0.05,
    filtered_rate: float = 0.08,
) -> dict:
    rng = random.Random(seed)
    companies: list[dict] = []
    contacts: list[dict] = []
    used_names: set[str] = set()

    for i in range(n_companies):
        while True:
            base = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}"
            if base not in used_names:
                used_names.add(base)
                break
        suffix = rng.choice(LEGAL_SUFFIXES)
        name = f"{base} {suffix}".strip()
        slug = _slug(base)
        domain = f"{slug}.test"
        industry, sub = rng.choice(INDUSTRIES)
        lo, hi = rng.choice(EMPLOYEE_BUCKETS)
        emp = rng.randint(lo, hi)
        city, state, country = rng.choice(CITIES)
        tags: list[str] = []

        r = rng.random()
        if r < invalid_domain_rate:
            tags.append("invalid_domain")
        if rng.random() < suppressed_rate:
            tags.append("suppressed")
        if rng.random() < filtered_rate:
            city, state, country = rng.choice([c for c in CITIES if c[2] in FILTERED_COUNTRIES])
            tags.append("filtered_by_campaign")
        low_value = rng.random() < 0.05
        if low_value:
            emp = rng.randint(1, 3)
            tags.append("low_value")

        truth = {
            "name": name,
            "website": None if "invalid_domain" in tags else f"https://www.{domain}",
            "root_domain": "not a domain!!" if "invalid_domain" in tags else domain,
            "linkedin_url": f"https://www.linkedin.com/company/{slug}-demo",
            "industry": industry,
            "sub_industry": sub,
            "employee_count": emp,
            "employee_range": _range_label(emp),
            "annual_revenue_usd": None if low_value else emp * rng.randint(80_000, 300_000),
            "hq_city": city, "hq_state": state, "hq_country": country,
            "company_type": rng.choice(COMPANY_TYPES),
            "founded_year": rng.randint(1985, 2023),
            "technology_signals": sorted(rng.sample(TECH_SIGNALS, k=rng.randint(2, 5))),
        }
        world_id = f"c{i:04d}"
        views = {
            p: _provider_view(rng, p, "company", truth, COMPANY_FIELDS,
                              conflict_rate, missing_rate, staleness_rate)
            for p in PERSONALITIES
        }
        companies.append({"world_id": world_id, "truth": truth, "tags": tags, "provider_views": views})

        # Duplicates / similar names / subsidiaries reference earlier companies
        if i > 0 and rng.random() < duplicate_rate:
            src = companies[rng.randrange(len(companies) - 1)]
            dup_truth = dict(src["truth"])
            dup_truth["name"] = rng.choice(NAME_VARIANTS).format(n=src["truth"]["name"].split(" Inc")[0])
            dup = {
                "world_id": f"c{i:04d}d",
                "truth": dup_truth,
                "tags": [f"duplicate_of:{src['world_id']}"],
                "provider_views": {
                    p: _provider_view(rng, p, "company", dup_truth, COMPANY_FIELDS,
                                      conflict_rate, missing_rate, staleness_rate)
                    for p in PERSONALITIES
                },
            }
            companies.append(dup)
        elif rng.random() < 0.04 and i > 0:
            src = companies[rng.randrange(len(companies) - 1)]
            sub_name = f"{src['truth']['name'].split(' ')[0]} {rng.choice(NOUNS)} {rng.choice(['Labs', 'Services'])}"  # noqa: E501
            sub_slug = _slug(sub_name)
            sub_truth = dict(truth)
            sub_truth.update({
                "name": sub_name, "root_domain": f"{sub_slug}.test",
                "website": f"https://www.{sub_slug}.test", "company_type": "Subsidiary",
            })
            companies.append({
                "world_id": f"c{i:04d}s",
                "truth": sub_truth,
                "tags": [f"subsidiary_of:{src['world_id']}", f"similar_name_to:{src['world_id']}"],
                "provider_views": {
                    p: _provider_view(rng, p, "company", sub_truth, COMPANY_FIELDS,
                                      conflict_rate, missing_rate, staleness_rate)
                    for p in PERSONALITIES
                },
            })

    # Contacts
    pid = 0
    enrichable = [c for c in companies if "invalid_domain" not in c["tags"]]
    for comp in companies:
        n_contacts = max(0, int(rng.gauss(contacts_per_company_avg, 1.2)))
        if "low_value" in comp["tags"]:
            n_contacts = min(n_contacts, 1)
        for _ in range(n_contacts):
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            title, seniority, dept = rng.choice(TITLES)
            dom = comp["truth"]["root_domain"]
            valid_dom = dom and "!" not in dom
            tags = []
            email: str | None = f"{first.lower()}.{last.lower()}@{dom}" if valid_dom else None
            if rng.random() < missing_rate * 0.6 or email is None:
                email = None
                tags.append("missing_email")
            if rng.random() < suppressed_rate:
                tags.append("suppressed")
            changed_jobs = rng.random() < staleness_rate * 0.5
            previous_title = None
            if changed_jobs:
                tags.append("changed_jobs")
                previous_title = TITLE_CONFUSIONS.get(title, "Sales Manager")
            elif rng.random() < staleness_rate * 0.4:
                tags.append("stale_title")
            truth = {
                "first_name": first, "last_name": last, "full_name": f"{first} {last}",
                "work_email": email, "email_valid": email is not None,
                "job_title": title, "seniority": seniority, "department": dept,
                "country": comp["truth"]["hq_country"],
                "linkedin_url": f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-demo",
            }
            views = {
                p: _provider_view(rng, p, "contact", truth, CONTACT_FIELDS,
                                  conflict_rate, missing_rate, staleness_rate,
                                  stale_title_override=previous_title)
                for p in PERSONALITIES
            }
            company_world_id = comp["world_id"]
            if rng.random() < wrong_account_rate and len(enrichable) > 1:
                tags.append("wrong_account_in_provider")
                other = rng.choice([c for c in enrichable if c["world_id"] != comp["world_id"]])
                for p in views:
                    if rng.random() < 0.5:
                        views[p]["country"] = {
                            "value": other["truth"]["hq_country"], "age_days": rng.randint(30, 200),
                            "correct": other["truth"]["hq_country"] == truth["country"],
                        }
            contacts.append({
                "world_id": f"p{pid:04d}", "company_world_id": company_world_id,
                "truth": truth, "tags": tags, "provider_views": views,
            })
            pid += 1

    return {
        "version": 1,
        "config": {
            "seed": seed, "n_companies": n_companies,
            "contacts_per_company_avg": contacts_per_company_avg,
            "conflict_rate": conflict_rate, "missing_rate": missing_rate,
            "staleness_rate": staleness_rate, "duplicate_rate": duplicate_rate,
            "wrong_account_rate": wrong_account_rate, "suppressed_rate": suppressed_rate,
            "invalid_domain_rate": invalid_domain_rate, "filtered_rate": filtered_rate,
        },
        "companies": companies,
        "contacts": contacts,
    }


def write_world(world: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(world, sort_keys=True, indent=2))


def load_world(path: str) -> dict:
    world = json.loads(Path(path).read_text())
    assert world.get("version") == 1, "unsupported world file version"
    assert "companies" in world and "contacts" in world, "malformed world file"
    return world


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the RelayIQ synthetic world")
    ap.add_argument("--out", default="data/synthetic_world.json")
    ap.add_argument("--companies", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--contacts-per-company", type=float, default=2.5)
    ap.add_argument("--conflict-rate", type=float, default=0.25)
    ap.add_argument("--missing-rate", type=float, default=0.12)
    ap.add_argument("--staleness-rate", type=float, default=0.2)
    ap.add_argument("--duplicate-rate", type=float, default=0.06)
    ap.add_argument("--wrong-account-rate", type=float, default=0.04)
    ap.add_argument("--suppressed-rate", type=float, default=0.03)
    ap.add_argument("--invalid-domain-rate", type=float, default=0.05)
    ap.add_argument("--filtered-rate", type=float, default=0.08)
    args = ap.parse_args()
    world = generate_world(
        seed=args.seed, n_companies=args.companies,
        contacts_per_company_avg=args.contacts_per_company,
        conflict_rate=args.conflict_rate, missing_rate=args.missing_rate,
        staleness_rate=args.staleness_rate, duplicate_rate=args.duplicate_rate,
        wrong_account_rate=args.wrong_account_rate, suppressed_rate=args.suppressed_rate,
        invalid_domain_rate=args.invalid_domain_rate, filtered_rate=args.filtered_rate,
    )
    write_world(world, args.out)
    print(  # noqa: T201 — CLI output
        f"wrote {args.out}: {len(world['companies'])} companies, {len(world['contacts'])} contacts"
    )


if __name__ == "__main__":
    main()
