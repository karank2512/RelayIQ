"""Benchmark CLI: python -m relayiq.benchmark.cli --out ../../docs/benchmarks/results.json"""

import argparse
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from relayiq.benchmark.runner import run_benchmark


def to_markdown(report: dict) -> str:
    cols = [
        ("strategy", "Strategy"),
        ("provider_calls", "Provider calls"),
        ("cost_credits", "Cost (credits)"),
        ("fill_rate", "Fill rate"),
        ("field_precision_vs_truth", "Precision vs truth"),
        ("true_usable_leads", "True usable leads"),
        ("cost_per_true_usable", "Cost / true usable"),
        ("review_records", "Review load"),
        ("filtered_records", "Filtered"),
    ]
    lines = [
        "# RelayIQ cost benchmark (measured on seeded synthetic data)",
        "",
        f"Generated: {report['generated_at']}  ·  seed {report['config']['seed']}  ·  "
        f"{report['config']['contacts']} contacts, "
        f"{int(report['config']['duplicate_submission_rate'] * 100)}% duplicate submissions",
        "",
        f"> {report['measurement_basis']}",
        "",
        "| " + " | ".join(c[1] for c in cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for r in report["results"]:
        lines.append("| " + " | ".join(str(r.get(c[0], "—")) for c in cols) + " |")
    baseline = report["results"][0]
    full = next(r for r in report["results"] if r["strategy"] == "relayiq_full")
    if baseline["cost_credits"]:
        saved = 1 - full["cost_credits"] / baseline["cost_credits"]
        lines += [
            "",
            f"**Headline (this run):** full RelayIQ spent {full['cost_credits']} credits vs "
            f"{baseline['cost_credits']} naive — **{saved:.0%} lower spend** — at "
            f"{full['field_precision_vs_truth']} field precision vs naive's "
            f"{baseline['field_precision_vs_truth']}.",
            "",
            "Notes: review-queue records are conservatively excluded from RelayIQ's usable-lead "
            "count (human review is not simulated). Provider costs/latencies are simulator "
            "parameters, not real vendor pricing.",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../../docs/benchmarks/results.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--companies", type=int, default=200)
    ap.add_argument("--duplicate-rate", type=float, default=0.15)
    args = ap.parse_args()

    report = run_benchmark(seed=args.seed, n_companies=args.companies,
                           duplicate_submission_rate=args.duplicate_rate)
    report["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    report["environment"] = {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "system": platform.system(),
        "note": "local development laptop — not representative of production capacity",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    out.with_suffix(".md").write_text(to_markdown(report))
    print(f"wrote {out} and {out.with_suffix('.md')}")  # noqa: T201
    for r in report["results"]:
        print(  # noqa: T201
            f"  {r['strategy']:16s} cost={r['cost_credits']:>9} calls={r['provider_calls']:>5} "
            f"fill={r['fill_rate']} precision={r['field_precision_vs_truth']} "
            f"true_usable={r['true_usable_leads']} cost/usable={r['cost_per_true_usable']}"
        )


if __name__ == "__main__":
    main()
