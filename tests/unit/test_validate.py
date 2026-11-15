"""The validator is checked against the official sample answers and mutations of them."""
import copy
import json

import pytest

from conftest import DATASET
from erasure_audit.contract import read_csv
from erasure_audit.evaluation.validate import load_evidence_times, validate_file, validate_steps

SAMPLES = {r["case_id"]: r for r in read_csv(DATASET / "sample_output.csv")}
REQUESTS = {r["case_id"]: r for r in read_csv(DATASET / "sample_deletion_requests.csv")}


@pytest.fixture(scope="module")
def times():
    return load_evidence_times(DATASET)


def errors(case_id, status, steps, times):
    return {i.rule for i in validate_steps(case_id, status, steps, REQUESTS[case_id]["audit_at"], times)
            if i.level == "ERROR"}


def incomplete_case():
    cid = next(c for c, r in SAMPLES.items() if r["deletion_status"] == "INCOMPLETE")
    return cid, json.loads(SAMPLES[cid]["steps_json"])


def test_official_samples_are_valid(times):
    for cid, row in SAMPLES.items():
        assert errors(cid, row["deletion_status"], json.loads(row["steps_json"]), times) == set(), cid


def test_sample_file_passes(tmp_path):
    issues = validate_file(DATASET / "sample_output.csv", DATASET / "sample_deletion_requests.csv", DATASET)
    assert not [i for i in issues if i.level == "ERROR"]


def test_not_applicable_must_be_empty(times):
    cid, steps = incomplete_case()
    assert "not_applicable_empty" in errors(cid, "NOT_APPLICABLE", steps, times)


def test_step_numbers_must_be_consecutive(times):
    cid, steps = incomplete_case()
    steps = copy.deepcopy(steps)
    steps[-1]["step_no"] += 5
    assert "step_no_consecutive" in errors(cid, "INCOMPLETE", steps, times)


def test_phase_order_is_enforced(times):
    cid, steps = incomplete_case()
    steps = list(reversed(copy.deepcopy(steps)))
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    assert "phase_order" in errors(cid, "INCOMPLETE", steps, times)


def test_operation_state_must_match(times):
    cid, steps = incomplete_case()
    steps = copy.deepcopy(steps)
    steps[0]["state"] = "ABSENT"
    assert "operation_state" in errors(cid, "INCOMPLETE", steps, times)


def test_future_steps_have_no_evidence(times):
    cid, steps = incomplete_case()
    steps = copy.deepcopy(steps)
    future = next(s for s in steps if s["phase"] != "FINDING")
    future["evidence_id"] = steps[0]["evidence_id"]
    assert "future_steps_empty_evidence" in errors(cid, "INCOMPLETE", steps, times)


def test_evidence_after_audit_at_is_rejected(times):
    cid, steps = incomplete_case()
    audit_at = REQUESTS[cid]["audit_at"]
    later = next(fid for fid, at in sorted(times.items(), key=lambda kv: kv[1]) if at > audit_at)
    steps = copy.deepcopy(steps)
    steps[0]["evidence_id"] = later
    assert "evidence_before_audit" in errors(cid, "INCOMPLETE", steps, times)
