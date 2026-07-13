# LinkedIn Outreach — Pilot Recruitment

Purpose: recruit 20-minute workflow interviews (plus an optional free enrichment-cost audit)
with people who run or build B2B data enrichment workflows.

Ground rules for every message:

- No hype, no fake urgency, no invented numbers.
- Be explicit that RelayIQ currently runs against **simulated providers and synthetic data**
  — no production data is needed or wanted for a demo.
- The ask is small and specific: 20 minutes, their workflow, their waste sources.
- Never claim customers, revenue, or results we don't have.

Personalize the `[bracketed]` parts before sending. Keep connection notes under LinkedIn's
300-character limit; the longer text goes in the first message after they accept.

---

## Variant A — RevOps Manager

**Connection note (<300 chars):**

> Hi [Name] — I build tooling for enrichment workflows and I'm interviewing RevOps folks
> about where enrichment budgets actually leak (dupes, stale data, conflicting providers).
> 20 min, I share everything I learn back. Open to it?

**Follow-up message after accept:**

> Thanks for connecting, [Name].
>
> Short version: I built RelayIQ, a control plane that sits between tools like Clay/HubSpot
> and data providers. It decides per-field whether to spend at all (cache, staleness,
> dedupe, budget checks), reconciles conflicting provider answers with visible reasoning,
> and gates what's allowed to write into the CRM.
>
> I'm doing 20-minute interviews with RevOps managers to pressure-test the problem, not to
> pitch. I'd ask about your stack, where enrichment spend gets wasted, and how you handle
> provider conflicts today. If useful, I'll also do a free enrichment-cost audit of your
> workflow (a structured walkthrough — you don't share any production data; the demo runs
> entirely on synthetic data).
>
> Would a 20-minute call in the next couple of weeks work?

---

## Variant B — GTM Engineer (Madison / UW-alumni angle)

**Connection note (<300 chars):**

> Hi [Name] — fellow [UW-Madison / Madison] person here. I built an enrichment
> orchestration layer (field-level provider routing, conflict reconciliation, CRM write
> gating) and I'm interviewing GTM engineers about their setups. 20 min? I'll trade notes.

**Follow-up message after accept:**

> Appreciate the accept, [Name] — always good to find [Madison / UW] people working in GTM.
>
> I've been building RelayIQ: a control plane between Clay-style workflows and enrichment
> providers. Concretely: pre-spend decisions (don't re-buy what's cached or fresh),
> per-field provider routing with explainable scoring, weighted reconciliation when
> providers disagree, a confidence score with visible components, and a gate that decides
> field-by-field what may write to the CRM.
>
> I'm interviewing GTM engineers for 20 minutes about how they wire this up today —
> waterfalls, dedupe hacks, conflict handling — and what they'd need to trust an automated
> confidence score. Not a sales call; happy to walk through my architecture in exchange.
> The demo uses only synthetic data, so there's nothing for you to share or connect.
>
> Any chance you have 20 minutes in the next two weeks?

---

## Variant C — Agency Founder (outbound / lead-gen agency)

**Connection note (<300 chars):**

> Hi [Name] — I'm researching how lead-gen agencies manage enrichment costs across client
> campaigns (per-campaign budgets, provider waterfalls, bad-data disputes). Interviewing
> founders for 20 min; offering a free cost audit of one workflow in return. Interested?

**Follow-up message after accept:**

> Thanks, [Name].
>
> Context: I built RelayIQ, an enrichment control plane. For an agency the relevant parts
> are per-campaign hard/soft budgets enforced atomically (concurrent jobs can't jointly
> blow a cap), a cost ledger that attributes every credit to a campaign/provider/field, and
> per-field routing so you buy titles from the provider that's good at titles and
> firmographics from the one that's good at those.
>
> I'm interviewing agency founders for 20 minutes about how enrichment spend is budgeted
> and where it leaks across clients. In return I'll do a free enrichment-cost audit: a
> structured walkthrough of one of your workflows to map waste sources. No production data
> changes hands — my demo environment is entirely synthetic and there is nothing to
> install or connect.
>
> Would 20 minutes some time in the next two weeks work for you?

---

## Logistics

- Scheduling: offer 2–3 concrete slots or a scheduling link; keep to 20 minutes.
- If they decline: thank them, ask if they know someone closer to the problem.
- If they ask "what are you selling?": nothing yet — this is a research/portfolio project
  with simulated providers; interviews shape whether and what to productize.
- After each interview: record notes into `docs/pilot/findings-template.md` (one copy per
  pilot) and follow the consent rules in `docs/pilot/consent-and-data-handling.md`.
