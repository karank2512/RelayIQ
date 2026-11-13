# License recommendation and adoption

**Status: MIT adopted.** The repository owner chose MIT, and the [`LICENSE`](../LICENSE) file
at the repository root (Copyright (c) 2026 Karan Kapur) is the governing text. This page is
background on the choice. It is not a legal document and not legal advice.

This page was first written as a recommendation, before the repository had a license. At that
point no `LICENSE` file existed and the choice was deliberately left to the owner. The
reasoning below is that original recommendation, kept for context; where it speaks of what
the owner could do, read it as written before adoption.

**Scope.** The MIT grant covers this repository's original source code and synthetic
fixtures. It does not cover third-party dependencies, which keep their own licenses, or any
material separately credited to someone else.

The original recommendation was MIT, for the reasons that follow.

## Why MIT fits this project

RelayIQ's current purpose is portfolio and pilot credibility: the value of the repo is
that engineers, interviewers, and prospective pilot users can read it, run
`make benchmark`, and verify the claims. MIT maximizes exactly that:

- **Zero friction for evaluation.** Companies with license-compliance gates (most
  enterprises a RevOps tool would target) can clone, run, and even internally modify an
  MIT project without legal review. AGPL code frequently cannot cross that gate at all.
- **Signals confidence, not protection.** The measured results — 3.3× cost-per-usable-lead
  improvement on the seeded synthetic benchmark — are the moat for a portfolio project;
  the code being copyable is a feature, not a risk, at this stage. There is no revenue to
  protect yet.
- **Compatible with everything.** MIT code can later be relicensed, dual-licensed, or
  embedded in a commercial product by the copyright holder. Starting permissive keeps
  every future door open; starting restrictive and loosening later is also possible, but
  buys nothing today.
- **Ecosystem norms.** The stack's neighbors (FastAPI, SQLAlchemy, React, Celery) are
  MIT/BSD/Apache; contributors expect a permissive license on a project like this.

## Tradeoffs vs the alternatives

### BUSL-1.1 (Business Source License)

- **What it buys:** protection against a cloud/SaaS vendor offering hosted RelayIQ before
  the owner can — source-available, converts to an open license after a change date
  (typically 4 years). This is the "MariaDB/HashiCorp" posture.
- **What it costs here:** BUSL is not OSI-approved open source; many companies' policies
  treat it as proprietary, which directly undercuts the pilot-recruitment goal ("run it
  against your own assumptions"). It also complicates casual contribution.
- **When it would become right:** if RelayIQ becomes a commercial product with paying
  customers and a hosted offering worth protecting. That is a future decision — and since
  the owner holds copyright on all code to date, relicensing later remains possible.

### AGPL-3.0

- **What it buys:** any party running a modified RelayIQ as a network service must publish
  their modifications — the strongest copyleft for server-side software.
- **What it costs here:** the chilling effect is the problem. Significant prospective
  users (agencies, RevOps teams inside larger companies) have blanket AGPL bans, so the
  people this project most wants to evaluate it couldn't. AGPL also effectively requires
  a CLA if a commercial dual-license is ever intended, adding process weight a solo
  project doesn't need.
- **When it would be right:** if the primary goal were guaranteeing the code and all
  derivatives stay open, above adoption and career value.

### Apache-2.0 (honorable mention)

The closest competitor to MIT: adds an explicit patent grant and contribution terms,
at the cost of a longer license and NOTICE-file mechanics. Reasonable to choose instead
of MIT if patent posture matters; for a portfolio-stage project the practical difference
is small. MIT is recommended for simplicity.

## What adoption changed

The recommendation listed three follow-up steps. They were carried out as follows:

1. A `LICENSE` file with the standard MIT text and the copyright line
   `Copyright (c) 2026 Karan Kapur` was added at the repository root.
2. `apps/api/pyproject.toml` declares `license = "MIT"` (the SPDX string form; the build
   requirement moved to `setuptools>=77`, the first release that accepts it), and
   `apps/dashboard/package.json` declares `"license": "MIT"`.
3. The README has a License section. No per-file headers were added; MIT does not need them.

One caveat regardless of license: the license covers the **code**. The honesty rules in
the README (measured-vs-simulated labeling, no invented numbers) are conventions of this
repository, and forks are under no legal obligation to keep them — one more reason the
canonical benchmark reports live in-repo where they can be regenerated and checked.
