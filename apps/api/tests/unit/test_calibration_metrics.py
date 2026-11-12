"""Constant-baseline + Brier-decomposition metrics (relayiq.benchmark.calibration).

Every expected value is recomputed independently with exact rational arithmetic
(fractions.Fraction) from the raw (confidence, correct) rows - not by calling the
code under test - and the real-pipeline test also recomputes from the committed summary.
"""

import json
import math
import random
from fractions import Fraction
from pathlib import Path

import pytest

from relayiq.benchmark.calibration import (
    baseline_and_decomposition,
    collect_scored_predictions,
    evaluate,
    render_baseline_markdown,
    scored_rows_sha256,
)

TOL = 2e-6  # outputs are rounded to 6 dp


def _bucket(c: Fraction) -> int:
    """Existing report rule on the float value (lo <= c < hi, 1.0 -> last), restated here
    independently. NOT Fraction(c)*10: Fraction(0.3) is just below 3/10 and would misbucket."""
    x = float(c)
    for i in range(10):
        if i / 10 <= x < (i + 1) / 10 or (i == 9 and x == 1.0):
            return i
    raise AssertionError(x)


def exact(pairs):
    """Independent exact reference. Returns Fractions for every metric."""
    n = len(pairs)
    f = [Fraction(c) for c, _ in pairs]
    o = [Fraction(int(ok)) for _, ok in pairs]
    o_bar = sum(o) / n
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(f):
        groups.setdefault(_bucket(c), []).append(i)
    fk = {k: sum(f[i] for i in m) / len(m) for k, m in groups.items()}
    ok = {k: sum(o[i] for i in m) / len(m) for k, m in groups.items()}
    rel = sum(len(m) * (fk[k] - ok[k]) ** 2 for k, m in groups.items()) / n
    res = sum(len(m) * (ok[k] - o_bar) ** 2 for k, m in groups.items()) / n
    unc = o_bar * (1 - o_bar)
    raw = sum((f[i] - o[i]) ** 2 for i in range(n)) / n
    grouped = sum((fk[_bucket(f[i])] - o[i]) ** 2 for i in range(n)) / n
    wvar = sum((f[i] - fk[_bucket(f[i])]) ** 2 for i in range(n)) / n
    wcov = sum((f[i] - fk[_bucket(f[i])]) * (o[i] - ok[_bucket(f[i])]) for i in range(n)) / n
    return {"o_bar": o_bar, "rel": rel, "res": res, "unc": unc, "raw": raw, "grouped": grouped,
            "wvar": wvar, "wcov": wcov, "groups": groups}


def assert_matches_exact(pairs):
    out = baseline_and_decomposition(pairs)
    ex = exact(pairs)
    b, d = out["constant_baseline"], out["decomposition"]
    assert b["constant_confidence"] == pytest.approx(float(ex["o_bar"]), abs=TOL)
    assert b["brier"] == pytest.approx(float(ex["o_bar"] * (1 - ex["o_bar"])), abs=TOL)
    assert b["brier_direct_check"] == pytest.approx(b["brier"], abs=TOL)
    for key, ek in [("reliability", "rel"), ("resolution", "res"), ("uncertainty", "unc"),
                    ("brier_raw", "raw"), ("brier_grouped", "grouped"),
                    ("within_bin_confidence_variance", "wvar"), ("within_bin_covariance", "wcov")]:
        assert d[key] == pytest.approx(float(ex[ek]), abs=TOL), key
    # Murphy is exact for the GROUPED forecast ...
    assert ex["rel"] - ex["res"] + ex["unc"] == ex["grouped"]
    assert d["murphy_grouped_brier"] == pytest.approx(d["brier_grouped"], abs=TOL)
    # ... and raw = grouped + (within-bin variance - 2 * within-bin covariance), also exact
    assert ex["raw"] == ex["grouped"] + ex["wvar"] - 2 * ex["wcov"]
    assert d["grouping_residual"] == pytest.approx(float(ex["raw"] - ex["grouped"]), abs=TOL)
    return out, ex


# ---------------------------------------------------------------- constant baseline

def test_constant_baseline_exact_closed_form():
    pairs = [(0.9, True)] * 4 + [(0.9, False)]  # accuracy exactly 0.8
    out = baseline_and_decomposition(pairs)
    b = out["constant_baseline"]
    assert b["constant_confidence"] == 0.8
    assert b["brier"] == pytest.approx(0.16, abs=1e-12)  # 0.8 * 0.2
    # direct evaluation of the constant forecast 0.8 on the same rows
    direct = sum((0.8 - (1.0 if ok else 0.0)) ** 2 for _, ok in pairs) / len(pairs)
    assert direct == pytest.approx(b["brier"], abs=1e-12)
    assert b["brier_direct_check"] == pytest.approx(b["brier"], abs=1e-12)
    assert b["denominator_N"] == 5
    assert "IN-SAMPLE" in b["label"] and "not a fair deployable comparator" in b["label"]
    # rules-v1 here: (4 * 0.1^2 + 0.9^2) / 5 = 0.85 / 5 = 0.17
    assert b["rules_v1_brier_raw"] == pytest.approx(0.17, abs=1e-12)
    assert b["rules_v1_minus_baseline_brier"] == pytest.approx(0.01, abs=1e-9)
    assert b["brier_skill_score_vs_in_sample_baseline"] == pytest.approx(1 - 0.17 / 0.16, abs=1e-9)


def test_baseline_equals_uncertainty_term():
    rng = random.Random(7)
    pairs = [(rng.random(), rng.random() < 0.7) for _ in range(300)]
    out = baseline_and_decomposition(pairs)
    assert out["constant_baseline"]["brier"] == out["decomposition"]["uncertainty"]


# ---------------------------------------------------------------- decomposition

def test_murphy_equals_raw_when_confidence_constant_within_bins():
    pairs = ([(0.85, True)] * 6 + [(0.85, False)] * 2 + [(0.25, True)] * 1 + [(0.25, False)] * 3
             + [(0.95, True)] * 4)
    out, ex = assert_matches_exact(pairs)
    d = out["decomposition"]
    assert d["confidence_varies_within_bins"] is False
    assert d["within_bin_confidence_variance"] == 0.0 and d["grouping_residual"] == 0.0
    assert d["brier_raw"] == pytest.approx(d["murphy_grouped_brier"], abs=TOL)
    assert ex["raw"] == ex["grouped"]


def test_murphy_does_not_equal_raw_with_variable_confidence_and_mixed_outcomes():
    # two buckets; confidences differ inside each bucket AND outcomes differ inside each bucket,
    # with the higher confidence on the wrong side so the covariance term is non-zero
    pairs = [(0.82, True), (0.88, False), (0.21, False), (0.29, True), (0.84, True), (0.86, True)]
    out, ex = assert_matches_exact(pairs)
    d = out["decomposition"]
    assert d["confidence_varies_within_bins"] is True
    assert d["within_bin_confidence_variance"] > 0
    assert d["within_bin_covariance"] != 0
    assert abs(d["murphy_grouped_brier"] - d["brier_raw"]) > 1e-3  # NOT equal to raw Brier
    assert d["grouping_residual"] == pytest.approx(
        d["within_bin_confidence_variance"] - 2 * d["within_bin_covariance"], abs=TOL)
    # sanity on a hand-computed value: bucket 0.8-0.9 mean conf = (0.82+0.88+0.84+0.86)/4 = 0.85,
    # accuracy 3/4; bucket 0.2-0.3 mean conf 0.25, accuracy 1/2
    rows = {r["bucket"]: r for r in d["by_bucket"]}
    assert rows["0.8-0.9"]["mean_confidence"] == pytest.approx(0.85)
    assert rows["0.8-0.9"]["accuracy"] == 0.75
    assert rows["0.2-0.3"]["mean_confidence"] == pytest.approx(0.25)
    assert rows["0.2-0.3"]["accuracy"] == 0.5


def test_random_datasets_match_exact_reference_and_evaluate_buckets():
    rng = random.Random(20261113)
    for trial in range(60):
        n = rng.randint(1, 120)
        lo = rng.choice([0.0, 0.3, 0.6])
        pairs = [(round(rng.uniform(lo, 1.0), rng.choice([2, 4, 8])), rng.random() < rng.random())
                 for _ in range(n)]
        out, ex = assert_matches_exact(pairs)
        table = evaluate(pairs)["reliability_table"]
        by = out["decomposition"]["by_bucket"]
        assert [r["n"] for r in by] == [t["n"] for t in table], trial
        assert sum(r["n"] for r in by) == n


# ---------------------------------------------------------------- weighting / denominators

def test_count_weighting_with_n_denominator_not_bucket_average():
    # unequal bucket sizes: 1 row in a badly-calibrated bucket, 9 in a well-calibrated one
    pairs = [(0.15, True)] + [(0.95, True)] * 8 + [(0.95, False)]
    out, ex = assert_matches_exact(pairs)
    d = out["decomposition"]
    by = [r for r in d["by_bucket"] if r["n"]]
    assert d["denominator_N"] == 10
    assert sum(r["weight_n_k_over_N"] for r in by) == pytest.approx(1.0, abs=1e-9)
    assert sum(r["reliability_contribution"] for r in by) == pytest.approx(d["reliability"], abs=1e-5)
    assert sum(r["resolution_contribution"] for r in by) == pytest.approx(d["resolution"], abs=1e-5)
    # count-weighted: bucket 0.1-0.2 has weight 0.1, term (0.15-1)^2
    assert by[0]["reliability_contribution"] == pytest.approx(0.1 * (0.15 - 1.0) ** 2, abs=1e-9)
    # an unweighted mean over the 2 non-empty buckets would give a different (wrong) answer
    f9 = (0.95 * 9) / 9
    unweighted = ((0.15 - 1.0) ** 2 + (f9 - 8 / 9) ** 2) / 2
    assert abs(unweighted - d["reliability"]) > 0.05
    assert d["reliability"] == pytest.approx(
        float(Fraction(1, 10) * (Fraction(15, 100) - 1) ** 2
              + Fraction(9, 10) * (Fraction(95, 100) - Fraction(8, 9)) ** 2),
        abs=TOL)


# ---------------------------------------------------------------- empty / degenerate

def test_empty_input_is_all_none_and_does_not_divide_by_zero():
    assert baseline_and_decomposition([]) == {"constant_baseline": None, "decomposition": None}
    assert render_baseline_markdown(evaluate([])) == ""
    assert evaluate([])["brier_score"] is None


def test_empty_buckets_are_skipped_but_listed():
    out = baseline_and_decomposition([(0.55, True), (0.58, False)])
    by = out["decomposition"]["by_bucket"]
    assert len(by) == 10
    empties = [r for r in by if r["n"] == 0]
    assert len(empties) == 9
    assert all(r["mean_confidence"] is None and r["accuracy"] is None
               and r["reliability_contribution"] == 0.0 for r in empties)


@pytest.mark.parametrize("ok", [True, False])
def test_all_true_and_all_false(ok):
    pairs = [(0.9, ok), (0.7, ok), (0.95, ok), (0.41, ok)]
    out, ex = assert_matches_exact(pairs)
    d, b = out["decomposition"], out["constant_baseline"]
    assert d["uncertainty"] == 0.0 and d["resolution"] == 0.0
    assert b["brier"] == 0.0 and b["constant_confidence"] == (1.0 if ok else 0.0)
    assert b["brier_skill_score_vs_in_sample_baseline"] is None  # 0/0 guarded, not NaN
    assert d["reliability"] == pytest.approx(d["brier_grouped"], abs=TOL)


def test_single_row_and_boundary_confidences():
    out = baseline_and_decomposition([(1.0, True)])
    assert out["decomposition"]["by_bucket"][9]["n"] == 1  # 1.0 belongs to the last bucket
    assert out["decomposition"]["brier_raw"] == 0.0
    out = baseline_and_decomposition([(0.0, False), (0.1, True), (0.3, True)])
    assert [r["n"] for r in out["decomposition"]["by_bucket"]][:4] == [1, 1, 0, 1]  # lo-inclusive


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan")])
def test_invalid_confidence_fails_loudly(bad):
    with pytest.raises(ValueError):
        baseline_and_decomposition([(0.5, True), (bad, False)])


def test_no_negative_terms_from_float_error():
    rng = random.Random(3)
    pairs = [(0.9 + rng.random() * 1e-9, True) for _ in range(500)]  # nearly degenerate
    d = baseline_and_decomposition(pairs)["decomposition"]
    assert d["reliability"] >= 0 and d["resolution"] >= 0 and d["uncertainty"] >= 0
    assert d["within_bin_confidence_variance"] >= 0


# ---------------------------------------------------------------- existing keys preserved

def test_evaluate_keeps_existing_keys_and_values():
    pairs = [(0.9, True), (0.8, False), (0.35, True), (0.99, True)]
    rep = evaluate(pairs)
    keys = list(rep)
    assert keys[:6] == ["n_predictions", "brier_score", "expected_calibration_error",
                        "overall_accuracy", "mean_confidence", "reliability_table"]
    assert keys[6:] == ["constant_baseline", "decomposition"]
    brier = sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / 4
    assert rep["brier_score"] == round(brier, 4)
    assert rep["decomposition"]["brier_raw"] == pytest.approx(brier, abs=TOL)


def test_rows_hash_is_order_sensitive_and_deterministic():
    a = [(0.5, True), (0.6, False)]
    assert scored_rows_sha256(a) == scored_rows_sha256(list(a))
    assert scored_rows_sha256(a) != scored_rows_sha256(a[::-1])


# ---------------------------------------------------------------- real synthetic pipeline

COMMITTED = Path(__file__).resolve().parents[4] / "docs" / "benchmarks" / "calibration.json"


@pytest.fixture(scope="module")
def real_pairs():
    return collect_scored_predictions(seed=42, n_companies=250)


def test_real_pipeline_is_deterministic(real_pairs):
    again = collect_scored_predictions(seed=42, n_companies=250)
    assert scored_rows_sha256(again) == scored_rows_sha256(real_pairs)


def test_real_pipeline_matches_exact_reference_and_frozen_numbers(real_pairs):
    out, ex = assert_matches_exact(real_pairs)
    rep = evaluate(real_pairs)
    # frozen numbers from the published seed-42 / 250-company synthetic run
    assert rep["n_predictions"] == 783
    assert rep["brier_score"] == 0.1642
    assert rep["expected_calibration_error"] == 0.0905
    assert rep["overall_accuracy"] == 0.8084
    assert rep["mean_confidence"] == 0.8737
    assert out["decomposition"]["confidence_varies_within_bins"] is True
    assert out["decomposition"]["brier_raw"] != out["decomposition"]["murphy_grouped_brier"]
    # the in-sample constant baseline beats the raw rules-v1 Brier on this sample
    assert out["constant_baseline"]["brier"] < out["constant_baseline"]["rules_v1_brier_raw"]


@pytest.mark.skipif(not COMMITTED.exists(), reason="docs/benchmarks/calibration.json not present")
def test_recompute_from_committed_summary_table(real_pairs):
    """Independent route: rebuild uncertainty/resolution/reliability/grouped Brier from the
    (4 dp rounded) reliability_table in the committed summary and compare to the raw rows."""
    doc = json.loads(COMMITTED.read_text())
    table = [t for t in doc["reliability_table"] if t["n"]]
    n = sum(t["n"] for t in table)
    assert n == doc["n_predictions"] == len(real_pairs)
    o_bar = sum(t["n"] * t["accuracy"] for t in table) / n
    unc = o_bar * (1 - o_bar)
    res = sum(t["n"] * (t["accuracy"] - o_bar) ** 2 for t in table) / n
    rel = sum(t["n"] * (t["mean_confidence"] - t["accuracy"]) ** 2 for t in table) / n
    d = baseline_and_decomposition(real_pairs)["decomposition"]
    assert o_bar == pytest.approx(doc["overall_accuracy"], abs=2e-4)
    assert unc == pytest.approx(d["uncertainty"], abs=5e-4)
    assert res == pytest.approx(d["resolution"], abs=5e-4)
    assert rel == pytest.approx(d["reliability"], abs=5e-4)
    assert rel - res + unc == pytest.approx(d["brier_grouped"], abs=1e-3)
    assert math.isfinite(d["grouping_residual"])
    # if the committed file already carries the new block, it must agree with the raw rows
    if "decomposition" in doc:
        for k in ("brier_raw", "brier_grouped", "reliability", "resolution", "uncertainty",
                  "grouping_residual"):
            assert doc["decomposition"][k] == pytest.approx(d[k], abs=TOL), k
        assert doc["constant_baseline"]["brier"] == pytest.approx(
            baseline_and_decomposition(real_pairs)["constant_baseline"]["brier"], abs=TOL)
        assert doc["run_parameters"]["scored_rows_sha256"] == scored_rows_sha256(real_pairs)


def test_markdown_labels_leakage_and_does_not_claim_murphy_equals_raw(real_pairs):
    md = render_baseline_markdown(evaluate(real_pairs))
    assert "IN-SAMPLE" in md and "data leakage" in md and "not a fair deployable comparator" in md
    assert "does **not** equal the raw Brier" in md
    assert "customer or production data" in md
