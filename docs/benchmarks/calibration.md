# Confidence calibration report (rules-v1)

Generated: 2026-11-12T11:46:57+00:00 · 783 scored fields · seed 42

- **Brier score:** 0.1642
- **Expected Calibration Error:** 0.0905
- **Overall accuracy:** 0.8084 · **mean confidence:** 0.8737

> rules-v1 is a heuristic confidence score, not a calibrated probability. Measured ECE 0.0905: materially miscalibrated on this distribution; the bucket table does not demonstrate useful ranking (AUC not computed). Do not read scores as probabilities; see docs/benchmarks/calibration.md.

```text
confidence bucket | accuracy (# = 5%)         | n
------------------+---------------------------+------
          0.0-0.1 | (empty)                    | 0
          0.1-0.2 | (empty)                    | 0
          0.2-0.3 | (empty)                    | 0
          0.3-0.4 | ############               | 5
          0.4-0.5 | ###########                | 9
          0.5-0.6 | #################          | 13
          0.6-0.7 | ###################        | 14
          0.7-0.8 | ###############            | 46
          0.8-0.9 | ################           | 268
          0.9-1.0 | ################           | 428
```

| bucket | n | mean confidence | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | None | None |
| 0.1-0.2 | 0 | None | None |
| 0.2-0.3 | 0 | None | None |
| 0.3-0.4 | 5 | 0.3808 | 0.6 |
| 0.4-0.5 | 9 | 0.4541 | 0.5556 |
| 0.5-0.6 | 13 | 0.5081 | 0.8462 |
| 0.6-0.7 | 14 | 0.6851 | 0.9286 |
| 0.7-0.8 | 46 | 0.7373 | 0.7391 |
| 0.8-0.9 | 268 | 0.8693 | 0.8172 |
| 0.9-1.0 | 428 | 0.9229 | 0.8131 |

## Constant baseline and Brier decomposition

Synthetic world, synthetic truth: nothing here is customer or production data.

> **IN-SAMPLE prevalence reference: the constant is the observed accuracy of this same synthetic sample, so it uses the outcomes being scored. It is not an independent held-out predictor and not a fair deployable comparator.** The baseline is computed on the same synthetic sample it is compared against (data leakage), so it only shows how much a constant forecast that already knows the base rate would score here.

- Constant baseline confidence = observed accuracy = **0.808429**; baseline Brier = accuracy × (1 − accuracy) = **0.154871**
- rules-v1 raw Brier = **0.164159** (difference vs baseline +0.009288; skill score vs in-sample baseline -0.059971)
- Denominator for every term: N = 783 scored fields; bucket terms are count-weighted by n_k / N; empty buckets are skipped.

Murphy decomposition of the **grouped** forecast (each row's confidence replaced by its bucket mean):

- reliability = (1/N) Σ n_k (conf_k − acc_k)² = **0.010905**
- resolution = (1/N) Σ n_k (acc_k − overall accuracy)² = **0.001614**
- uncertainty = accuracy (1 − accuracy) = **0.154871**
- grouped Brier = reliability − resolution + uncertainty = **0.164162**

This identity is exact for the grouped forecast only. Confidence varies within buckets here, so it does **not** equal the raw Brier: raw Brier 0.164159 = grouped 0.164162 + grouping residual -3e-06 (= within-bin confidence variance 0.000238 − 2 × within-bin covariance 0.000121).

| bucket | n | n_k/N | mean confidence | accuracy | reliability term | resolution term |
|---|---|---|---|---|---|---|
| 0.0-0.1 | 0 | 0.0 | None | None | 0.0 | 0.0 |
| 0.1-0.2 | 0 | 0.0 | None | None | 0.0 | 0.0 |
| 0.2-0.3 | 0 | 0.0 | None | None | 0.0 | 0.0 |
| 0.3-0.4 | 5 | 0.006386 | 0.3808 | 0.6 | 0.000307 | 0.000277 |
| 0.4-0.5 | 9 | 0.011494 | 0.454056 | 0.555556 | 0.000118 | 0.000735 |
| 0.5-0.6 | 13 | 0.016603 | 0.508108 | 0.846154 | 0.001897 | 2.4e-05 |
| 0.6-0.7 | 14 | 0.01788 | 0.685057 | 0.928571 | 0.00106 | 0.000258 |
| 0.7-0.8 | 46 | 0.058748 | 0.737315 | 0.73913 | 0.0 | 0.000282 |
| 0.8-0.9 | 268 | 0.342273 | 0.869307 | 0.817164 | 0.000931 | 2.6e-05 |
| 0.9-1.0 | 428 | 0.546616 | 0.922894 | 0.813084 | 0.006591 | 1.2e-05 |
