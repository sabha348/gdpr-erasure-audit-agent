import pytest

from conftest import DATASET
from erasure_audit.models import Approval, DeletionRequest
from erasure_audit.pipeline import read_requests
from erasure_audit.remediation import DryRunExecutor
from erasure_audit.service import AuditService


@pytest.fixture(scope="module")
def service():
    return AuditService(DATASET)


def test_service_matches_pipeline(service, pipeline):
    for q in read_requests(DATASET / "sample_deletion_requests.csv"):
        result = service.audit(DeletionRequest(**q))
        status, steps, _ = pipeline.assess(q)
        assert result.deletion_status.value == status
        assert [s.model_dump(mode="json") for s in result.steps] == steps
        assert result.duration_ms > 0 and result.usage.llm_calls == 0


def test_dry_run_executor_is_idempotent(tmp_path, service):
    q = next(q for q in read_requests(DATASET / "sample_deletion_requests.csv")
             if service.audit(DeletionRequest(**q)).deletion_status.value == "INCOMPLETE")
    steps = service.audit(DeletionRequest(**q)).remediation_steps
    ex = DryRunExecutor(tmp_path / "outbox.jsonl")
    approval = Approval(decision="approve", approver="dpo@example.test")
    first = ex.issue("audit-x", steps, approval)
    second = ex.issue("audit-x", steps, approval)
    assert first == second
    assert (tmp_path / "outbox.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert all(s.phase.value == "REMEDIATION" for s in first.steps)
