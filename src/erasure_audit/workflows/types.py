"""Activity and workflow payloads (serialised with Temporal's pydantic data converter)."""
from __future__ import annotations

from pydantic import BaseModel

from ..models import Approval, AuditResult, DeletionRequest, Step
from ..pipeline.sources import SourceEvidence


class AuditWorkflowInput(BaseModel):
    request: DeletionRequest
    approval_timeout_s: int = 7 * 24 * 3600


class Identity(BaseModel):
    user_id: str | None
    tokens: list[str]


class CollectInput(BaseModel):
    source: str
    audit_at: str
    tokens: list[str]


class AssessInput(BaseModel):
    request: DeletionRequest
    classification: dict
    sources: list[SourceEvidence]


class IssueInput(BaseModel):
    audit_id: str
    steps: list[Step]
    approval: Approval


__all__ = ["AuditWorkflowInput", "Identity", "CollectInput", "AssessInput", "IssueInput", "AuditResult",
           "SourceEvidence"]
