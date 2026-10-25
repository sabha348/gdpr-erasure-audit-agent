"""Deletion-audit pipeline. See code/main.py for the command line and docs/DECISIONS.md for the rules."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contract import OUTPUT_HEADER, serialize_steps  # noqa: E402

from .assess import Pipeline
from .config import CHOICES, Options


def read_requests(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build(dataset_dir: Path, options: Options = Options()) -> Pipeline:
    """The activity-batch account matcher needs every request the dataset knows about (sample and evaluation),
    because their deletion batches interleave in the same activity stream."""
    context = []
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        if (dataset_dir / name).exists():
            context += read_requests(dataset_dir / name)
    return Pipeline(dataset_dir, options, context)


def run_cases(pipeline: Pipeline, requests: list[dict]) -> list[tuple[str, str, list, dict]]:
    return [(q["case_id"], *pipeline.assess(q)) for q in requests]


def write_output(results, path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(OUTPUT_HEADER)
        for case_id, status, steps, _diag in results:
            w.writerow([case_id, status, serialize_steps(steps)])


def write_diagnostics(results, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for _cid, _status, _steps, diag in results:
            f.write(json.dumps(diag, ensure_ascii=False, default=str) + "\n")


__all__ = ["build", "run_cases", "write_output", "write_diagnostics", "read_requests", "Options", "CHOICES", "Pipeline"]
