"""AuditBackend: how the API runs an audit and its approval step.

SyncBackend runs the audit in-process (in a worker thread, so a slow model call never blocks the event loop) and
keeps records in an AuditStore. TemporalBackend (M3) starts a workflow per audit and signals it on approval. Both
expose the same lifecycle, so routes and clients don't change between them.

audit_id is derived from case_id ("audit-<case_id>"). Resubmitting the same request returns the existing audit
instead of starting a second one; reusing a case_id with a different request is a conflict. Temporal's
workflow-id uniqueness gives the same guarantee in M3.
"""
from __future__ import annotations

from typing import Protocol

import anyio

from ..models import Approval, ApprovalDecision, AuditRecord, AuditState, DeletionRequest, DeletionStatus
from ..remediation import RemediationExecutor, utc_now
from ..service import AuditService
from .errors import AuditNotFound, InvalidState, RequestConflict
from .store import AuditStore


def audit_id_for(case_id: str) -> str:
    return f"audit-{case_id}"


class AuditBackend(Protocol):
    name: str

    async def submit(self, request: DeletionRequest) -> tuple[AuditRecord, bool]: ...
    async def get(self, audit_id: str) -> AuditRecord: ...
    async def list(self, state: AuditState | None = None, limit: int = 100) -> list[AuditRecord]: ...
    async def decide(self, audit_id: str, approval: Approval) -> AuditRecord: ...
    async def ready(self) -> bool: ...


class SyncBackend:
    name = "sync"

    def __init__(self, service: AuditService, store: AuditStore, executor: RemediationExecutor):
        self.service, self.store, self.executor = service, store, executor

    async def submit(self, request: DeletionRequest) -> tuple[AuditRecord, bool]:
        now = utc_now()
        placeholder = AuditRecord(audit_id=audit_id_for(request.case_id), state=AuditState.RUNNING, request=request,
                                  created_at=now, updated_at=now)
        record, created = self.store.put_if_absent(placeholder)
        if not created:
            if record.request != request:
                raise RequestConflict(f"case_id {request.case_id} was already submitted with different fields")
            return record, False
        try:
            result = await anyio.to_thread.run_sync(self.service.audit, request)
        except Exception as e:  # recorded on the audit, not leaked as a 500 with a traceback
            record = record.model_copy(update={"state": AuditState.FAILED, "error": f"{type(e).__name__}: {e}",
                                               "updated_at": utc_now()})
            self.store.put(record)
            return record, True
        state = (AuditState.AWAITING_APPROVAL if result.deletion_status is DeletionStatus.INCOMPLETE
                 else AuditState.COMPLETED)
        record = record.model_copy(update={"state": state, "result": result, "updated_at": utc_now()})
        self.store.put(record)
        return record, True

    async def get(self, audit_id: str) -> AuditRecord:
        record = self.store.get(audit_id)
        if record is None:
            raise AuditNotFound(f"no audit {audit_id}")
        return record

    async def list(self, state: AuditState | None = None, limit: int = 100) -> list[AuditRecord]:
        return self.store.list(state, limit)

    async def decide(self, audit_id: str, approval: Approval) -> AuditRecord:
        record = await self.get(audit_id)
        if record.state is not AuditState.AWAITING_APPROVAL:
            raise InvalidState(f"{audit_id} is {record.state.value}; only AWAITING_APPROVAL audits can be decided")
        now = utc_now()
        update = {"approval": approval, "approved_at": now, "updated_at": now}
        if approval.decision is ApprovalDecision.APPROVE:
            assert record.result is not None  # AWAITING_APPROVAL always carries a result
            ticket = await anyio.to_thread.run_sync(self.executor.issue, audit_id, record.result.remediation_steps,
                                                    approval)
            update |= {"state": AuditState.REMEDIATION_ISSUED, "remediation": ticket}
        else:
            update |= {"state": AuditState.REJECTED}
        record = record.model_copy(update=update)
        self.store.put(record)
        return record

    async def ready(self) -> bool:
        return True
