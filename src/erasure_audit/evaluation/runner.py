"""Instrumented evaluation run: every labelled case goes through the same `Pipeline.assess` the CLI and the API
use, timed with `perf_counter` and with every model call captured (tokens, cost) per case."""
from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..extraction.llm import capture_usage
from ..pipeline import Options, build
from .cases import LabelledCase
from .metrics import aggregate, score_case


@dataclass
class CaseResult:
    case_id: str
    slice: str
    gold_status: str
    pred_status: str
    score: dict
    latency_ms: float
    llm_calls: int = 0
    cached_llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    low_confidence: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


def _usage(calls: list[dict]) -> dict:
    fresh = [c for c in calls if not c.get("cached")]
    return {
        "llm_calls": len(fresh),
        "cached_llm_calls": len(calls) - len(fresh),
        "input_tokens": sum(c.get("input_tokens", 0) for c in fresh),
        "output_tokens": sum(c.get("output_tokens", 0) + c.get("thinking_tokens", 0) for c in fresh),
        "cost_usd": sum(c.get("estimated_cost_usd") or 0.0 for c in fresh),
    }


def run_cases(cases: list[LabelledCase], dataset_dir: Path, options: Options = Options()) -> tuple[list[CaseResult], dict]:
    started = time.perf_counter()
    extra = [c.request.as_row() for c in cases if c.slice == "custom"]
    pipeline = build(dataset_dir, options, extra_requests=extra)
    startup_s = time.perf_counter() - started

    results = []
    for case in cases:
        with capture_usage() as calls:
            t0 = time.perf_counter()
            status, steps, diag = pipeline.assess(case.request.as_row())
            latency_ms = (time.perf_counter() - t0) * 1000
        results.append(CaseResult(
            case_id=case.case_id, slice=case.slice, gold_status=case.expected_status.value, pred_status=status,
            score=score_case(case.gold_row(), status, steps), latency_ms=latency_ms,
            low_confidence=list(diag.get("low_confidence", [])), steps=steps, **_usage(calls)))
    return results, {"startup_s": startup_s, "options": asdict(options)}


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def summarise(results: list[CaseResult]) -> dict:
    """Quality metrics plus cost and latency for one slice."""
    m = aggregate([r.score for r in results])
    n = len(results) or 1
    lat = [r.latency_ms for r in results]
    m.update({
        "llm_calls": sum(r.llm_calls for r in results),
        "cached_llm_calls": sum(r.cached_llm_calls for r in results),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "cost_usd": sum(r.cost_usd for r in results),
        "latency_ms_p50": percentile(lat, 50), "latency_ms_p95": percentile(lat, 95),
        "latency_ms_mean": sum(lat) / n,
    })
    m["tokens_per_case"] = (m["input_tokens"] + m["output_tokens"]) / n
    m["cost_per_case_usd"] = m["cost_usd"] / n
    return m


def summarise_by_slice(results: list[CaseResult]) -> dict[str, dict]:
    out = {}
    for name in ("public_sample", "custom"):
        part = [r for r in results if r.slice == name]
        if part:
            out[name] = summarise(part)
    out["all"] = summarise(results)
    return out
