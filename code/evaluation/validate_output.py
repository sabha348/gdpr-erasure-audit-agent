"""Validate a predictions CSV against every format rule in README.md / problem_statement.md.

Usage (from the repository root):
    python code/evaluation/validate_output.py                       # output.csv vs deletion_requests.csv
    python code/evaluation/validate_output.py PRED.csv --requests dataset/sample_deletion_requests.csv

Exit code 0 when there are no errors (warnings allowed), 1 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contract import (  # noqa: E402
    DATASET_DIR, OPERATIONS, OUTPUT_HEADER, PHASE_RANK, PHASES, REMEDIATION_TO_VERIFICATION, REPO_ROOT,
    STATUSES, STEP_KEYS, case_sort_key, natural_key, read_csv, step_sort_key,
)


@dataclass
class Issue:
    level: str  # "ERROR" | "WARN"
    rule: str
    case_id: str
    message: str


def load_evidence_times(dataset_dir: Path) -> dict[str, str]:
    """Every citable id (file_id, activity_id, audit_id) -> the time it became available."""
    times = {r["file_id"]: r["captured_at"] for r in read_csv(dataset_dir / "evidence_files.csv")}
    times.update({r["activity_id"]: r["event_time"] for r in read_csv(dataset_dir / "system_activity.csv")})
    times.update({r["audit_id"]: r["event_time"] for r in read_csv(dataset_dir / "user_audit_log.csv")})
    return times


def validate_steps(case_id: str, status: str, steps, audit_at: str | None, evidence_times: dict[str, str]) -> list[Issue]:
    issues: list[Issue] = []

    def err(rule, msg):
        issues.append(Issue("ERROR", rule, case_id, msg))

    def warn(rule, msg):
        issues.append(Issue("WARN", rule, case_id, msg))

    if not isinstance(steps, list):
        err("json_array", "steps_json is not a JSON array")
        return issues
    if status == "NOT_APPLICABLE":
        if steps:
            err("not_applicable_empty", f"NOT_APPLICABLE must have [] but has {len(steps)} steps")
        return issues
    if not steps:
        err("non_empty_steps", f"{status} case has no steps")
        return issues

    well_formed = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            err("step_object", f"step #{i} is not an object")
            continue
        keys = set(s)
        if keys != set(STEP_KEYS):
            missing, extra = set(STEP_KEYS) - keys, keys - set(STEP_KEYS)
            err("step_keys", f"step #{i} keys mismatch (missing={sorted(missing)}, extra={sorted(extra)})")
            continue
        if not isinstance(s["step_no"], int) or isinstance(s["step_no"], bool):
            err("step_no_type", f"step #{i} step_no is not an integer: {s['step_no']!r}")
            continue
        bad_types = [k for k in STEP_KEYS[1:] if not isinstance(s[k], str)]
        if bad_types:
            err("field_type", f"step #{i} non-string fields: {bad_types}")
            continue
        if s["phase"] not in PHASES:
            err("phase", f"step #{i} unknown phase {s['phase']!r}")
            continue
        if s["operation"] not in OPERATIONS:
            err("operation", f"step #{i} unknown operation {s['operation']!r}")
            continue
        op_phase, op_state = OPERATIONS[s["operation"]]
        if s["phase"] != op_phase:
            err("operation_phase", f"step #{i} {s['operation']} belongs to {op_phase}, not {s['phase']}")
        if s["state"] != op_state:
            err("operation_state", f"step #{i} {s['operation']} requires state {op_state}, got {s['state']!r}")
        if not s["system_id"] or not s["resource_id"]:
            err("ids_non_empty", f"step #{i} has empty system_id/resource_id")
        well_formed.append(s)

    if len(well_formed) != len(steps):
        return issues  # structural errors already reported; ordering checks would be noise

    if [s["step_no"] for s in steps] != list(range(1, len(steps) + 1)):
        err("step_no_consecutive", f"step_no must be 1..{len(steps)}, got {[s['step_no'] for s in steps]}")

    phases = [PHASE_RANK[s["phase"]] for s in steps]
    if phases != sorted(phases):
        err("phase_order", "phases must be ordered FINDING -> REMEDIATION -> VERIFICATION")
    elif [step_sort_key(s) for s in steps] != sorted(step_sort_key(s) for s in steps):
        natural = [(*step_sort_key(s)[:3], natural_key(s["resource_id"])) for s in steps]
        if natural == sorted(natural):
            warn("within_phase_order", "resource_ids follow numeric, not string, order (README says ascending; tie-break unconfirmed)")
        else:
            err("within_phase_order", "within a phase, steps must follow operation table order, then system_id, then resource_id")

    triples = collections.Counter((s["phase"], s["system_id"], s["resource_id"], s["operation"]) for s in steps)
    for t, n in triples.items():
        if n > 1:
            warn("duplicate_step", f"step {t} appears {n} times")

    by_phase = collections.defaultdict(list)
    for s in steps:
        by_phase[s["phase"]].append(s)

    if status == "COMPLETE":
        if set(by_phase) != {"VERIFICATION"}:
            err("complete_verification_only", f"COMPLETE may only contain VERIFICATION steps, found {sorted(by_phase)}")
        for s in steps:
            if not s["evidence_id"]:
                err("complete_evidence_required", f"step {s['step_no']} ({s['operation']} {s['resource_id']}) has no evidence_id")
    elif status == "INCOMPLETE":
        if not by_phase.get("FINDING"):
            err("incomplete_needs_finding", "INCOMPLETE case has no FINDING step")
        if not by_phase.get("REMEDIATION"):
            warn("incomplete_needs_remediation", "INCOMPLETE case has no REMEDIATION step")
        if not by_phase.get("VERIFICATION"):
            warn("incomplete_needs_verification", "INCOMPLETE case has no VERIFICATION step")
        for s in by_phase.get("FINDING", []):
            if not s["evidence_id"]:
                err("finding_evidence_required", f"finding step {s['step_no']} ({s['resource_id']}) has no evidence_id")
        for s in by_phase.get("REMEDIATION", []) + by_phase.get("VERIFICATION", []):
            if s["evidence_id"] != "":
                err("future_steps_empty_evidence", f"{s['phase']} step {s['step_no']} must have evidence_id \"\" (got {s['evidence_id']!r})")
        # Plan consistency (warnings: the contract implies it but does not state it as a hard rule).
        found = {(s["system_id"], s["resource_id"]) for s in by_phase.get("FINDING", [])}
        remediated = {(s["system_id"], s["resource_id"]): s["operation"] for s in by_phase.get("REMEDIATION", [])}
        verified = {(s["system_id"], s["resource_id"], s["operation"]) for s in by_phase.get("VERIFICATION", [])}
        for key in found - set(remediated):
            warn("finding_without_remediation", f"finding on {key} has no remediation step")
        for key, op in remediated.items():
            if (*key, REMEDIATION_TO_VERIFICATION[op]) not in verified:
                warn("remediation_without_verification", f"{op} on {key} has no {REMEDIATION_TO_VERIFICATION[op]} step")

    # Cited evidence must exist and be available at audit_at.
    for s in steps:
        eid = s["evidence_id"]
        if not eid:
            continue
        if eid not in evidence_times:
            err("evidence_exists", f"step {s['step_no']} cites unknown evidence_id {eid!r}")
        elif audit_at and evidence_times[eid] > audit_at:
            err("evidence_before_audit", f"step {s['step_no']} cites {eid} ({evidence_times[eid]}) after audit_at {audit_at}")
    return issues


def validate_file(pred_path: Path, requests_path: Path, dataset_dir: Path = DATASET_DIR) -> list[Issue]:
    issues: list[Issue] = []
    requests = read_csv(requests_path)
    audit_at = {r["case_id"]: r["audit_at"] for r in requests}
    expected_ids = [r["case_id"] for r in requests]
    evidence_times = load_evidence_times(dataset_dir)

    with open(pred_path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)
    if header != OUTPUT_HEADER:
        issues.append(Issue("ERROR", "header", "-", f"header must be {','.join(OUTPUT_HEADER)}, got {header}"))
        return issues

    if len(rows) != len(expected_ids):
        issues.append(Issue("ERROR", "row_count", "-", f"expected {len(expected_ids)} data rows, got {len(rows)}"))
    ids = []
    for n, row in enumerate(rows, 2):
        if len(row) != 3:
            issues.append(Issue("ERROR", "column_count", f"line {n}", f"expected 3 columns, got {len(row)}"))
            continue
        case_id, status, steps_json = row
        ids.append(case_id)
        if status not in STATUSES:
            issues.append(Issue("ERROR", "status", case_id, f"unknown deletion_status {status!r}"))
            continue
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError as e:
            issues.append(Issue("ERROR", "json_valid", case_id, f"steps_json is not valid JSON: {e}"))
            continue
        issues += validate_steps(case_id, status, steps, audit_at.get(case_id), evidence_times)

    dupes = [c for c, k in collections.Counter(ids).items() if k > 1]
    if dupes:
        issues.append(Issue("ERROR", "unique_case_ids", "-", f"duplicate case_ids: {dupes[:10]}"))
    missing, unknown = set(expected_ids) - set(ids), set(ids) - set(expected_ids)
    if missing:
        issues.append(Issue("ERROR", "all_cases_present", "-", f"{len(missing)} case_ids missing, e.g. {sorted(missing, key=case_sort_key)[:10]}"))
    if unknown:
        issues.append(Issue("ERROR", "no_unknown_cases", "-", f"{len(unknown)} case_ids not in requests, e.g. {sorted(unknown)[:10]}"))
    if ids != sorted(ids, key=case_sort_key):
        issues.append(Issue("ERROR", "sorted_by_case_id", "-", "rows are not sorted by case_id (numeric order, as in the requests file)"))
    elif ids == expected_ids and ids != sorted(ids):
        issues.append(Issue("WARN", "sorted_by_case_id", "-", "numeric case order (matches the requests file); a plain string sort would differ"))
    return issues


def print_report(issues: list[Issue], pred_path: Path, max_per_rule: int = 5) -> None:
    errors = [i for i in issues if i.level == "ERROR"]
    warns = [i for i in issues if i.level == "WARN"]
    print(f"Validated {pred_path}: {len(errors)} error(s), {len(warns)} warning(s)")
    by_rule = collections.defaultdict(list)
    for i in issues:
        by_rule[(i.level, i.rule)].append(i)
    for (level, rule), items in sorted(by_rule.items()):
        print(f"  [{level}] {rule}: {len(items)}")
        for i in items[:max_per_rule]:
            print(f"      {i.case_id}: {i.message}")
        if len(items) > max_per_rule:
            print(f"      ... {len(items) - max_per_rule} more")
    print("RESULT:", "PASS" if not errors else "FAIL")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("predictions", nargs="?", default=str(REPO_ROOT / "output.csv"))
    ap.add_argument("--requests", default=str(DATASET_DIR / "deletion_requests.csv"))
    ap.add_argument("--max-per-rule", type=int, default=5)
    args = ap.parse_args(argv)
    pred = Path(args.predictions)
    if not pred.exists():
        print(f"Predictions file not found: {pred}")
        return 1
    issues = validate_file(pred, Path(args.requests))
    print_report(issues, pred, args.max_per_rule)
    return 0 if not any(i.level == "ERROR" for i in issues) else 1


if __name__ == "__main__":
    sys.exit(main())
