"""End-to-end guards: the full run is pinned byte for byte, and the labelled samples never regress."""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import DATASET, GOLDEN

from erasure_audit.contract import read_csv
from erasure_audit.evaluation.score import evaluate
from erasure_audit.pipeline import read_requests, run_cases, write_output

# Baseline measured at the hackathon tag and after the determinism fix; raise it when the agent improves.
BASELINE = {"status_accuracy": 1.0, "step_f1": 0.943, "exact_cases": 18}


def test_full_run_matches_golden(pipeline, tmp_path):
    out = tmp_path / "output.csv"
    write_output(run_cases(pipeline, read_requests(DATASET / "deletion_requests.csv")), out)
    assert out.read_bytes() == (GOLDEN / "output.csv").read_bytes()


def test_samples_do_not_regress(pipeline):
    preds = {cid: {"status": status, "steps": steps}
             for cid, status, steps, _ in run_cases(pipeline, read_requests(DATASET / "sample_deletion_requests.csv"))}
    m = evaluate(read_csv(DATASET / "sample_output.csv"), preds)
    assert m["status_accuracy"] >= BASELINE["status_accuracy"]
    assert m["step_f1"] >= BASELINE["step_f1"]
    assert m["exact_cases"] >= BASELINE["exact_cases"]


def test_output_independent_of_hash_seed(tmp_path):
    """Regression for the account-join bug: set iteration order must never change an answer."""
    outputs = []
    for seed in ("1", "2"):
        out = tmp_path / f"out_{seed}.csv"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-m", "erasure_audit", "run", "--output", str(out)], env=env, check=True,
                       capture_output=True)
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]
