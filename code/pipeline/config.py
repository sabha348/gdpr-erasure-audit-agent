"""Named, swappable decisions (docs/DECISIONS.md). Defaults are set from the 32-sample sweep
(code/evaluation/sweep.py); every alternative stays selectable with `--option name=value`."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

CHOICES = {
    # DECISIONS #3: which visible file a step cites.
    "citation": (
        "per_resource_latest",    # latest file showing that resource's state; ties -> case coverage -> lowest file_id
        "per_resource_coverage",  # file showing that resource that covers most of the case's resources; ties -> latest
        "case_dominant",          # one file per case where possible (the one supporting most steps), else per_resource_latest
    ),
    # DECISIONS #4: how the state at audit_at is reconstructed from several observations.
    "latest_state": (
        "latest_observation",     # newest observation (by its own timestamp) wins; any copy present in one snapshot = present
        "latest_file",            # observations ordered by file capture time first, then by their own timestamp
        "present_since_request",  # present if any observation after requested_at shows it present (no later override)
    ),
    # DECISIONS #9/#10: what counts as an observation of a data flow / restore path.
    "flow_precedence": (
        "evidence_and_checks",    # files + CHECK_DATA_FLOW / CHECK_RESTORE_ACCESS rows; DISABLE/BLOCK "SUCCESS" rows are claims
        "evidence_only",          # files only; activity rows ignored
        "claims_as_observations", # also treat DISABLE_DATA_FLOW / BLOCK_RESTORE SUCCESS rows as observations
    ),
    # Observations made before the request was submitted (SAMPLE_ANALYSIS R25: story fixtures for other cases).
    "stale_evidence": (
        "keep",                   # every visible observation counts
        "drop_before_request",    # only observations at/after requested_at establish state
    ),
    # SAMPLE_ANALYSIS R9: where the treatment of a record comes from.
    "treatment": (
        "policy_with_activity_hint",  # deletion_rules category; a deletion_worker DELETE/REDACT/ANONYMIZE row on the resource overrides
        "policy_only",                # deletion_rules category only
    ),
    # How accounts with no identifying evidence are found (DECISIONS: Account linking).
    "account_linking": (
        "evidence_and_workflow_join",   # evidence first; else exact join on the workflow's account row (reported_at window + status)
        "evidence_only",
    ),
    # DECISIONS #1: resource_id order inside one (phase, operation, system_id).
    "resource_order": ("string", "numeric"),
    # DECISIONS #15: request classification backend (LLM backend to be plugged in later).
    "classifier": ("rules",),
    # Screenshots without a model transcription: rule-based fallback (low confidence) or ignore.
    "image_fallback": ("rules", "off"),
}


@dataclass(frozen=True)
class Options:
    citation: str = "per_resource_latest"
    latest_state: str = "latest_observation"
    flow_precedence: str = "evidence_and_checks"
    stale_evidence: str = "drop_before_request"
    treatment: str = "policy_with_activity_hint"
    account_linking: str = "evidence_and_workflow_join"
    resource_order: str = "string"
    classifier: str = "rules"
    image_fallback: str = "rules"

    def __post_init__(self):
        for f in fields(self):
            if getattr(self, f.name) not in CHOICES[f.name]:
                raise ValueError(f"{f.name}={getattr(self, f.name)!r}; choose from {CHOICES[f.name]}")

    def with_(self, **kw) -> "Options":
        return replace(self, **kw)

    @classmethod
    def parse(cls, pairs: list[str]) -> "Options":
        kw = {}
        for p in pairs or []:
            k, _, v = p.partition("=")
            if k not in CHOICES:
                raise ValueError(f"unknown option {k!r}; known: {sorted(CHOICES)}")
            kw[k] = v
        return cls(**kw)
