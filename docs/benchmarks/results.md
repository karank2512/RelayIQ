# RelayIQ cost benchmark (measured on seeded synthetic data)

Generated: 2026-07-12T00:09:16+00:00  ·  seed 42  ·  431 contacts, 15% duplicate submissions

> Simulated providers (deterministic personalities), real RelayIQ control-plane code, quality scored against known synthetic truth.

| Strategy | Provider calls | Cost (credits) | Fill rate | Precision vs truth | True usable leads | Cost / true usable | Review load | Filtered |
|---|---|---|---|---|---|---|---|---|
| naive | 990 | 2886.8 | 0.9221 | 0.682 | 218 | 13.242 | 0 | 0 |
| cache_only | 862 | 2514.6 | 0.9221 | 0.682 | 218 | 11.535 | 0 | 0 |
| filter_cache | 456 | 1318.6 | 0.9221 | 0.682 | 218 | 6.049 | 0 | 235 |
| static_routing | 473 | 987.8 | 0.9317 | 0.773 | 243 | 4.065 | 4 | 235 |
| relayiq_full | 594 | 1097.8 | 0.924 | 0.7836 | 236 | 4.652 | 11 | 235 |
| dynamic_routing | 932 | 1329.8 | 0.9212 | 0.7276 | 224 | 5.937 | 9 | 235 |

**Headline (this run):** full RelayIQ spent 1097.8 credits vs 2886.8 naive — **62% lower spend** — at 0.7836 field precision vs naive's 0.682.

Notes: review-queue records are conservatively excluded from RelayIQ's usable-lead count (human review is not simulated). Provider costs/latencies are simulator parameters, not real vendor pricing.
