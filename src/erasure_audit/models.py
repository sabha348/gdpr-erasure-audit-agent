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


Operation = StrEnum("Operation", {op: op for op in OPERATIONS})
State = StrEnum("State", {s: s for s in sorted({state for _, state in OPERATIONS.values()})})


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
