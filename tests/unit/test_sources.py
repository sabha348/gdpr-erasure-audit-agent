import shutil

import pytest

from erasure_audit.pipeline import Options, build, read_requests
from erasure_audit.pipeline.sources import (
    ACTIVITY_SOURCE,
    SOURCES,
    EvidenceCorrupt,
    EvidenceUnavailable,
    collect,
    scope_from,
    source_of,
)
from paths import DATASET

REQUESTS = read_requests(DATASET / "sample_deletion_requests.csv") + read_requests(DATASET / "deletion_requests.csv")


def test_every_evidence_file_belongs_to_a_source(pipeline):
    assert all(source_of(d["file_path"]) for d in pipeline.ds.docs.values())


def test_scoped_assessment_with_all_sources_equals_full_assessment(pipeline):
    """The workflow path (collect every source, assess on that scope) must not change a single answer."""
    for q in REQUESTS:
        _, tokens = pipeline.subject(q["email"])
        scope = scope_from([collect(pipeline, s, q["audit_at"], tokens) for s in SOURCES])
        assert pipeline.assess(q, scope)[:2] == pipeline.assess(q)[:2], q["case_id"]


def test_dropping_a_source_changes_answers(pipeline):
    """Sanity check that the scope is really applied: without SQL snapshots some audits change."""
    changed = 0
    for q in REQUESTS[:60]:
        _, tokens = pipeline.subject(q["email"])
        partial = [collect(pipeline, s, q["audit_at"], tokens) for s in SOURCES if s != "sql_snapshots"]
        changed += pipeline.assess(q, scope_from(partial))[:2] != pipeline.assess(q)[:2]
    assert changed > 0


def test_activity_source_counts_rows_up_to_audit_at(pipeline):
    q = REQUESTS[0]
    got = collect(pipeline, ACTIVITY_SOURCE, q["audit_at"], set())
    assert got.activity_rows == sum(a["event_time"] <= q["audit_at"] for a in pipeline.ds.activity)


@pytest.fixture
def private_pipeline(tmp_path):
    copy = tmp_path / "dataset"
    shutil.copytree(DATASET, copy)
    return build(copy, Options()), copy


def test_changed_evidence_is_corrupt_and_missing_evidence_is_unavailable(private_pipeline):
    pipe, root = private_pipeline
    q = REQUESTS[-1]
    doc = next(d for d in pipe.ds.docs.values() if source_of(d["file_path"]) == "logs" and d["captured_at"] <= q["audit_at"])
    path = root / doc["file_path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(EvidenceCorrupt):
        collect(pipe, "logs", q["audit_at"], set())
    path.unlink()
    with pytest.raises(EvidenceUnavailable):
        collect(pipe, "logs", q["audit_at"], set())
