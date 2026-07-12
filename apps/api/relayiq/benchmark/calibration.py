"""Confidence-calibration evaluation against synthetic reviewed truth.

Runs the full-pipeline strategy, keeps every accepted field's confidence score and its
correctness vs world truth, then reports: reliability table (10 buckets), Brier score,
Expected Calibration Error (ECE), and an ASCII reliability diagram.

The rules-v1 score is a heuristic CONFIDENCE SCORE, not a calibrated probability — this
evaluation measures exactly how far off it is, and the report says so.

Usage: python -m relayiq.benchmark.calibration --out ../../docs/benchmarks/calibration.json
"""

import argparse
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from relayiq.benchmark.runner import (
    CONTACT_FIELDS,
    PROVIDER_PRIORS,
    STATIC_ROUTES,
    _call,
    _is_prefiltered,
    _mk_providers,
    _obs_from_call,
    _thresholds,
    _truth_correct,
    StrategyResult,
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
            age = (primary.retrieved_at - primary.source_timestamp).days
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
            age = (chosen.retrieved_at - chosen.source_timestamp).days
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
    report["honest_interpretation"] = (
        "rules-v1 is a heuristic confidence score, not a calibrated probability. "
        f"Measured ECE {report['expected_calibration_error']}: "
        + ("reasonably aligned with observed accuracy on this synthetic distribution."
           if (report["expected_calibration_error"] or 1) < 0.08
           else "materially miscalibrated on this distribution — treat scores as a ranking "
                "signal, not a probability; see docs/benchmarks/calibration.md.")
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
    )
    out.with_suffix(".md").write_text(md)
    print(f"wrote {out} and {out.with_suffix('.md')}")  # noqa: T201
    print(ascii_reliability(report["reliability_table"]))  # noqa: T201
    print(f"brier={report['brier_score']} ece={report['expected_calibration_error']}")  # noqa: T201


if __name__ == "__main__":
    main()
