import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from erasure_audit.remediation import DryRunExecutor
from erasure_audit.service import AuditService
from erasure_audit.workflows.activities import AuditActivities
from erasure_audit.workflows.audit_workflow import ErasureAuditWorkflow
from paths import DATASET


@pytest.fixture(scope="session")
def service():
    return AuditService(DATASET)


@pytest.fixture(scope="session")
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as e:
        yield e


@pytest.fixture(scope="session")
def pool():
    with ThreadPoolExecutor(max_workers=8) as p:
        yield p


@pytest.fixture
def make_worker(env, pool, service, tmp_path):
    """Returns (task_queue, outbox, async-context worker, activities) for an activities class (default: the real one)."""
    def factory(activities_cls=AuditActivities, **kw):
        queue = f"test-{uuid.uuid4().hex[:8]}"
        outbox = tmp_path / f"{queue}.jsonl"
        acts = activities_cls(service, DryRunExecutor(outbox), **kw)
        worker = Worker(env.client, task_queue=queue, workflows=[ErasureAuditWorkflow], activities=acts.all(),
                        activity_executor=pool)
        return queue, outbox, worker, acts
    return factory
