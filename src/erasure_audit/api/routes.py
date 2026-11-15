from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from ..models import Approval, AuditRecord, AuditState, DeletionRequest
from .backend import AuditBackend
from .deps import get_backend
from .errors import BatchTooLarge, NotReady

router = APIRouter()
Backend = Annotated[AuditBackend, Depends(get_backend)]


class BatchRequest(BaseModel):
    requests: list[DeletionRequest] = Field(min_length=1)


class BatchResponse(BaseModel):
    audits: list[AuditRecord]


class PolicyRule(BaseModel):
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@router.get("/healthz", tags=["ops"], summary="Liveness")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"], summary="Readiness: dataset loaded and backend reachable")
async def readyz(request: Request) -> dict:
    backend = getattr(request.app.state, "backend", None)
    if backend is None or not await backend.ready():
        raise NotReady("backend not ready")
    service = request.app.state.service
    return {"status": "ready", "backend": backend.name, "startup_s": round(service.startup_s, 3),
            "classifier": service.options.classifier}


@router.post("/v1/audits", tags=["audits"], response_model=AuditRecord, status_code=status.HTTP_201_CREATED,
             responses={200: {"description": "Same request already submitted; existing audit returned"},
                        409: {"description": "case_id reused with different fields"}})
async def create_audit(body: DeletionRequest, backend: Backend, response: Response) -> AuditRecord:
    record, created = await backend.submit(body)
    if not created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/audits/{record.audit_id}"
    return record


@router.post("/v1/audits/batch", tags=["audits"], response_model=BatchResponse)
async def create_audits(body: BatchRequest, backend: Backend, request: Request) -> BatchResponse:
    limit = request.app.state.settings.batch_limit
    if len(body.requests) > limit:
        raise BatchTooLarge(f"{len(body.requests)} requests; the limit is {limit}")
    return BatchResponse(audits=[(await backend.submit(r))[0] for r in body.requests])


@router.get("/v1/audits", tags=["audits"], response_model=list[AuditRecord])
async def list_audits(backend: Backend, state: AuditState | None = None,
                      limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[AuditRecord]:
    return await backend.list(state, limit)


@router.get("/v1/audits/{audit_id}", tags=["audits"], response_model=AuditRecord)
async def get_audit(audit_id: str, backend: Backend) -> AuditRecord:
    return await backend.get(audit_id)


@router.post("/v1/audits/{audit_id}/approval", tags=["audits"], response_model=AuditRecord,
             summary="Human approval or rejection of the remediation plan",
             responses={409: {"description": "Audit is not awaiting approval"}})
async def decide(audit_id: str, body: Approval, backend: Backend) -> AuditRecord:
    return await backend.decide(audit_id, body)


@router.get("/v1/policy/rules", tags=["policy"], response_model=list[PolicyRule])
async def policy_rules(request: Request) -> list[PolicyRule]:
    return [PolicyRule(**r) for r in request.app.state.service.rules]
