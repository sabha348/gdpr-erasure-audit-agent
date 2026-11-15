"""Scorer arithmetic on small synthetic plans (these test the metric code, they are not evaluation labels)."""
from erasure_audit.evaluation.metrics import aggregate, plan_violations, prf, score_case


def step(no, phase, op, state, rid, ev=""):
    return {"step_no": no, "phase": phase, "system_id": "sys", "resource_id": rid, "operation": op, "state": state,
            "evidence_id": ev}


GOLD = {"case_id": "c1", "deletion_status": "INCOMPLETE", "steps": [
    step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "r1", "e1"),
    step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT", "r1"),
    step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "r1"),
]}


def test_perfect_prediction():
    s = score_case(GOLD, "INCOMPLETE", GOLD["steps"])
    assert s["exact"] and s["step_f1"] == 1.0 and not s["plan_violations"]


def test_wrong_evidence_costs_one_step_and_one_field():
    pred = [dict(GOLD["steps"][0], evidence_id="e9"), *GOLD["steps"][1:]]
    s = score_case(GOLD, "INCOMPLETE", pred)
    assert (s["tp"], s["tp_ignoring_evidence"]) == (2, 3)
    assert s["field_ok"]["evidence_id"] == 2 and s["field_ok"]["operation"] == 3
    assert not s["exact"]


def test_per_phase_counts_and_aggregate():
    s = score_case(GOLD, "INCOMPLETE", GOLD["steps"][:1])  # findings only
    m = aggregate([s])
    assert m["per_phase"]["FINDING"]["f1"] == 1.0
    assert m["remediation_f1"] == 0.0
    assert m["step_recall"] == 1 / 3 and m["step_precision"] == 1.0


def test_prf_edge_cases():
    assert prf(0, 0, 0)["f1"] == 1.0  # nothing expected, nothing predicted
    assert prf(0, 2, 0)["recall"] == 0.0


def test_plan_violations_catch_unsafe_order_and_missing_checks():
    plan = [
        step(1, "REMEDIATION", "DELETE_RECORD", "ABSENT", "r1"),
        step(2, "REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED", "flow1"),
        step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "r1"),
    ]
    v = plan_violations(plan)
    assert any("before every data flow" in x for x in v)
    assert any("flow1" in x for x in v)
