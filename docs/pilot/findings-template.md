# Pilot Findings — [Pilot ID / Company Alias]

> **TEMPLATE — NO PILOTS CONDUCTED YET.**
> This file is the blank per-pilot template. Copy it to
> `docs/pilot/findings/<pilot-id>.md` for each pilot. Until filled copies exist, RelayIQ
> has **zero** pilot findings, and nothing in this template may be cited as evidence.

## Metadata

| Field | Value |
| --- | --- |
| Pilot ID | e.g. P-001 |
| Date(s) | |
| Format | interview only / interview + cost audit / interview + demo |
| Participant role | RevOps / GTM engineer / agency founder / other |
| Company segment | size, industry, motion (outbound, PLG, agency) — keep non-identifying |
| Consent | notes: yes/no · recording: yes/no · quotes: per-quote approval on file? |
| Deletion requested? | no / yes (date actioned) |

## 1. Context

Two or three sentences: who they are, what their GTM motion is, why enrichment matters to
them. Keep it non-identifying unless they approved attribution.

## 2. Workflow map

Describe (or diagram) the flow as they run it today:

```
[lead source] -> [tool] -> [enrichment step(s) / providers] -> [dedupe? review?] -> [CRM]
```

- Tools:
- Providers (and waterfall order, if any):
- Human touchpoints:
- Failure/repair loops (what happens when data is wrong):

## 3. Waste — measured vs. reported

**Keep these two tables separate. Never merge them.**

### 3a. Reported (their claims, unverified)

| Claim | Their number/words | Source (who said it, when) |
| --- | --- | --- |
| Monthly enrichment spend | | |
| Estimated waste share | | |
| Duplicate-enrichment frequency | | |
| Stale-data incidents | | |

### 3b. Measured (only if a cost audit ran and produced artifacts)

| Metric | Value | How it was measured (artifact/query) |
| --- | --- | --- |
| | | |

If no audit was performed, write: **"No measurements taken — reported figures only."**

## 4. Conflict & dedupe handling today

- What happens when providers disagree:
- What happens on duplicate entry:
- Lineage/traceability of a bad value:

## 5. Review & trust

- Existing review step (who/when/criteria):
- What would make them trust an automated confidence score:
- Confidence threshold instinct (auto-pass vs. must-review):
- Willingness to gate CRM writes: hard block / secondary property / approval queue / none

## 6. Quotes

Only quotes with the participant's written approval of exact wording **and** attribution
level. One row per quote.

| Quote (verbatim) | Attribution approved as | Approval on file (link/date) |
| --- | --- | --- |
| | | |

## 7. Implications for RelayIQ

- Assumptions validated:
- Assumptions challenged or invalidated:
- Feature/roadmap impact (link to `docs/roadmap.md` items):

## 8. Follow-ups

- [ ] Thank-you sent (with deletion contact)
- [ ] Demo offered / scheduled / done
- [ ] Feedback questionnaire sent (`docs/pilot/feedback-questionnaire.md`)
- [ ] Quote approvals collected (if any quotes kept)
- [ ] Findings reviewed against consent doc before any external use
