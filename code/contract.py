"""Output contract shared by the pipeline, the evaluator and the validator.

Everything here comes from README.md / problem_statement.md / AGENTS.md section 6.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "dataset"

OUTPUT_HEADER = ["case_id", "deletion_status", "steps_json"]
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
STEP_KEYS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")

# Operation -> (phase, state), listed in the canonical order of the problem statement's table.
# Within a phase, steps are ordered by this table, then system_id, then resource_id.
OPERATIONS = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
OPERATION_RANK = {op: i for i, op in enumerate(OPERATIONS)}
PHASE_RANK = {p: i for i, p in enumerate(PHASES)}

# Remediation operation -> the verification operation that must follow it.
REMEDIATION_TO_VERIFICATION = {
    "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
    "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
    "DELETE_RECORD": "VERIFY_RECORD_ABSENT",
    "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
}


def case_sort_key(case_id: str):
    """Numeric case order (case_33 < case_100), matching deletion_requests.csv (DECISIONS #1)."""
    m = re.fullmatch(r"(.*?)(\d+)", case_id)
    return (m.group(1), int(m.group(2))) if m else (case_id, -1)


def natural_key(text: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def step_sort_key(step: dict):
    """README ordering: phase, operation table order, system_id, resource_id."""
    return (PHASE_RANK[step["phase"]], OPERATION_RANK[step["operation"]], step["system_id"], step["resource_id"])


def serialize_steps(steps: list[dict]) -> str:
    """Deterministic steps_json: fixed key order, compact separators."""
    return json.dumps([{k: s[k] for k in STEP_KEYS} for s in steps], ensure_ascii=False, separators=(",", ":"))


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
