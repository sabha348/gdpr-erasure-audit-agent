from erasure_audit.models import AuditResult, Diagnostics, Step
from erasure_audit.pipeline import read_requests
from paths import DATASET


def test_every_pipeline_result_fits_the_typed_models(pipeline):
    """Typed models must accept everything the pipeline produces on all 550 known requests."""
    for name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        for q in read_requests(DATASET / name):
            status, steps, diag = pipeline.assess(q)
            result = AuditResult(case_id=q["case_id"], deletion_status=status, steps=[Step(**s) for s in steps],
                                 diagnostics=Diagnostics(**diag), duration_ms=0.0)
            assert [s.model_dump(mode="json") for s in result.steps] == steps
