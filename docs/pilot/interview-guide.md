# Pilot Interview Guide — 20-Minute Workflow Interview

Audience: RevOps managers, GTM engineers, agency founders who run B2B enrichment.
Goal: understand the real workflow and its waste sources; validate (or invalidate) the
assumptions RelayIQ is built on. This is a discovery interview, **not** a demo or pitch.

Before starting, cover the consent points in `docs/pilot/consent-and-data-handling.md`
(notes, optional recording, quote permission, deletion on request).

Timeboxes are guidance; follow the interesting thread but protect the last 5 minutes
(trust + gating questions are the most valuable and most often skipped).

---

## 0. Framing (1 min)

> "I'm researching how enrichment workflows actually run and where budgets leak. I built a
> prototype control plane, but today I want your workflow, not my demo. Nothing you say is
> shared with names attached unless you explicitly approve a quote. OK to take notes?"

## 1. Current stack (4 min)

- Walk me through your enrichment flow end to end: where does a record enter, what
  happens, where does it land?
- Which tools are involved? (Clay? HubSpot/Salesforce? A homegrown pipeline? Zapier/Make?)
- Which data providers do you use? (Apollo, Clearbit/HubSpot Breeze, ZoomInfo,
  People Data Labs, Prospeo, FullEnrich, waterfalls inside Clay, ...)
- Who owns this? Who gets paged / blamed when the data is wrong?

Listen for: waterfall setups, manual CSV hops, provider count, one-owner-vs-team.

## 2. Spend and waste (4 min)

- Roughly what do you spend per month on enrichment (credits or dollars — ranges fine)?
- What fraction of that spend do you *suspect* is wasted? On what?
  - Re-enriching records you already paid for?
  - Enriching records that were never usable (bad email, out-of-ICP, suppressed)?
  - Buying stale data (title changed months ago, provider still sells the old one)?
- Have you ever measured this, or is it a gut number? What would you need to measure it?
- Do you set budgets per campaign/client? What happens when one blows through its cap —
  do you find out before or after?

Listen for: whether waste is measured vs. felt; whether budget overruns are detected
proactively; per-client cost attribution (agencies).

## 3. Duplicates and conflicts (4 min)

- When the same person/company enters twice (different lists, different campaigns), what
  happens today? Who catches it and when?
- When two providers disagree — say, two different job titles — what does your stack do?
  What do *you* do?
- Have you ever traced a bad CRM value back to its source? How long did that take?
- What's your worst "bad data made it to sales / the client" story?

Listen for: last-write-wins behavior, manual spot-checking, absence of lineage, cost of a
bad-data incident (time, trust, churn).

## 4. Review workflows (3 min)

- Is there any human review step between enrichment and the CRM today? Who does it, and
  how do they decide?
- If there's no review step: what class of records *would* you want a human to look at,
  if it were cheap to route them?
- When a reviewer (or anyone) fixes a value, is that fix ever overwritten later by the
  next enrichment run?

Listen for: review is usually implicit/ad hoc; fixes being clobbered by re-enrichment is
a known rage point — probe it.

## 5. Trust in automated confidence (3 min)

- If a tool attached a confidence score to each enriched field, what would make you
  actually trust it — versus ignore it?
  - Seeing the components (freshness, provider agreement, format checks, review history)?
  - A track record ("fields we scored above 0.8 were right N% of the time")?
  - The ability to override it and have the override stick?
- At what confidence would you let a value flow through untouched? Where's the line for
  "must be reviewed"?

Listen for: whether explainability or measured calibration matters more to them; the
threshold instinct (this directly parameterizes RelayIQ's `min_confidence` gate).

## 6. Gating CRM writes (2 min)

- Would you let a tool *block* enrichment data from writing to your CRM when confidence is
  low or a conflict is unresolved? Or is any blocking unacceptable?
- Middle grounds: writing to a secondary/"suggested" property instead of the main field;
  queueing for approval. Which of those would fly in your org?
- Who would need to sign off on giving a tool CRM write access at all?

Listen for: appetite for hard gating vs. suggest-only mode; procurement/security friction.

## 7. Wrap-up (1 min)

- If you could fix only one of the things we discussed, which one?
- May I follow up with a 2-minute demo (synthetic data) once I've digested this?
- Anyone else you think I should talk to?
- Confirm consent: notes kept, quotes only with approval, deletion on request.

---

## After the interview

1. Within 24h, transcribe notes into a copy of `docs/pilot/findings-template.md`.
2. Mark every number they gave as **reported** (their claim), never as measured.
3. Send a two-line thank-you with the deletion-request contact.
