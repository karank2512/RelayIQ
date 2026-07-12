# Confidence calibration report (rules-v1)

Generated: 2026-07-12T00:09:35+00:00 · 783 scored fields · seed 42

- **Brier score:** 0.1642
- **Expected Calibration Error:** 0.0905
- **Overall accuracy:** 0.8084 · **mean confidence:** 0.8737

> rules-v1 is a heuristic confidence score, not a calibrated probability. Measured ECE 0.0905: materially miscalibrated on this distribution — treat scores as a ranking signal, not a probability; see docs/benchmarks/calibration.md.

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
