"""TemporalBackend: each audit is an ErasureAuditWorkflow; the workflow's state is the audit record.

* submit  -> start_workflow(id=audit-<case_id>). Temporal rejects a second workflow with the same id, which
             gives the same idempotency as SyncBackend: identical request -> existing audit, different -> 409.
             The call then waits up to ERASURE_SUBMIT_WAIT_S for a first verdict, so most clients get the
             result in one round trip; slower audits come back RUNNING and are polled with GET.
* get     -> the `status` query (works on running and closed workflows while a worker is up).
* decide  -> checks AWAITING_APPROVAL, sends the approve_remediation signal, waits briefly for the outcome.
* list    -> workflow visibility (WorkflowType filter), then one query per audit.
"""
from __future__ import annotations

import asyncio
import time

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ..models import Approval, AuditRecord, AuditState, DeletionRequest
from ..remediation import utc_now
from ..settings import Settings
from ..workflows.audit_workflow import WORKFLOW_NAME, ErasureAuditWorkflow
from ..workflows.types import AuditWorkflowInput
from .backend import audit_id_for
from .errors import AuditNotFound, InvalidState, NotReady, RequestConflict


class TemporalBackend:
    name = "temporal"

    def __init__(self, client: Client, settings: Settings):
        self.client, self.settings = client, settings

    async def _query(self, handle: WorkflowHandle, request: DeletionRequest | None = None) -> AuditRecord:
        try:
            record = await handle.query(ErasureAuditWorkflow.status)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise AuditNotFound(f"no audit {handle.id}") from e
            raise NotReady(f"Temporal query failed: {e.message}") from e
        if record is None:  # started, first workflow task not processed yet
            if request is None:
                raise NotReady(f"{handle.id} has not started yet")
            now = utc_now()
            record = AuditRecord(audit_id=handle.id, state=AuditState.RUNNING, request=request, created_at=now,
                                 updated_at=now)
        return record

    async def _wait(self, handle: WorkflowHandle, request: DeletionRequest | None, leave: AuditState) -> AuditRecord:
        deadline = time.monotonic() + self.settings.submit_wait_s
        record = await self._query(handle, request)
        while record.state is leave and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            record = await self._query(handle, request)
        return record

    async def submit(self, request: DeletionRequest) -> tuple[AuditRecord, bool]:
        audit_id = audit_id_for(request.case_id)
        try:
            handle = await self.client.start_workflow(
                ErasureAuditWorkflow.run,
                AuditWorkflowInput(request=request, approval_timeout_s=self.settings.approval_timeout_s),
                id=audit_id, task_queue=self.settings.temporal_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            existing = await self._query(self.client.get_workflow_handle(audit_id))
            if existing.request != request:
                raise RequestConflict(f"case_id {request.case_id} was already submitted with different fields") from None
            return existing, False
        except RPCError as e:
            raise NotReady(f"Temporal unavailable: {e.message}") from e
        return await self._wait(handle, request, AuditState.RUNNING), True

    async def get(self, audit_id: str) -> AuditRecord:
        return await self._query(self.client.get_workflow_handle(audit_id))

    async def list(self, state: AuditState | None = None, limit: int = 100) -> list[AuditRecord]:
        records = []
        async for wf in self.client.list_workflows(f'WorkflowType="{WORKFLOW_NAME}"', limit=limit * 2):
            try:
                rec = await self._query(self.client.get_workflow_handle(wf.id))
            except (AuditNotFound, NotReady):
                continue
            if state is None or rec.state is state:
                records.append(rec)
            if len(records) >= limit:
                break
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    async def decide(self, audit_id: str, approval: Approval) -> AuditRecord:
        handle = self.client.get_workflow_handle(audit_id)
        record = await self._query(handle)
        if record.state is not AuditState.AWAITING_APPROVAL:
            raise InvalidState(f"{audit_id} is {record.state.value}; only AWAITING_APPROVAL audits can be decided")
        await handle.signal(ErasureAuditWorkflow.approve_remediation, approval)
        return await self._wait(handle, None, AuditState.AWAITING_APPROVAL)

    async def ready(self) -> bool:
        try:
            return await self.client.service_client.check_health()
        except RPCError:
            return False
