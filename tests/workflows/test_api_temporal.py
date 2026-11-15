"""The HTTP API in temporal mode against a real local Temporal dev server and an in-process worker."""
import uuid

import httpx
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from erasure_audit.api.app import create_app
from erasure_audit.api.temporal_backend import TemporalBackend
from erasure_audit.contract import read_csv
from erasure_audit.pipeline import read_requests
from erasure_audit.settings import Settings
from erasure_audit.workflows.worker import build_worker
from paths import DATASET

SAMPLES = read_requests(DATASET / "sample_deletion_requests.csv")
GOLD = {r["case_id"]: r for r in read_csv(DATASET / "sample_output.csv")}


def first(status):
    return next(q for q in SAMPLES if GOLD[q["case_id"]]["deletion_status"] == status)


@pytest.fixture(scope="session")
async def local_env():
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as e:
        yield e


@pytest.fixture
async def api(local_env, service, pool, tmp_path):
    from erasure_audit.remediation import DryRunExecutor
    from erasure_audit.workflows.activities import AuditActivities

    settings = Settings(_env_file=None, ERASURE_DATASET_DIR=str(DATASET), ERASURE_CACHE_DIR=str(tmp_path),
                        ERASURE_EXECUTION_MODE="temporal", TEMPORAL_TASK_QUEUE=f"api-{uuid.uuid4().hex[:8]}",
                        ERASURE_SUBMIT_WAIT_S=20, ERASURE_LOG_JSON=False)
    acts = AuditActivities(service, DryRunExecutor(settings.remediation_outbox))
    app = create_app(settings, service=service, backend=TemporalBackend(local_env.client, settings),
                     configure_logging=False)
    async with build_worker(local_env.client, settings, acts, executor=pool), app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
            client.settings = settings
            yield client


async def test_ready_reports_temporal_backend(api):
    body = (await api.get("/readyz")).json()
    assert body["backend"] == "temporal" and body["status"] == "ready"


async def test_approval_flow_over_http(api, pipeline):
    q = {**first("INCOMPLETE"), "case_id": f"t-{uuid.uuid4().hex[:6]}"}
    created = await api.post("/v1/audits", json=q)
    assert created.status_code == 201, created.text
    rec = created.json()
    assert rec["state"] == "AWAITING_APPROVAL"
    assert rec["result"]["steps"] == pipeline.assess(q)[1]

    again = await api.post("/v1/audits", json=q)
    assert again.status_code == 200 and again.json()["audit_id"] == rec["audit_id"]
    assert (await api.post("/v1/audits", json={**q, "request_text": "different"})).status_code == 409

    decided = await api.post(f"/v1/audits/{rec['audit_id']}/approval", json={"decision": "approve", "approver": "dpo"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "REMEDIATION_ISSUED"
    assert (await api.get(f"/v1/audits/{rec['audit_id']}")).json()["remediation"]["executor"] == "dry_run"
    assert (await api.post(f"/v1/audits/{rec['audit_id']}/approval",
                           json={"decision": "approve", "approver": "x"})).status_code == 409


async def test_complete_audit_and_not_found(api):
    q = {**first("COMPLETE"), "case_id": f"t-{uuid.uuid4().hex[:6]}"}
    assert (await api.post("/v1/audits", json=q)).json()["state"] == "COMPLETED"
    assert (await api.get("/v1/audits/audit-does-not-exist")).status_code == 404
