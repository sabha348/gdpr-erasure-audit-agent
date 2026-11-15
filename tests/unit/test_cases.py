import json

import pytest
from pydantic import ValidationError

from erasure_audit.evaluation.cases import LabelledCase, json_schema, load_cases, load_custom
from erasure_audit.models import Step
from paths import DATASET, ROOT


def test_public_samples_load_as_32_labelled_cases():
    cases = load_cases(DATASET, "public")
    assert len(cases) == 32
    assert {c.slice for c in cases} == {"public_sample"}


def test_checked_in_schema_matches_model():
    on_disk = json.loads((ROOT / "eval" / "schema" / "case.schema.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(json_schema(), sort_keys=True))


def _as_custom(case: LabelledCase, case_id: str) -> dict:
    data = case.model_dump(mode="json")
    data["request"]["case_id"] = case_id
    return data


def test_custom_cases_reject_reused_dataset_ids(tmp_path):
    sample = load_cases(DATASET, "public")[0]
    (tmp_path / "a.json").write_text(json.dumps(_as_custom(sample, sample.case_id)), encoding="utf-8")
    with pytest.raises(ValueError, match="reuses a dataset case_id"):
        load_cases(DATASET, "custom", custom_dir=tmp_path)


def test_custom_loader_reports_the_bad_file(tmp_path):
    sample = load_cases(DATASET, "public")[4]
    bad = _as_custom(sample, "custom_x")
    bad["expected_status"] = "NOT_APPLICABLE"
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        load_custom(tmp_path)


def test_step_operation_fixes_phase_and_state():
    with pytest.raises(ValidationError):
        Step(step_no=1, phase="FINDING", system_id="s", resource_id="r", operation="DELETE_RECORD", state="ABSENT")
    with pytest.raises(ValidationError):
        Step(step_no=1, phase="REMEDIATION", system_id="s", resource_id="r", operation="DELETE_RECORD",
             state="ABSENT", evidence_id="evidence_01")
