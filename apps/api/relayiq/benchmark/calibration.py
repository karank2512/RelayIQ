"""Confidence-calibration evaluation against synthetic reviewed truth.

Runs the full-pipeline strategy, keeps every accepted field's confidence score and its
correctness vs world truth, then reports: reliability table (10 buckets), Brier score,
Expected Calibration Error (ECE), and an ASCII reliability diagram.

The rules-v1 score is a heuristic CONFIDENCE SCORE, not a calibrated probability — this
evaluation measures exactly how far off it is, and the report says so.

Usage: python -m relayiq.benchmark.calibration --out ../../docs/benchmarks/calibration.json
"""

import argparse
import hashlib
import json
import math
import platform
from datetime import UTC, datetime
from pathlib import Path

from relayiq.benchmark.runner import (
    CONTACT_FIELDS,
    PROVIDER_PRIORS,
    STATIC_ROUTES,
    StrategyResult,
    _call,
    _is_prefiltered,
    _mk_providers,
    _obs_age,
    _obs_from_call,
    _thresholds,
    _truth_correct,
)
from relayiq.engines.confidence import FieldConfidenceInput, score_field
from relayiq.engines.reconciliation import reconcile_field
from relayiq.enums import ReconciliationOutcome
from relayiq.seed.worldgen import generate_world, write_world
from relayiq.services.staleness import freshness_factor


def collect_scored_predictions(seed: int = 42, n_companies: int = 250) -> list[tuple[float, bool]]:
    """(confidence, correct) pairs for every field accepted by the pipeline."""
    import tempfile

    world = generate_world(seed=seed, n_companies=n_companies)
    world_path = str(Path(tempfile.mkdtemp(prefix="riq-cal-")) / "world.json")
    write_world(world, world_path)
    companies = {c["world_id"]: c for c in world["companies"]}
    providers = _mk_providers(world_path, seed)
    sink = StrategyResult("calibration", "")
    pairs: list[tuple[float, bool]] = []

    for contact in world["contacts"]:
        company = companies[contact["company_world_id"]]
        if _is_prefiltered(contact, company):
            continue
        plan: dict[str, list[str]] = {}
        for f in CONTACT_FIELDS:
            plan.setdefault(STATIC_ROUTES[f][0], []).append(f)
        calls = {pk: _call(providers[pk], contact, fields, sink) for pk, fields in plan.items()}
        obs_by_field: dict[str, list] = {}
        for pk, fields in plan.items():
            for f in fields:
                obs = _obs_from_call(contact["world_id"], calls[pk], f)
                if obs is not None:
                    obs_by_field.setdefault(f, []).append(obs)
        # staleness cross-check identical to the full strategy
        for f in {"job_title"} & set(obs_by_field):
            primary = obs_by_field[f][0]
            age = _obs_age(primary)
            if age > _thresholds(f).fresh_days:
                other = [p for p in STATIC_ROUTES[f] if p != primary.provider_key]
                if other:
                    r2 = _call(providers[other[0]], contact, [f], sink)
                    obs2 = _obs_from_call(contact["world_id"], r2, f)
                    if obs2 is not None:
                        obs_by_field[f].append(obs2)

        for f, obs_list in obs_by_field.items():
            recon = reconcile_field("contact", f, obs_list,
                                    provider_priors=PROVIDER_PRIORS, thresholds=_thresholds(f))
            if recon.outcome not in (ReconciliationOutcome.AUTO_ACCEPT,
                                     ReconciliationOutcome.ACCEPT_WITH_WARNING) or not recon.chosen:
                continue
            chosen = recon.chosen
            age = _obs_age(chosen)
            conf = score_field(FieldConfidenceInput(
                provider_reliability_prior=PROVIDER_PRIORS.get(chosen.provider_key, 0.8),
                field_quality_prior=0.85,
                freshness_factor=freshness_factor(age, _thresholds(f)),
                agreement=recon.agreement,
                format_valid=True,
                provider_native_confidence=chosen.provider_confidence,
                conflict_severity=recon.conflict_severity,
            )).score
            correct = _truth_correct(contact, f, chosen.normalized_value or chosen.raw_value)
            pairs.append((conf, correct))
    return pairs


def evaluate(pairs: list[tuple[float, bool]]) -> dict:
    n = len(pairs)
    brier = sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / n if n else None
    buckets = []
    ece = 0.0
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        inb = [(c, ok) for c, ok in pairs if lo <= c < hi or (i == 9 and c == 1.0)]
        if not inb:
            buckets.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": 0, "mean_confidence": None,
                            "accuracy": None})
            continue
        mean_c = sum(c for c, _ in inb) / len(inb)
        acc = sum(1 for _, ok in inb if ok) / len(inb)
        ece += (len(inb) / n) * abs(acc - mean_c)
        buckets.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": len(inb),
                        "mean_confidence": round(mean_c, 4), "accuracy": round(acc, 4)})
    return {
        "n_predictions": n,
        "brier_score": round(brier, 4) if brier is not None else None,
        "expected_calibration_error": round(ece, 4) if n else None,
        "overall_accuracy": round(sum(1 for _, ok in pairs if ok) / n, 4) if n else None,
        "mean_confidence": round(sum(c for c, _ in pairs) / n, 4) if n else None,
        "reliability_table": buckets,
        **baseline_and_decomposition(pairs),
    }


N_BUCKETS = 10
_DECOMP_DIGITS = 6  # new keys use 6 dp so the identities below are checkable to ~1e-6


def _bucket_index(c: float) -> int:
    """Bucket index using EXACTLY the membership rule of `evaluate` (lo <= c < hi; 1.0 -> last)."""
    for i in range(N_BUCKETS):
        lo, hi = i / 10, (i + 1) / 10
        if lo <= c < hi or (i == N_BUCKETS - 1 and c == 1.0):
            return i
    raise ValueError(f"confidence {c!r} is outside [0, 1]")


def _r(x: float) -> float:
    return round(x, _DECOMP_DIGITS)


def scored_rows_sha256(pairs: list[tuple[float, bool]]) -> str:
    """Deterministic fingerprint of the raw (confidence, correct) rows, in order."""
    h = hashlib.sha256()
    for c, ok in pairs:
        h.update(f"{c!r},{int(bool(ok))}\n".encode())
    return h.hexdigest()


def baseline_and_decomposition(pairs: list[tuple[float, bool]]) -> dict:
    """Constant in-sample baseline + Brier decomposition. All sums use math.fsum.

    Notation: N = len(pairs) (the single denominator for every term below), o_i in {0,1},
    f_i = confidence, o_bar = overall accuracy; bucket k has n_k rows, mean confidence f_k,
    accuracy o_k (empty buckets have n_k = 0 and contribute nothing).

      brier_raw      = (1/N) sum_i (f_i - o_i)^2                  [= existing `brier_score`]
      uncertainty    = o_bar (1 - o_bar)
      reliability    = (1/N) sum_k n_k (f_k - o_k)^2
      resolution     = (1/N) sum_k n_k (o_k - o_bar)^2
      brier_grouped  = (1/N) sum_i (f_k(i) - o_i)^2   (every row forecast replaced by its bucket
                       mean confidence)  == reliability - resolution + uncertainty  (Murphy, exact)
      within_bin_confidence_variance = (1/N) sum_i (f_i - f_k(i))^2
      within_bin_covariance          = (1/N) sum_i (f_i - f_k(i)) (o_i - o_k(i))
      grouping_residual = brier_raw - brier_grouped
                        = within_bin_confidence_variance - 2 * within_bin_covariance  (exact)

    Murphy's identity holds for the GROUPED forecast, not for the raw-score Brier unless
    confidence is constant within every bucket. The constant baseline f = o_bar has
    Brier = o_bar (1 - o_bar) = uncertainty; it uses the outcomes of the same sample.
    """
    n = len(pairs)
    if n == 0:
        return {"constant_baseline": None, "decomposition": None}
    idx = [_bucket_index(c) for c, _ in pairs]  # raises on NaN / out-of-range
    o = [1.0 if ok else 0.0 for _, ok in pairs]
    f = [c for c, _ in pairs]
    o_bar = math.fsum(o) / n
    brier_raw = math.fsum((fi - oi) ** 2 for fi, oi in zip(f, o, strict=True)) / n
    unc = o_bar * (1.0 - o_bar)
    baseline_brier_direct = math.fsum((o_bar - oi) ** 2 for oi in o) / n  # cross-check of the closed form

    rows = []
    rel_terms, res_terms, wv_terms, wc_terms, grouped_terms = [], [], [], [], []
    for k in range(N_BUCKETS):
        members = [i for i, b in enumerate(idx) if b == k]
        nk = len(members)
        lo, hi = k / 10, (k + 1) / 10
        if nk == 0:
            rows.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": 0, "weight_n_k_over_N": 0.0,
                         "mean_confidence": None, "accuracy": None, "confidence_min": None,
                         "confidence_max": None, "reliability_contribution": 0.0,
                         "resolution_contribution": 0.0})
            continue
        fk = math.fsum(f[i] for i in members) / nk
        ok_ = math.fsum(o[i] for i in members) / nk
        rel_terms.append(nk * (fk - ok_) ** 2)
        res_terms.append(nk * (ok_ - o_bar) ** 2)
        wv_terms.extend((f[i] - fk) ** 2 for i in members)
        wc_terms.extend((f[i] - fk) * (o[i] - ok_) for i in members)
        grouped_terms.extend((fk - o[i]) ** 2 for i in members)
        rows.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": nk, "weight_n_k_over_N": _r(nk / n),
                     "mean_confidence": _r(fk), "accuracy": _r(ok_),
                     "confidence_min": _r(min(f[i] for i in members)),
                     "confidence_max": _r(max(f[i] for i in members)),
                     "reliability_contribution": _r(nk * (fk - ok_) ** 2 / n),
                     "resolution_contribution": _r(nk * (ok_ - o_bar) ** 2 / n)})
    rel = math.fsum(rel_terms) / n
    res = math.fsum(res_terms) / n
    brier_grouped = math.fsum(grouped_terms) / n
    wvar = math.fsum(wv_terms) / n
    wcov = math.fsum(wc_terms) / n
    return {
        "constant_baseline": {
            "label": "IN-SAMPLE prevalence reference: the constant is the observed accuracy of "
                     "this same synthetic sample, so it uses the outcomes being scored. It is "
                     "not an independent held-out predictor and not a fair deployable comparator.",
            "constant_confidence": _r(o_bar),
            "brier": _r(unc),
            "brier_direct_check": _r(baseline_brier_direct),
            "formula": "brier = accuracy * (1 - accuracy)",
            "rules_v1_brier_raw": _r(brier_raw),
            "rules_v1_minus_baseline_brier": _r(brier_raw - unc),
            "brier_skill_score_vs_in_sample_baseline": _r(1.0 - brier_raw / unc) if unc > 0 else None,
            "denominator_N": n,
        },
        "decomposition": {
            "note": "Murphy identity (reliability - resolution + uncertainty) is exact for "
                    "brier_grouped, NOT for brier_raw. brier_raw = brier_grouped + grouping_residual.",
            "denominator_N": n,
            "weighting": "bucket terms weighted by n_k / N (count-weighted); empty buckets skipped",
            "bucket_scheme": "10 equal-width buckets [0.0,0.1) ... [0.9,1.0]; same as reliability_table",
            "brier_raw": _r(brier_raw),
            "brier_grouped": _r(brier_grouped),
            "reliability": _r(rel),
            "resolution": _r(res),
            "uncertainty": _r(unc),
            "murphy_grouped_brier": _r(rel - res + unc),
            "grouping_residual": _r(brier_raw - brier_grouped),
            "within_bin_confidence_variance": _r(wvar),
            "within_bin_covariance": _r(wcov),
            "confidence_varies_within_bins": any(
                r["n"] > 1 and r["confidence_max"] != r["confidence_min"] for r in rows),
            "by_bucket": rows,
        },
    }


def ascii_reliability(buckets: list[dict]) -> str:
    lines = ["confidence bucket | accuracy (# = 5%)         | n",
             "------------------+---------------------------+------"]
    for b in buckets:
        if not b["n"]:
            lines.append(f"{b['bucket']:>17} | {'(empty)':<27}| 0")
            continue
        bar = "#" * int(round(b["accuracy"] * 20))
        lines.append(f"{b['bucket']:>17} | {bar:<27}| {b['n']}")
    return "\n".join(lines)


def render_baseline_markdown(report: dict) -> str:
    base, dec = report.get("constant_baseline"), report.get("decomposition")
    if not base or not dec:
        return ""
    rows = "\n".join(
        f"| {b['bucket']} | {b['n']} | {b['weight_n_k_over_N']} | {b['mean_confidence']} | "
        f"{b['accuracy']} | {b['reliability_contribution']} | {b['resolution_contribution']} |"
        for b in dec["by_bucket"])
    return (
        "\n## Constant baseline and Brier decomposition\n\n"
        "Synthetic world, synthetic truth: nothing here is customer or production data.\n\n"
        f"> **{base['label']}** The baseline is computed on the same synthetic sample it is "
        "compared against (data leakage), so it only shows how much a constant forecast "
        "that already knows the base rate would score here.\n\n"
        f"- Constant baseline confidence = observed accuracy = **{base['constant_confidence']}**; "
        f"baseline Brier = accuracy × (1 − accuracy) = **{base['brier']}**\n"
        f"- rules-v1 raw Brier = **{base['rules_v1_brier_raw']}** "
        f"(difference vs baseline {base['rules_v1_minus_baseline_brier']:+}; "
        f"skill score vs in-sample baseline {base['brier_skill_score_vs_in_sample_baseline']})\n"
        f"- Denominator for every term: N = {dec['denominator_N']} scored fields; bucket terms "
        "are count-weighted by n_k / N; empty buckets are skipped.\n\n"
        "Murphy decomposition of the **grouped** forecast (each row's confidence replaced by "
        "its bucket mean):\n\n"
        f"- reliability = (1/N) Σ n_k (conf_k − acc_k)² = **{dec['reliability']}**\n"
        f"- resolution = (1/N) Σ n_k (acc_k − overall accuracy)² = **{dec['resolution']}**\n"
        f"- uncertainty = accuracy (1 − accuracy) = **{dec['uncertainty']}**\n"
        f"- grouped Brier = reliability − resolution + uncertainty = **{dec['brier_grouped']}**\n\n"
        "This identity is exact for the grouped forecast only. Confidence varies within buckets "
        f"here, so it does **not** equal the raw Brier: raw Brier {dec['brier_raw']} = grouped "
        f"{dec['brier_grouped']} + grouping residual {dec['grouping_residual']} "
        "(= within-bin confidence variance "
        f"{dec['within_bin_confidence_variance']} − 2 × within-bin covariance "
        f"{dec['within_bin_covariance']}).\n\n"
        "| bucket | n | n_k/N | mean confidence | accuracy | reliability term | resolution term |\n"
        "|---|---|---|---|---|---|---|\n" + rows + "\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../../docs/benchmarks/calibration.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--companies", type=int, default=250)
    args = ap.parse_args()

    pairs = collect_scored_predictions(seed=args.seed, n_companies=args.companies)
    report = evaluate(pairs)
    report["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    report["formula_version"] = "rules-v1"
    report["environment"] = {"python": platform.python_version(), "system": platform.system()}
    report["run_parameters"] = {"seed": args.seed, "n_companies": args.companies,
                                "scored_rows_sha256": scored_rows_sha256(pairs),
                                "data": "synthetic world; truth is generated, not customer data"}
    report["honest_interpretation"] = (
        "rules-v1 is a heuristic confidence score, not a calibrated probability. "
        f"Measured ECE {report['expected_calibration_error']}: "
        + ("reasonably aligned with observed accuracy on this synthetic distribution."
           if (report["expected_calibration_error"] or 1) < 0.08
           else "materially miscalibrated on this distribution; the bucket table does not "
                "demonstrate useful ranking (AUC not computed). Do not read scores as "
                "probabilities; see docs/benchmarks/calibration.md.")
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    md = (
        "# Confidence calibration report (rules-v1)\n\n"
        f"Generated: {report['generated_at']} · {report['n_predictions']} scored fields · "
        f"seed {args.seed}\n\n"
        f"- **Brier score:** {report['brier_score']}\n"
        f"- **Expected Calibration Error:** {report['expected_calibration_error']}\n"
        f"- **Overall accuracy:** {report['overall_accuracy']} · "
        f"**mean confidence:** {report['mean_confidence']}\n\n"
        f"> {report['honest_interpretation']}\n\n"
        "```text\n" + ascii_reliability(report["reliability_table"]) + "\n```\n\n"
        "| bucket | n | mean confidence | accuracy |\n|---|---|---|---|\n"
        + "\n".join(
            f"| {b['bucket']} | {b['n']} | {b['mean_confidence']} | {b['accuracy']} |"
            for b in report["reliability_table"]
        )
        + "\n"
        + render_baseline_markdown(report)
    )
    out.with_suffix(".md").write_text(md)
    print(f"wrote {out} and {out.with_suffix('.md')}")  # noqa: T201
    print(ascii_reliability(report["reliability_table"]))  # noqa: T201
    print(f"brier={report['brier_score']} ece={report['expected_calibration_error']}")  # noqa: T201


if __name__ == "__main__":
    main()
