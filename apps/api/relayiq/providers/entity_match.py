"""Entity checks for the public-source adapters.

The word lists are heuristics tuned on a small sample of company names; they are not known to generalise.

A record is accepted only if (1) its name equals the query after dropping legal and generic words and
(2) for Wikidata, its one-line description reads like an organisation and not a place, person, game,
album and so on. No description means no answer.
"""

import re

_LEGAL = {
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
    "incorporated",
    "sa",
    "ag",
    "gmbh",
    "pbc",
}
_GENERIC = {
    "labs",
    "lab",
    "technologies",
    "technology",
    "software",
    "systems",
    "communications",
    "group",
    "platforms",
    "platform",
    "ai",
    "hq",
    "global",
    "usa",
    "us",
    "international",
    "services",
}

_ORG_WORDS = re.compile(
    r"\b(company|corporation|business|software|startup|start-up|firm|enterprise|provider|developer|platform|"
    r"bank|manufacturer|retailer|vendor|services?|cloud|technology|technologies|e-commerce|fintech|"
    r"organi[sz]ation|conglomerate|studio)\b",
    re.I,
)
_NOT_ORG = re.compile(
    r"\b(video game|game|album|village|given name|family name|surname|band|film|song|genus|comune|parish|"
    r"municipality|commune|species|town|river|character|novel|album|single|episode|physical quantity)\b",
    re.I,
)


def gnorm(s: str | None, *, generic: bool = True) -> str:
    drop = _LEGAL | (_GENERIC if generic else set())
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t not in drop]
    return " ".join(toks)


def name_matches(query: str, candidate_name: str | None) -> bool:
    q = gnorm(query)
    return bool(q) and q == gnorm(candidate_name)


def org_like(description: str | None) -> bool:
    d = description or ""
    return bool(_ORG_WORDS.search(d)) and not _NOT_ORG.search(d)
