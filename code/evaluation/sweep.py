"""Sequential one-factor-at-a-time sweep of the pipeline's named options on the 32 public samples.

Options are visited in declaration order. Each alternative is scored with every other option at the current
settings (defaults, updated by the picks made earlier in the sweep), so two options that fix the same case
are not both credited. The best value is picked by (exact sequence accuracy, step F1, mean field accuracy,
case-status accuracy); on a tie the current default stays. A final run scores the combined picks.

Usage (from the repository root):
    python code/evaluation/sweep.py            # writes code/evaluation/results/sweep.md and sweep.json
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
sys.path.insert(0, str(CODE))
from pipeline import CHOICES, Options, build, read_requests  # noqa: E402

_spec = importlib.util.spec_from_file_location("eval_main", HERE / "main.py")
eval_main = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(HERE))
_spec.loader.exec_module(eval_main)

DATASET = CODE.parent / "dataset"
RESULTS = HERE / "results"


def score(options: Options) -> dict:
    pipe = build(DATASET, options)
    preds = {}
    for q in read_requests(DATASET / "sample_deletion_requests.csv"):
        status, steps, _ = pipe.assess(q)
        preds[q["case_id"]] = {"status": status, "steps": steps}
    m = eval_main.evaluate(eval_main.read_csv(DATASET / "sample_output.csv"), preds)
    m["field_mean"] = sum(m["field_accuracy"].values()) / len(m["field_accuracy"])
    m.pop("diffs")
    return m


def rank(m: dict) -> tuple:
    return (round(m["sequence_accuracy"], 6), round(m["step_f1"], 6), round(m["field_mean"], 6), round(m["status_accuracy"], 6))


def row(name: str, value: str, m: dict, mark: str = "") -> str:
    pct = lambda x: f"{100 * x:.1f}%"
    fa = m["field_accuracy"]
    return (f"| {name} | {value}{mark} | {pct(m['status_accuracy'])} | {pct(m['step_precision'])} | {pct(m['step_recall'])} | "
            f"{pct(m['step_f1'])} | {pct(m['field_mean'])} | {pct(fa['operation'])} | {pct(fa['evidence_id'])} | "
            f"{pct(m['sequence_accuracy'])} ({m['exact_cases']}/{m['cases']}) |")


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    base = Options()
    lines = ["| option | value | case status | step P | step R | step F1 | field mean | field: operation | field: evidence_id | exact sequence |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    record, picked = {"defaults_before": asdict(base), "runs": []}, {}
    cache = {}
    for f in fields(Options):
        name, current = f.name, getattr(base, f.name)
        if len(CHOICES[name]) < 2:
            continue
        results = {}
        for value in CHOICES[name]:
            opt = base.with_(**{name: value})
            key = tuple(asdict(opt).items())
            results[value] = cache.get(key) or score(opt)
            cache[key] = results[value]
            record["runs"].append({"option": name, "value": value, "metrics": results[value]})
        best = max(results, key=lambda v: rank(results[v]))
        if rank(results[best]) == rank(results[current]):
            best = current  # tie -> keep the current default
        picked[name] = best
        base = base.with_(**{name: best})  # later options are compared against this pick
        for value in CHOICES[name]:
            mark = " (default)" if value == current else ""
            mark += " **← picked**" if value == best and value != current else (" ← kept" if value == best else "")
            lines.append(row(name, value, results[value], mark))
    final = base
    fm = score(final)
    record["picked"] = picked
    record["final_options"] = asdict(final)
    record["final_metrics"] = fm
    lines += ["", f"Combined picked values: `{asdict(final)}`", "", row("combined", "picked", fm)]
    table = "\n".join(lines)
    (RESULTS / "sweep.md").write_text(table + "\n", encoding="utf-8")
    (RESULTS / "sweep.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
