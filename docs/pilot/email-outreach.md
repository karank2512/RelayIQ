# Email Outreach — Pilot Recruitment

Same ground rules as the LinkedIn variants: honest, specific, no hype, no invented numbers.
The demo runs entirely on **synthetic data with simulated providers**; recipients never need
to share production data, credentials, or CRM access.

Personalize all `[bracketed]` fields. Keep subject lines plain — no clickbait.

---

## Variant 1 — Problem-first (RevOps / GTM operator)

**Subject:** Question about how you handle enrichment waste

> Hi [Name],
>
> I'm researching a specific problem: how much of a B2B enrichment budget gets burned on
> records that were already enriched, already stale, or come back with conflicting values
> from different providers — and how teams handle that today.
>
> I built RelayIQ, a control plane that sits between workflow tools (Clay, HubSpot) and
> data providers. It makes a pre-spend decision per field (cache/staleness/dedupe/budget
> checks before any provider is called), routes each field to the provider best suited for
> it, reconciles conflicts with human-readable reasoning, and gates CRM writes behind a
> confidence threshold and a review queue.
>
> I'm doing 20-minute interviews with people who run these workflows. I'd like to hear how
> your stack is wired, where spend leaks, and what it would take for you to trust an
> automated confidence score. In return I'll do a free enrichment-cost audit: a structured
> walkthrough of one workflow to map likely waste sources.
>
> To be clear about what this is: a research and portfolio project, currently running
> against simulated providers on synthetic data. I'm not selling anything, and you would
> not share any production data.
>
> Would 20 minutes in the next two weeks work? Happy to fit your calendar.
>
> Thanks,
> [Your name]
> [link to repo or portfolio page]

---

## Variant 2 — Peer/builder angle (GTM engineer / technical operator)

**Subject:** 20 min on enrichment plumbing? (I'll trade architecture notes)

> Hi [Name],
>
> I saw [specific thing: their post / talk / tool they maintain] and figured you've felt
> the sharp edges of enrichment plumbing firsthand.
>
> I spent the last stretch building RelayIQ — an enrichment control plane. The parts I'd
> nerd out with you about:
>
> - Pre-enrichment decision engine: policy, identifier validation, in-flight dedupe,
>   staleness-aware cache reuse, and budget checks run cheapest-first before any credit is
>   spent.
> - Field-level routing: per-field provider selection with transparent scoring (cost,
>   quality priors, provider health, circuit breakers), so every choice is explainable
>   after the fact.
> - Reconciliation: conflicting provider values are grouped by equivalence, weighted by
>   provider prior x freshness x native confidence x format validity, and either
>   auto-accepted, accepted with warning, or routed to human review — with the reasoning
>   stored.
> - CRM gate: per-field write/no-write/require-approval/preserve-CRM decisions, so
>   low-confidence data never silently overwrites a fresh CRM value.
>
> It currently runs against deterministic provider simulators on a synthetic dataset
> (known ground truth), which makes behavior measurable — but I want to check the design
> against real workflows before going further.
>
> Could I get 20 minutes to hear how you've wired this today and where it hurts? I'll walk
> through any part of my architecture you're curious about in exchange. No production data
> involved on either side.
>
> Thanks,
> [Your name]
> [link to repo or portfolio page]

---

## Follow-up (send once, ~5–7 days later, either variant)

**Subject:** Re: [original subject]

> Hi [Name],
>
> Quick nudge on my note from last week — I'm interviewing [RevOps managers / GTM
> engineers / agency founders] about enrichment workflows and waste, 20 minutes, nothing
> to install and no production data involved.
>
> If the timing's bad, two easy outs:
>
> 1. Reply "later" and I'll check back in a quarter.
> 2. If someone else on your team is closer to the enrichment stack, I'd appreciate an
>    intro.
>
> Either way, thanks for reading.
>
> [Your name]

---

## Handling replies

- **"What's the catch?"** — None; it's a research/portfolio project on simulated
  providers. Interviews decide whether it becomes a product.
- **"Can you show it?"** — Yes: the 2-minute demo (see `docs/pilot/demo-script.md`) runs
  live on synthetic data. Offer it at the end of the interview, not as the opener.
- **"Send me a summary instead."** — Send the intake form (`docs/pilot/intake-form.md`)
  and offer async written answers; lower fidelity but better than nothing.
- After each interview, file notes per `docs/pilot/findings-template.md` and follow
  `docs/pilot/consent-and-data-handling.md` for quotes and deletion requests.
