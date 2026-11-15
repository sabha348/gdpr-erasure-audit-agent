"""Typed domain models shared by the evaluation harness, the API and the workflows.

The string values are the output contract of the challenge (contract.py) and must not change.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contract import OPERATIONS

TIMESTAMP = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"  # UTC, second precision; compared as strings throughout


class Phase(StrEnum):
    FINDING = "FINDING"
    REMEDIATION = "REMEDIATION"
    VERIFICATION = "VERIFICATION"


class Operation(StrEnum):
    """Written out (not generated) so type checkers see the members; a test keeps it equal to contract.OPERATIONS."""
    REPORT_REMAINING_RECORD = "REPORT_REMAINING_RECORD"
    REPORT_EXPOSED_PII = "REPORT_EXPOSED_PII"
    REPORT_ACTIVE_DATA_FLOW = "REPORT_ACTIVE_DATA_FLOW"
    REPORT_RESTORE_RISK = "REPORT_RESTORE_RISK"
    DISABLE_DATA_FLOW = "DISABLE_DATA_FLOW"
    BLOCK_RESTORE = "BLOCK_RESTORE"
    DELETE_RECORD = "DELETE_RECORD"
    REDACT_RECORD = "REDACT_RECORD"
    ANONYMIZE_RECORD = "ANONYMIZE_RECORD"
    VERIFY_RECORD_ABSENT = "VERIFY_RECORD_ABSENT"
    VERIFY_RECORD_PRESENT = "VERIFY_RECORD_PRESENT"
    VERIFY_RECORD_REDACTED = "VERIFY_RECORD_REDACTED"
    VERIFY_RECORD_ANONYMIZED = "VERIFY_RECORD_ANONYMIZED"
    VERIFY_DATA_FLOW_DISABLED = "VERIFY_DATA_FLOW_DISABLED"
    VERIFY_RESTORE_BLOCKED = "VERIFY_RESTORE_BLOCKED"


class State(StrEnum):
    ABSENT = "ABSENT"
    ACTIVE = "ACTIVE"
    ANONYMIZED = "ANONYMIZED"
    BLOCKED = "BLOCKED"
    DISABLED = "DISABLED"
    PRESENT = "PRESENT"
    REDACTED = "REDACTED"


class DeletionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReportedStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIALLY_COMPLETE = "PARTIALLY_COMPLETE"
    NOT_STARTED = "NOT_STARTED"


class Step(BaseModel):
    """One finding, remediation or verification step. The operation fixes the phase and the state."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_no: int = Field(ge=1)
    phase: Phase
    system_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    operation: Operation
    state: State
    evidence_id: str = ""

    @model_validator(mode="after")
    def _operation_fixes_phase_and_state(self) -> Step:
        phase, state = OPERATIONS[self.operation.value]
        if (self.phase.value, self.state.value) != (phase, state):
            raise ValueError(f"{self.operation.value} requires phase={phase} state={state}")
        if self.phase is Phase.REMEDIATION and self.evidence_id:
            raise ValueError("remediation steps describe future work and carry no evidence_id")
        return self


class DeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=3)
    requested_at: str = Field(pattern=TIMESTAMP)
    reported_status: ReportedStatus
    reported_at: str = Field(pattern=TIMESTAMP)
    audit_at: str = Field(pattern=TIMESTAMP)
    request_text: str = Field(min_length=1)

    def as_row(self) -> dict[str, str]:
        """The CSV-row shape the pipeline consumes."""
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in self.model_dump().items()}


# ------------------------------------------------------------------------------------------- audit results
class Classification(BaseModel):
    model_config = ConfigDict(extra="allow")

    is_deletion: bool
    method: str = Field(description="rules | llm | rules_fallback")
    confidence: str


class ResourceAssessment(BaseModel):
    category: str
    action: str = Field(description="Policy action from deletion_rules.csv: DELETE, REDACT, ANONYMIZE, RETAIN")
    state: str | None = Field(None, description="Reconstructed state at audit_at")
    evidence: list[str] = Field(default_factory=list)


class Diagnostics(BaseModel):
    """Why the agent decided what it decided; every uncertain decision is listed in low_confidence."""
    model_config = ConfigDict(extra="allow")

    subject: str | None = Field(None, description="Resolved stable user_id")
    classification: Classification
    account: dict | None = None
    resources: dict[str, ResourceAssessment] = Field(default_factory=dict)
    low_confidence: list[str] = Field(default_factory=list)
    error: str | None = None


class Usage(BaseModel):
    llm_calls: int = 0
    cached_llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class AuditResult(BaseModel):
    case_id: str
    deletion_status: DeletionStatus
    steps: list[Step]
    diagnostics: Diagnostics
    usage: Usage = Field(default_factory=Usage)
    duration_ms: float

    @property
    def remediation_steps(self) -> list[Step]:
        return [s for s in self.steps if s.phase is Phase.REMEDIATION]


# ------------------------------------------------------------------------------------------- lifecycle
class AuditState(StrEnum):
    """RUNNING -> COMPLETED (nothing to remediate) | AWAITING_APPROVAL -> REMEDIATION_ISSUED | REJECTED | EXPIRED;
    any step can end in FAILED."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATION_ISSUED = "REMEDIATION_ISSUED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    approver: str = Field(min_length=1, max_length=200, description="Who decided (user id or email)")
    comment: str = Field("", max_length=2000)


class RemediationTicket(BaseModel):
    ticket_id: str
    executor: str = Field(description="Which RemediationExecutor issued it, e.g. dry_run")
    issued_at: str
    steps: list[Step]


class AuditRecord(BaseModel):
    audit_id: str
    state: AuditState
    request: DeletionRequest
    result: AuditResult | None = None
    approval: Approval | None = None
    approved_at: str | None = None
    remediation: RemediationTicket | None = None
    error: str | None = None
    created_at: str
    updated_at: str
