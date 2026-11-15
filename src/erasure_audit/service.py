"""AuditService: the agent behind a typed, in-process interface. One instance per process.

Start-up loads the dataset, the extraction cache and the cross-request account-join context once (about a
second). After that `Pipeline.assess` only reads shared state (every index lookup is a `.get`), so one service
instance can serve concurrent requests without a lock.

Requests whose case_id is in the dataset get the full account-join context. Ad-hoc requests from outside the
dataset are audited with evidence-based linking only, because the workflow join needs the whole batch of
requests up front (docs/DECISIONS.md D3).
"""
from __future__ import annotations

import time
from pathlib import Path

from .contract import read_csv
from .extraction.llm import capture_usage, summarise_usage
from .models import AuditResult, DeletionRequest, Diagnostics, Step, Usage
from .pipeline import Options, Pipeline, build
from .pipeline.assess import ALL_EVIDENCE, EvidenceScope


class AuditService:
    def __init__(self, dataset_dir: Path, options: Options = Options()):
        started = time.perf_counter()
        self.dataset_dir = Path(dataset_dir)
        self.options = options
        self.pipeline: Pipeline = build(self.dataset_dir, options)
        self.rules = read_csv(self.dataset_dir / "deletion_rules.csv")
        self.startup_s = time.perf_counter() - started

    def audit(self, request: DeletionRequest, scope: EvidenceScope = ALL_EVIDENCE,
              classification: dict | None = None) -> AuditResult:
        """Full audit in one call (sync mode). The workflow calls it with the scope its collectors returned and
        the classification its classify activity produced."""
        with capture_usage() as calls:
            t0 = time.perf_counter()
            status, steps, diag = self.pipeline.assess(request.as_row(), scope, classification)
            duration_ms = (time.perf_counter() - t0) * 1000
        usage = summarise_usage(calls)
        usage.pop("api_retries", None)
        return AuditResult(case_id=request.case_id, deletion_status=status, steps=[Step(**s) for s in steps],
                           diagnostics=Diagnostics(**diag), usage=Usage(**usage), duration_ms=duration_ms)
