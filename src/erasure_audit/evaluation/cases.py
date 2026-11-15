"""Labelled evaluation cases: one request plus its expected verdict and ordered steps.

Two slices exist:
  * `public_sample`: the 32 official answers published with the challenge (dataset/sample_output.csv). They are
    real ground truth, but the pipeline's option defaults were chosen on them, so scores on this slice are
    in-sample. `erasure-audit eval --cv` adds a leave-one-out estimate.
  * `custom`: hand-written cases in eval/cases/custom/*.json (schema: eval/schema/case.schema.json). They are
    held out by construction and reported as their own slice. Only the repository owner writes them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contract import case_sort_key, read_csv
from ..models import DeletionRequest, DeletionStatus, Step

Slice = Literal["public_sample", "custom"]
CUSTOM_DIR = Path("eval") / "cases" / "custom"
SCHEMA_PATH = Path("eval") / "schema" / "case.schema.json"


class LabelledCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slice: Slice = "custom"
    request: DeletionRequest
    expected_status: DeletionStatus
    expected_steps: list[Step] = Field(default_factory=list)
    note: str = Field("", description="Why this case exists / what it tests")

    @property
    def case_id(self) -> str:
        return self.request.case_id

    @model_validator(mode="after")
    def _consistent(self) -> LabelledCase:
        if self.expected_status is DeletionStatus.NOT_APPLICABLE and self.expected_steps:
            raise ValueError("NOT_APPLICABLE cases have no steps")
        if self.expected_status is not DeletionStatus.NOT_APPLICABLE and not self.expected_steps:
            raise ValueError(f"{self.expected_status.value} cases need at least one step")
        numbers = [s.step_no for s in self.expected_steps]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"step_no must run 1..n in order, got {numbers}")
        return self

    def gold_row(self) -> dict:
        """The row shape the metrics expect (same as sample_output.csv, steps decoded)."""
        return {"case_id": self.case_id, "deletion_status": self.expected_status.value,
                "steps": [s.model_dump(mode="json") for s in self.expected_steps]}


def load_public_samples(dataset_dir: Path) -> list[LabelledCase]:
    requests = {r["case_id"]: r for r in read_csv(dataset_dir / "sample_deletion_requests.csv")}
    cases = []
    for row in read_csv(dataset_dir / "sample_output.csv"):
        cases.append(LabelledCase(slice="public_sample", request=DeletionRequest(**requests[row["case_id"]]),
                                  expected_status=row["deletion_status"], expected_steps=json.loads(row["steps_json"])))
    return sorted(cases, key=lambda c: case_sort_key(c.case_id))


def load_custom(directory: Path = CUSTOM_DIR) -> list[LabelledCase]:
    cases = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            case = LabelledCase.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from e
        cases.append(case.model_copy(update={"slice": "custom"}))
    return cases


def load_cases(dataset_dir: Path, case_set: str = "all", custom_dir: Path = CUSTOM_DIR) -> list[LabelledCase]:
    """case_set: public | custom | all."""
    if case_set not in ("public", "custom", "all"):
        raise ValueError(f"unknown case set {case_set!r}; use public, custom or all")
    cases = []
    if case_set in ("public", "all"):
        cases += load_public_samples(dataset_dir)
    if case_set in ("custom", "all"):
        cases += load_custom(custom_dir)
    reserved = {r["case_id"] for name in ("deletion_requests.csv", "sample_deletion_requests.csv")
                for r in read_csv(dataset_dir / name)}
    seen: set[str] = set()
    for c in cases:
        if c.case_id in seen:
            raise ValueError(f"duplicate case_id {c.case_id}")
        if c.slice == "custom" and c.case_id in reserved:
            raise ValueError(f"custom case {c.case_id} reuses a dataset case_id; pick a new id (e.g. custom_01)")
        seen.add(c.case_id)
    return cases


def json_schema() -> dict:
    return LabelledCase.model_json_schema()
