import json

from erasure_audit.contract import (
    OPERATIONS,
    PHASES,
    REMEDIATION_TO_VERIFICATION,
    STEP_KEYS,
    case_sort_key,
    serialize_steps,
    step_sort_key,
)


def test_every_operation_has_a_known_phase():
    assert {phase for phase, _ in OPERATIONS.values()} == set(PHASES)


def test_every_remediation_has_a_verification():
    remediations = {op for op, (phase, _) in OPERATIONS.items() if phase == "REMEDIATION"}
    assert set(REMEDIATION_TO_VERIFICATION) == remediations
    for rem, ver in REMEDIATION_TO_VERIFICATION.items():
        assert OPERATIONS[ver][0] == "VERIFICATION"
        assert OPERATIONS[ver][1] == OPERATIONS[rem][1], "verification checks the state the remediation produces"


def test_case_sort_is_numeric():
    assert sorted(["case_100", "case_33", "case_5"], key=case_sort_key) == ["case_5", "case_33", "case_100"]


def test_steps_sort_by_phase_then_operation_then_ids():
    steps = [
        {"phase": "VERIFICATION", "operation": "VERIFY_RECORD_ABSENT", "system_id": "a", "resource_id": "x"},
        {"phase": "REMEDIATION", "operation": "DELETE_RECORD", "system_id": "a", "resource_id": "x"},
        {"phase": "REMEDIATION", "operation": "DISABLE_DATA_FLOW", "system_id": "z", "resource_id": "y"},
        {"phase": "FINDING", "operation": "REPORT_REMAINING_RECORD", "system_id": "b", "resource_id": "x"},
        {"phase": "FINDING", "operation": "REPORT_REMAINING_RECORD", "system_id": "a", "resource_id": "x"},
    ]
    ordered = [(s["operation"], s["system_id"]) for s in sorted(steps, key=step_sort_key)]
    assert ordered == [("REPORT_REMAINING_RECORD", "a"), ("REPORT_REMAINING_RECORD", "b"),
                       ("DISABLE_DATA_FLOW", "z"), ("DELETE_RECORD", "a"), ("VERIFY_RECORD_ABSENT", "a")]


def test_serialization_is_compact_and_key_ordered():
    step = {k: "v" for k in reversed(STEP_KEYS)} | {"step_no": 1, "extra": "dropped"}
    text = serialize_steps([step])
    assert " " not in text
    assert list(json.loads(text)[0]) == list(STEP_KEYS)
