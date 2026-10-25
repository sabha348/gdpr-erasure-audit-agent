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

Pipeline contract: `python code/main.py --requests <csv> --output <csv>` writes predictions for the
given requests. Use --predictions to score an existing file without running the pipeline.

Usage (from the repository root):
    python code/evaluation/main.py
    python code/evaluation/main.py --predictions path/to/sample_predictions.csv
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contract import DATASET_DIR, REPO_ROOT, case_sort_key, read_csv  # noqa: E402
from validate_output import print_report, validate_file  # noqa: E402

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def step_tuple(s: dict, fields=FIELDS) -> tuple:
    return tuple(s.get(f) for f in fields)


def load_predictions(path: Path) -> dict[str, dict]:
    preds = {}
    for r in read_csv(path):
        try:
            steps = json.loads(r.get("steps_json") or "[]")
        except json.JSONDecodeError:
            steps = None  # scored as wrong; the validator reports the details
        preds[r["case_id"]] = {"status": r.get("deletion_status"), "steps": steps if isinstance(steps, list) else []}
    return preds


def multiset_tp(gold: list[tuple], pred: list[tuple]) -> int:
    return sum((collections.Counter(gold) & collections.Counter(pred)).values())


def align(gold: list[dict], pred: list[dict]):
    """Greedy alignment: each gold step takes the unused predicted step with the same system_id +
    resource_id that agrees on the most remaining fields (ties -> earliest). Returns
    (pairs, unmatched_gold, unmatched_pred) with pairs as (gold_step, pred_step)."""
    used = set()
    pairs, missing = [], []
    for g in gold:
        best, best_score = None, -1
        for j, p in enumerate(pred):
            if j in used or p.get("system_id") != g["system_id"] or p.get("resource_id") != g["resource_id"]:
                continue
            score = sum(p.get(f) == g[f] for f in ("phase", "operation", "state", "evidence_id"))
            if score > best_score:
                best, best_score = j, score
        if best is None:
            missing.append(g)
        else:
            used.add(best)
            pairs.append((g, pred[best]))
    extra = [p for j, p in enumerate(pred) if j not in used]
    return pairs, missing, extra


def fmt_step(s: dict) -> str:
    ev = s.get("evidence_id")
    return f"{s.get('phase', '?')[:5]:5} {s.get('system_id')}/{s.get('resource_id')} {s.get('operation')} {s.get('state')} ev={ev!r}"


def evaluate(gold_rows: list[dict], preds: dict[str, dict]) -> dict:
    status_ok = 0
    confusion = collections.Counter()
    tp = n_gold = n_pred = 0
    tp_no_ev = 0
    field_ok = collections.Counter()
    extra_total = 0
    seq_ok = 0
    set_ok = order_ok_given_set = 0
    diffs = []

    for row in sorted(gold_rows, key=lambda r: case_sort_key(r["case_id"])):
        cid = row["case_id"]
        g_status, g_steps = row["deletion_status"], json.loads(row["steps_json"])
        p = preds.get(cid)
        p_status, p_steps = (p["status"], p["steps"]) if p else (None, [])
        p_steps = [s for s in p_steps if isinstance(s, dict)]

        confusion[(g_status, p_status)] += 1
        status_ok += g_status == p_status

        g_t, p_t = [step_tuple(s) for s in g_steps], [step_tuple(s) for s in p_steps]
        case_tp = multiset_tp(g_t, p_t)
        tp += case_tp
        n_gold += len(g_t)
        n_pred += len(p_t)
        tp_no_ev += multiset_tp([t[:5] for t in g_t], [t[:5] for t in p_t])

        pairs, missing, extra = align(g_steps, p_steps)
        for g, pr in pairs:
            for f in FIELDS:
                field_ok[f] += g[f] == pr.get(f)
        extra_total += len(extra)

        same_set = collections.Counter(g_t) == collections.Counter(p_t)
        exact = g_status == p_status and g_t == p_t and [s.get("step_no") for s in p_steps] == list(range(1, len(p_steps) + 1))
        set_ok += same_set
        order_ok_given_set += same_set and g_t == p_t
        seq_ok += exact

        if not exact:
            lines = [f"{cid}: status gold={g_status} pred={p_status}" + ("" if p else "  (NO PREDICTION ROW)")]
            for g, pr in pairs:
                changed = [f"{f}: {g[f]!r} -> {pr.get(f)!r}" for f in FIELDS if g[f] != pr.get(f)]
                if changed:
                    lines.append(f"  ~ gold#{g['step_no']} {fmt_step(g)}  |  " + "; ".join(changed))
            for g in missing:
                lines.append(f"  - missing gold#{g['step_no']} {fmt_step(g)}")
            for pr in extra:
                lines.append(f"  + extra   pred#{pr.get('step_no')} {fmt_step(pr)}")
            p_nos = [s.get("step_no") for s in p_steps]
            if p_nos != list(range(1, len(p_steps) + 1)):
                lines.append(f"  ! step_no not consecutive from 1: {p_nos}")
            if same_set and g_t != p_t:
                lines.append("  order differs:")
                lines.append("    gold: " + " | ".join(f"{t[3]}:{t[2]}" for t in g_t))
                lines.append("    pred: " + " | ".join(f"{t[3]}:{t[2]}" for t in p_t))
            diffs.append("\n".join(lines))

    n_cases = len(gold_rows)
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_gold if n_gold else 0.0
    return {
        "cases": n_cases,
        "status_accuracy": status_ok / n_cases,
        "status_confusion": {f"{g}->{p}": n for (g, p), n in sorted(confusion.items(), key=str)},
        "steps_gold": n_gold,
        "steps_pred": n_pred,
        "steps_matched": tp,
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "step_recall_ignoring_evidence_id": tp_no_ev / n_gold if n_gold else 0.0,
        "field_accuracy": {f: field_ok[f] / n_gold if n_gold else 0.0 for f in FIELDS},
        "unaligned_pred_steps": extra_total,
        "step_set_accuracy": set_ok / n_cases,
        "order_accuracy_given_correct_set": order_ok_given_set / set_ok if set_ok else 0.0,
        "sequence_accuracy": seq_ok / n_cases,
        "exact_cases": seq_ok,
        "diffs": diffs,
    }


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
    cmd = [sys.executable, str(REPO_ROOT / "code" / "main.py"), "--requests", str(requests), "--output", str(output)]
    print("Running pipeline:", " ".join(cmd))
    if output.exists():
        output.unlink()
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print(f"Pipeline exited with code {proc.returncode}")
        return False
    if not output.exists():
        print(f"Pipeline finished but wrote no predictions to {output} (is code/main.py implemented?)")
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requests", default=str(DATASET_DIR / "sample_deletion_requests.csv"))
    ap.add_argument("--gold", default=str(DATASET_DIR / "sample_output.csv"))
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
