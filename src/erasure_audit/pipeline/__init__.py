"""Deletion-audit pipeline. See erasure_audit/cli.py for the command line and docs/DECISIONS.md for the rules."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ..contract import OUTPUT_HEADER, serialize_steps
from .assess import Pipeline
from .config import CHOICES, Options


def read_requests(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build(dataset_dir: Path, options: Options = Options(), extra_requests: list[dict] | None = None) -> Pipeline:
    """The activity-batch account matcher needs every request the dataset knows about (sample and evaluation),
    because their deletion batches interleave in the same activity stream. `extra_requests` (for example
    hand-written evaluation cases) join that context so they compete for accounts like any other request."""
    context = []
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        if (dataset_dir / name).exists():
            context += read_requests(dataset_dir / name)
    known = {q["case_id"] for q in context}
    context += [q for q in extra_requests or [] if q["case_id"] not in known]
    return Pipeline(dataset_dir, options, context)


def run_cases(pipeline: Pipeline, requests: list[dict]) -> list[tuple[str, str, list, dict]]:
    return [(q["case_id"], *pipeline.assess(q)) for q in requests]


def write_output(results, path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")  # same bytes on every OS
        w.writerow(OUTPUT_HEADER)
        for case_id, status, steps, _diag in results:
            w.writerow([case_id, status, serialize_steps(steps)])


def write_diagnostics(results, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for _cid, _status, _steps, diag in results:
            f.write(json.dumps(diag, ensure_ascii=False, default=str) + "\n")


__all__ = ["build", "run_cases", "write_output", "write_diagnostics", "read_requests", "Options", "CHOICES", "Pipeline"]
