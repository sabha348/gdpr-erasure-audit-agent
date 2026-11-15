"""Evaluate the pipeline on the public sample cases.

Runs the pipeline on dataset/sample_deletion_requests.csv, compares the result with
dataset/sample_output.csv and reports:

  * case-status accuracy (plus a confusion matrix);
  * step-level precision / recall / F1, matching steps on
    (phase, system_id, resource_id, operation, state, evidence_id) as a multiset per case;
  * field-level accuracy: each gold step is aligned to the best predicted step for the same
    system_id + resource_id, then every field is compared (unaligned gold steps count as wrong);
  * exact sequence accuracy: status and the full ordered step list identical;
  * a per-case diff for every case that is not an exact match.

Pipeline contract: `erasure-audit run --requests <csv> --output <csv>` writes predictions for the
given requests. Use --predictions to score an existing file without running the pipeline.

Usage (from the repository root):
    erasure-audit score
    erasure-audit score --predictions path/to/sample_predictions.csv
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ..contract import case_sort_key, read_csv
from ..settings import get_settings
from .metrics import aggregate, score_case
from .validate import print_report, validate_file

RESULTS_DIR = Path("eval") / "results"


def load_predictions(path: Path) -> dict[str, dict]:
    preds = {}
    for r in read_csv(path):
        try:
            steps = json.loads(r.get("steps_json") or "[]")
        except json.JSONDecodeError:
            steps = None  # scored as wrong; the validator reports the details
        preds[r["case_id"]] = {"status": r.get("deletion_status"), "steps": steps if isinstance(steps, list) else []}
    return preds


def evaluate(gold_rows: list[dict], preds: dict[str, dict]) -> dict:
    """gold_rows in sample_output.csv shape; preds = {case_id: {status, steps}}. Adds per-case diffs."""
    scores = []
    for row in sorted(gold_rows, key=lambda r: case_sort_key(r["case_id"])):
        gold = {"case_id": row["case_id"], "deletion_status": row["deletion_status"],
                "steps": json.loads(row["steps_json"])}
        p = preds.get(row["case_id"])
        scores.append(score_case(gold, p["status"] if p else None, p["steps"] if p else []))
    m = aggregate(scores)
    m["diffs"] = ["\n".join(s["diff"]) for s in scores if s["diff"]]
    return m


def render(m: dict) -> str:
    pct = lambda x: f"{100 * x:6.2f}%"
    out = [
        "## Sample evaluation",
        "",
        f"Cases: {m['cases']}   gold steps: {m['steps_gold']}   predicted steps: {m['steps_pred']}",
        "",
        "| metric | value |",
        "|---|---|",
        f"| case-status accuracy | {pct(m['status_accuracy'])} |",
        f"| step precision | {pct(m['step_precision'])} |",
        f"| step recall | {pct(m['step_recall'])} |",
        f"| step F1 | {pct(m['step_f1'])} |",
        f"| step recall ignoring evidence_id | {pct(m['step_recall_ignoring_evidence_id'])} |",
    ]
    out += [f"| field accuracy: {f} | {pct(v)} |" for f, v in m["field_accuracy"].items()]
    out += [
        f"| unaligned predicted steps | {m['unaligned_pred_steps']} |",
        f"| step-set accuracy (per case) | {pct(m['step_set_accuracy'])} |",
        f"| order accuracy given correct set | {pct(m['order_accuracy_given_correct_set'])} |",
        f"| exact sequence accuracy | {pct(m['sequence_accuracy'])} ({m['exact_cases']}/{m['cases']}) |",
        "",
        "Status confusion (gold->pred): " + ", ".join(f"{k}: {v}" for k, v in m["status_confusion"].items()),
        "",
    ]
    if m["diffs"]:
        out += [f"### Per-case diffs ({len(m['diffs'])} cases)", "", "```", *m["diffs"], "```"]
    return "\n".join(out)


def run_pipeline(requests: Path, output: Path) -> bool:
    cmd = [sys.executable, "-m", "erasure_audit", "run", "--requests", str(requests), "--output", str(output)]
    print("Running pipeline:", " ".join(cmd))
    if output.exists():
        output.unlink()
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"Pipeline exited with code {proc.returncode}")
        return False
    if not output.exists():
        print(f"Pipeline finished but wrote no predictions to {output} ")
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requests", default=str(get_settings().dataset_dir / "sample_deletion_requests.csv"))
    ap.add_argument("--gold", default=str(get_settings().dataset_dir / "sample_output.csv"))
    ap.add_argument("--predictions", help="score this CSV instead of running the pipeline")
    ap.add_argument("--out-dir", default=str(RESULTS_DIR), help="where predictions, metrics JSON and report go")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    requests = Path(args.requests)
    if args.predictions:
        pred_path = Path(args.predictions)
    else:
        pred_path = out_dir / "sample_predictions.csv"
        if not run_pipeline(requests, pred_path):
            return 2

    print_report(validate_file(pred_path, requests), pred_path)
    print()
    metrics = evaluate(read_csv(Path(args.gold)), load_predictions(pred_path))
    report = render(metrics)
    print(report)
    (out_dir / "sample_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (out_dir / "sample_report.md").write_text(report + "\n", encoding="utf-8")
    print(f"\nWrote {out_dir / 'sample_metrics.json'} and {out_dir / 'sample_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
