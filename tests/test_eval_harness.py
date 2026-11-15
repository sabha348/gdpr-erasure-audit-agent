from conftest import DATASET
from erasure_audit.evaluation.cases import load_cases
from erasure_audit.evaluation.cv import leave_one_out
from erasure_audit.evaluation.report import write
from erasure_audit.evaluation.runner import run_cases, summarise_by_slice
from erasure_audit.extraction.llm import capture_usage, record_usage


def test_usage_capture_is_scoped():
    record_usage({"input_tokens": 99})  # outside any capture: dropped
    with capture_usage() as outer:
        record_usage({"input_tokens": 1})
        with capture_usage() as inner:
            record_usage({"input_tokens": 2})
    assert [c["input_tokens"] for c in outer] == [1]
    assert [c["input_tokens"] for c in inner] == [2]


def test_eval_run_writes_reports(tmp_path):
    cases = load_cases(DATASET, "public")
    results, info = run_cases(cases, DATASET)
    by_slice = summarise_by_slice(results)
    assert len(results) == 32
    assert by_slice["public_sample"]["status_accuracy"] == 1.0
    assert all(r.latency_ms > 0 for r in results)
    path = write(tmp_path, results, by_slice, info)
    text = path.read_text(encoding="utf-8")
    assert "## Summary" in text and "case_01" in text
    assert (tmp_path / "results.csv").read_text(encoding="utf-8").count("\n") == 33


def test_leave_one_out_scores_every_fold():
    cv = leave_one_out(load_cases(DATASET, "public"), DATASET)
    assert cv["folds"] == 32
    assert cv["held_out"]["cases"] == 32
    assert 0.0 <= cv["held_out"]["step_f1"] <= 1.0
