import asyncio
import json

from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer

from erasure_audit.contract import read_csv
from erasure_audit.models import Approval, AuditState, DeletionRequest
from erasure_audit.pipeline import read_requests
from erasure_audit.workflows.activities import AuditActivities
from erasure_audit.workflows.audit_workflow import ErasureAuditWorkflow
from erasure_audit.workflows.types import AuditWorkflowInput, CollectInput, SourceEvidence
from paths import DATASET

SAMPLES = read_requests(DATASET / "sample_deletion_requests.csv")
GOLD = {r["case_id"]: r for r in read_csv(DATASET / "sample_output.csv")}


def sample(status, n=0):
    return DeletionRequest(**[q for q in SAMPLES if GOLD[q["case_id"]]["deletion_status"] == status][n])


async def start(env, queue, request, timeout_s=3600):
    return await env.client.start_workflow(
        ErasureAuditWorkflow.run, AuditWorkflowInput(request=request, approval_timeout_s=timeout_s),
        id=f"audit-{request.case_id}-{queue}", task_queue=queue)


async def wait_for(handle, state, tries=200):
    for _ in range(tries):
        rec = await handle.query(ErasureAuditWorkflow.status)
        if rec and rec.state is state:
            return rec
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {state}; last {rec.state if rec else None}")


async def test_every_sample_matches_the_pipeline(env, make_worker, pipeline):
    queue, _, worker, _ = make_worker()
    async with worker:
        handles = [await start(env, queue, DeletionRequest(**q), timeout_s=1) for q in SAMPLES]
        for q, h in zip(SAMPLES, handles, strict=True):
            rec = await h.result()  # INCOMPLETE ones time out (EXPIRED) after 1 s of skipped time
            status, steps, _ = pipeline.assess(q)
            assert rec.result.deletion_status.value == status
            assert [s.model_dump(mode="json") for s in rec.result.steps] == steps
            assert rec.state is (AuditState.EXPIRED if status == "INCOMPLETE" else AuditState.COMPLETED)


async def test_approval_signal_issues_remediation(env, make_worker):
    queue, outbox, worker, _ = make_worker()
    async with worker:
        h = await start(env, queue, sample("INCOMPLETE"))
        waiting = await wait_for(h, AuditState.AWAITING_APPROVAL)
        assert waiting.result.remediation_steps
        await h.signal(ErasureAuditWorkflow.approve_remediation, Approval(decision="approve", approver="dpo"))
        rec = await h.result()
    assert rec.state is AuditState.REMEDIATION_ISSUED
    assert rec.remediation.steps == waiting.result.remediation_steps
    assert json.loads(outbox.read_text(encoding="utf-8"))["audit_id"] == rec.audit_id


async def test_rejection_and_second_signal_is_ignored(env, make_worker):
    queue, outbox, worker, _ = make_worker()
    async with worker:
        h = await start(env, queue, sample("INCOMPLETE", 1))
        await wait_for(h, AuditState.AWAITING_APPROVAL)
        await h.signal(ErasureAuditWorkflow.approve_remediation, Approval(decision="reject", approver="dpo"))
        await h.signal(ErasureAuditWorkflow.approve_remediation, Approval(decision="approve", approver="late"))
        rec = await h.result()
    assert rec.state is AuditState.REJECTED and rec.approval.approver == "dpo"
    assert not outbox.exists()


async def test_no_decision_expires(env, make_worker):
    queue, _, worker, _ = make_worker()
    async with worker:
        rec = await (await start(env, queue, sample("INCOMPLETE"), timeout_s=7 * 24 * 3600)).result()
    assert rec.state is AuditState.EXPIRED  # a week of waiting, skipped by the test server


async def test_complete_audit_skips_approval(env, make_worker):
    queue, _, worker, _ = make_worker()
    async with worker:
        rec = await (await start(env, queue, sample("COMPLETE"))).result()
    assert rec.state is AuditState.COMPLETED and rec.approval is None


class FlakyLogs(AuditActivities):
    """The logs source fails twice with a transient error, then recovers."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.attempts = []

    @activity.defn(name="collect_evidence")
    def collect_evidence(self, inp: CollectInput) -> SourceEvidence:
        if inp.source == "logs":
            self.attempts.append(activity.info().attempt)
            if activity.info().attempt < 3:
                raise ApplicationError("log store timed out", type="EvidenceUnavailable")
        return super().collect_evidence(inp)


async def test_transient_source_failure_is_retried(env, make_worker, pipeline):
    queue, _, worker, acts = make_worker(FlakyLogs)
    async with worker:
        req = sample("COMPLETE")
        rec = await (await start(env, queue, req)).result()
    assert acts.attempts == [1, 2, 3]  # two transient failures, then success
    assert rec.state is AuditState.COMPLETED
    assert rec.result.deletion_status.value == pipeline.assess(req.as_row())[0]


class CorruptSql(AuditActivities):
    @activity.defn(name="collect_evidence")
    def collect_evidence(self, inp: CollectInput) -> SourceEvidence:
        if inp.source == "sql_snapshots":
            raise ApplicationError("evidence_07 changed since extraction", type="EvidenceCorrupt", non_retryable=True)
        return super().collect_evidence(inp)


async def test_corrupt_source_fails_closed(env, make_worker):
    queue, _, worker, _ = make_worker(CorruptSql)
    async with worker:
        rec = await (await start(env, queue, sample("COMPLETE"))).result()
    assert rec.state is AuditState.FAILED and rec.result is None
    assert "collect_evidence" in rec.error and "changed since extraction" in rec.error


async def test_history_replays_deterministically(env, make_worker):
    queue, _, worker, _ = make_worker()
    async with worker:
        h = await start(env, queue, sample("INCOMPLETE"))
        await wait_for(h, AuditState.AWAITING_APPROVAL)
        await h.signal(ErasureAuditWorkflow.approve_remediation, Approval(decision="approve", approver="dpo"))
        await h.result()
        history = await h.fetch_history()
    await Replayer(workflows=[ErasureAuditWorkflow], data_converter=pydantic_data_converter).replay_workflow(history)


class SlowIssue(AuditActivities):
    """Remediation cannot be issued yet (e.g. the outbox volume is not writable): retried."""

    @activity.defn(name="issue_remediation")
    def issue_remediation(self, inp):
        raise ApplicationError("outbox not writable", type="PermissionError")


async def test_decision_is_visible_while_remediation_is_retried(env, make_worker):
    queue, _, worker, _ = make_worker(SlowIssue)
    async with worker:
        h = await start(env, queue, sample("INCOMPLETE"))
        await wait_for(h, AuditState.AWAITING_APPROVAL)
        await h.signal(ErasureAuditWorkflow.approve_remediation, Approval(decision="approve", approver="dpo"))
        for _ in range(100):
            rec = await h.query(ErasureAuditWorkflow.status)
            if rec.approval:
                break
            await asyncio.sleep(0.05)
        assert rec.approval.approver == "dpo" and rec.state is AuditState.AWAITING_APPROVAL
        await h.terminate("test done")
