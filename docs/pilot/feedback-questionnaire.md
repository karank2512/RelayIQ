# Post-Demo Feedback Questionnaire

Send within 24 hours of a RelayIQ demo (the 2-minute recording or a live walkthrough).
Target completion time: under 8 minutes. 12 questions: 7 Likert, 5 open.

All Likert items use the same 1–5 scale:
**1 = strongly disagree · 2 = disagree · 3 = neutral · 4 = agree · 5 = strongly agree.**

Form intro text (paste verbatim):

> You just saw RelayIQ running on synthetic data with simulated providers. These 12
> questions calibrate what's worth building next. Blunt answers are the useful ones —
> "this solves nothing for me" is a valid and valuable response. Answers are handled per
> the consent policy you already saw: anonymized, aggregated, deletable on request.

---

## A. Problem fit

**Q1 (Likert).** The problems RelayIQ targets (duplicate spend, stale data, provider
conflicts, unguarded CRM writes) are real problems in my workflow.

**Q2 (Likert).** At least one of those problems costs my team meaningful money or time
every month.

**Q3 (open).** Which single moment in the demo felt most relevant to your workflow — and
which felt least relevant?

## B. Comprehension & trust

**Q4 (Likert).** I understood *why* the system made each decision it showed (routing
choice, conflict resolution, review requirement, CRM gate outcome).

**Q5 (Likert).** The confidence score, with its visible components, is something I could
imagine trusting for automated decisions after seeing a track record.

**Q6 (open).** What evidence would you need before letting a confidence threshold
auto-approve writes into your CRM? (e.g., measured precision at a threshold, a trial
period in suggest-only mode, per-field control, something else.)

## C. Workflow fit

**Q7 (Likert).** The review queue (accept / pick an observation / correct / reject, with
reversible decisions) matches how my team would actually want to handle uncertain data.

**Q8 (Likert).** Gating CRM writes per field (write / secondary property / require
approval / preserve existing value) would be acceptable in my org.

**Q9 (open).** Where would RelayIQ have to sit in your stack to be usable — behind Clay,
behind your CRM, as an API you call from scripts — and what would break if it sat there?

## D. Value & next steps

**Q10 (Likert).** If the measured benchmarks (cost saved vs. a naive baseline, accuracy of
the confidence score) come back strong, I'd consider piloting this on a real workflow.

**Q11 (open).** What's missing that would block you from piloting it regardless of
benchmark results? (Security review, Salesforce support, SOC 2, pricing, team buy-in...)

**Q12 (open).** If you had to describe RelayIQ to a colleague in one sentence, what would
you say? (This tells us whether the positioning landed.)

---

## Scoring & use

- Tabulate Likert medians per question across respondents; n will be small, so report
  counts, not percentages.
- Q12 answers are the positioning test: if descriptions don't cluster, the story isn't
  landing.
- File each response with its pilot's findings copy (see `findings-template.md`); quote
  rules from `consent-and-data-handling.md` apply to open-text answers too.
