"""ErasureAuditWorkflow: classify -> resolve identity -> collect every evidence source in parallel -> assess ->
(if INCOMPLETE) wait for a human decision -> issue the remediation plan.

Deterministic by construction: no I/O, clocks or randomness here (workflow.now() only); everything else is an
activity. The workflow's state is the audit record, exposed through the `status` query, so Temporal is the
durable store in temporal mode. Activity failures that survive their retry policy end the audit as FAILED
(fail closed) instead of failing the workflow, so the record stays queryable.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from ..models import Approval, ApprovalDecision, AuditRecord, AuditState, DeletionStatus
    from ..pipeline.sources import SOURCES
    from .activities import CLASSIFY_MAX_ATTEMPTS, AuditActivities
    from .types import AssessInput, AuditWorkflowInput, CollectInput, IssueInput

WORKFLOW_NAME = "ErasureAuditWorkflow"

# Per-activity timeouts and retries (start_to_close bounds one attempt; schedule_to_close bounds all of them).
QUICK = dict(start_to_close_timeout=timedelta(seconds=10),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3))
CLASSIFY = dict(start_to_close_timeout=timedelta(seconds=90), schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=5), backoff_coefficient=3.0,
                                         maximum_interval=timedelta(minutes=2), maximum_attempts=CLASSIFY_MAX_ATTEMPTS))
COLLECT = dict(start_to_close_timeout=timedelta(seconds=60), schedule_to_close_timeout=timedelta(minutes=5),
               retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), backoff_coefficient=2.0,
                                        maximum_interval=timedelta(seconds=30), maximum_attempts=5,
                                        non_retryable_error_types=["EvidenceCorrupt", "ValueError"]))
ASSESS = dict(start_to_close_timeout=timedelta(seconds=60),
              retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3))
ISSUE = dict(start_to_close_timeout=timedelta(seconds=30), schedule_to_close_timeout=timedelta(hours=1),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                                      maximum_interval=timedelta(minutes=1)))


def _now() -> str:
    return workflow.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _cause(err: BaseException) -> str:
    while getattr(err, "cause", None) is not None:
        err = err.cause
    return str(err)


@workflow.defn(name=WORKFLOW_NAME)
class ErasureAuditWorkflow:
    def __init__(self) -> None:
        self._record: AuditRecord | None = None
        self._approval: Approval | None = None

    def _update(self, **fields) -> AuditRecord:
        self._record = self._record.model_copy(update={**fields, "updated_at": _now()})
        return self._record

    @workflow.run
    async def run(self, inp: AuditWorkflowInput) -> AuditRecord:
        now = _now()
        self._record = AuditRecord(audit_id=workflow.info().workflow_id, state=AuditState.RUNNING,
                                   request=inp.request, created_at=now, updated_at=now)
        acts = AuditActivities
        stage = "classify_request"
        try:
            classification = await workflow.execute_activity_method(
                acts.classify_request, inp.request.request_text, **CLASSIFY)
            stage = "resolve_identity"
            identity = await workflow.execute_activity_method(acts.resolve_identity, inp.request.email, **QUICK)
            stage = "collect_evidence"
            collected = await asyncio.gather(*(
                workflow.execute_activity_method(
                    acts.collect_evidence, CollectInput(source=s, audit_at=inp.request.audit_at, tokens=identity.tokens),
                    activity_id=f"collect_{s}", **COLLECT)
                for s in SOURCES))
            stage = "assess"
            result = await workflow.execute_activity_method(
                acts.assess, AssessInput(request=inp.request, classification=classification, sources=list(collected)),
                **ASSESS)
        except ActivityError as e:
            return self._update(state=AuditState.FAILED, error=f"{stage}: {_cause(e)}")

        if result.deletion_status is not DeletionStatus.INCOMPLETE:
            return self._update(state=AuditState.COMPLETED, result=result)

        self._update(state=AuditState.AWAITING_APPROVAL, result=result)
        try:
            await workflow.wait_condition(lambda: self._approval is not None,
                                          timeout=timedelta(seconds=inp.approval_timeout_s))
        except TimeoutError:
            return self._update(state=AuditState.EXPIRED, error="no approval decision before the timeout")

        approval = self._approval
        self._update(approval=approval, approved_at=_now())
        if approval.decision is ApprovalDecision.REJECT:
            return self._update(state=AuditState.REJECTED)
        try:
            ticket = await workflow.execute_activity_method(
                acts.issue_remediation,
                IssueInput(audit_id=self._record.audit_id, steps=result.remediation_steps, approval=approval), **ISSUE)
        except ActivityError as e:
            return self._update(state=AuditState.FAILED, error=f"issue_remediation: {_cause(e)}")
        return self._update(state=AuditState.REMEDIATION_ISSUED, remediation=ticket)

    @workflow.signal
    def approve_remediation(self, approval: Approval) -> None:
        """Human decision. Only the first decision on an AWAITING_APPROVAL audit counts; others are ignored."""
        if self._record and self._record.state is AuditState.AWAITING_APPROVAL and self._approval is None:
            self._approval = approval
        else:
            workflow.logger.warning("approval signal ignored", extra={"state": getattr(self._record, "state", None)})

    @workflow.query
    def status(self) -> AuditRecord | None:
        return self._record
