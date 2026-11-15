"""Activities: everything that does I/O or may fail lives here; the workflow only orchestrates.

All activities are synchronous and run on the worker's thread pool. They share one AuditService per worker
process (dataset, cache and indexes loaded once). Error mapping:
  * EvidenceUnavailable, transient model errors -> retryable ApplicationError (Temporal retries per policy);
  * EvidenceCorrupt, invalid input              -> non-retryable ApplicationError (fail closed at once).
"""
from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..models import RemediationTicket
from ..pipeline.classify import classify_with
from ..pipeline.sources import EvidenceCorrupt, EvidenceUnavailable, collect, scope_from
from ..remediation import RemediationExecutor
from ..service import AuditService
from .types import AssessInput, AuditResult, CollectInput, Identity, IssueInput, SourceEvidence

CLASSIFY_MAX_ATTEMPTS = 4  # keep in sync with the workflow's retry policy for classify_request


class AuditActivities:
    def __init__(self, service: AuditService, executor: RemediationExecutor):
        self.service = service
        self.executor = executor

    @activity.defn(name="classify_request")
    def classify_request(self, request_text: str) -> dict:
        result = classify_with(self.service.options.classifier, request_text)
        # A transient model failure is retried by Temporal; only on the last attempt is the rules fallback accepted.
        if result.get("llm_error_kind") == "transient" and activity.info().attempt < CLASSIFY_MAX_ATTEMPTS:
            raise ApplicationError(f"classifier model failed: {result['llm_error']}", type="TransientModelError")
        return result

    @activity.defn(name="resolve_identity")
    def resolve_identity(self, email: str) -> Identity:
        user_id, tokens = self.service.pipeline.subject(email)
        return Identity(user_id=user_id, tokens=sorted(tokens))

    @activity.defn(name="collect_evidence")
    def collect_evidence(self, inp: CollectInput) -> SourceEvidence:
        try:
            return collect(self.service.pipeline, inp.source, inp.audit_at, set(inp.tokens))
        except EvidenceUnavailable as e:
            raise ApplicationError(str(e), type="EvidenceUnavailable") from e
        except (EvidenceCorrupt, ValueError) as e:
            raise ApplicationError(str(e), type=type(e).__name__, non_retryable=True) from e

    @activity.defn(name="assess")
    def assess(self, inp: AssessInput) -> AuditResult:
        return self.service.audit(inp.request, scope_from(inp.sources), inp.classification)

    @activity.defn(name="issue_remediation")
    def issue_remediation(self, inp: IssueInput) -> RemediationTicket:
        return self.executor.issue(inp.audit_id, inp.steps, inp.approval)  # idempotent per audit: safe to retry

    def all(self) -> list:
        return [self.classify_request, self.resolve_identity, self.collect_evidence, self.assess,
                self.issue_remediation]
