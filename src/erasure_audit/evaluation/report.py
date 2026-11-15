"""Writes an evaluation run to disk: results.md (tables), results.csv (one row per case) and metrics.json."""
from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..extraction.llm import settings as llm_settings
from .runner import CaseResult

SLICE_TITLES = {"public_sample": "public samples (in-sample)", "custom": "custom (held-out)", "all": "all"}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def metadata(run_info: dict) -> dict:
    llm = llm_settings()
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(),
        "options": run_info["options"],
        "llm_provider": llm["provider"],
        "llm_model": llm["model"] if llm["provider"] != "none" else None,
        "startup_s": round(run_info["startup_s"], 3),
    }


SUMMARY_ROWS = [
    ("cases", lambda m: str(m["cases"])),
    ("verdict accuracy", lambda m: _pct(m["status_accuracy"])),
    ("step precision / recall / F1", lambda m: f"{_pct(m['step_precision'])} / {_pct(m['step_recall'])} / {_pct(m['step_f1'])}"),
    ("finding-step F1", lambda m: _pct(m["per_phase"]["FINDING"]["f1"])),
    ("remediation-step F1", lambda m: _pct(m["per_phase"]["REMEDIATION"]["f1"])),
    ("verification-step F1", lambda m: _pct(m["per_phase"]["VERIFICATION"]["f1"])),
    ("field accuracy (mean of 6)", lambda m: _pct(sum(m["field_accuracy"].values()) / len(m["field_accuracy"]))),
    ("exact sequence", lambda m: f"{_pct(m['sequence_accuracy'])} ({m['exact_cases']}/{m['cases']})"),
    ("plan validity (label-free)", lambda m: _pct(m["plan_validity"])),
    ("LLM calls (fresh / cached)", lambda m: f"{m['llm_calls']} / {m['cached_llm_calls']}"),
    ("tokens in / out", lambda m: f"{m['input_tokens']:,} / {m['output_tokens']:,}"),
    ("tokens per case", lambda m: f"{m['tokens_per_case']:,.0f}"),
    ("cost total / per case (USD, list price)", lambda m: f"${m['cost_usd']:.4f} / ${m['cost_per_case_usd']:.5f}"),
    ("latency p50 / p95 (ms)", lambda m: f"{m['latency_ms_p50']:.1f} / {m['latency_ms_p95']:.1f}"),
    ("API retries (429 / 5xx), cases affected", lambda m: f"{m['api_retries']}, {m['cases_with_retries']}"),
    ("classifier fell back to rules (cases)", lambda m: str(m["classifier_fallbacks"])),
]


def summary_table(by_slice: dict[str, dict]) -> list[str]:
    names = list(by_slice)
    if len(names) == 2 and "all" in names:  # one slice only: "all" would repeat it
        names.remove("all")
    lines = ["| metric | " + " | ".join(SLICE_TITLES.get(n, n) for n in names) + " |",
             "|---|" + "---|" * len(names)]
    for label, fn in SUMMARY_ROWS:
        lines.append(f"| {label} | " + " | ".join(fn(by_slice[n]) for n in names) + " |")
    return lines


def cv_table(cv: dict) -> list[str]:
    rows = [("verdict accuracy", "status_accuracy"), ("step F1", "step_f1"), ("remediation-step F1", "remediation_f1"),
            ("exact sequence", "sequence_accuracy")]
    lines = ["| metric | in-sample (shipped defaults) | leave-one-out held-out |", "|---|---|---|"]
    for label, key in rows:
        lines.append(f"| {label} | {_pct(cv['in_sample'][key])} | {_pct(cv['held_out'][key])} |")
    lines += ["", f"{cv['folds']} folds; each re-ran the option sweep on the other {cv['folds'] - 1} cases starting from "
              f"untuned options; {cv['folds_matching_shipped_defaults']} folds picked exactly the shipped defaults; "
              f"{cv['option_sets_evaluated']} option sets evaluated."]
    if cv["fold_deviations"]:
        common: dict[str, int] = {}
        for d in cv["fold_deviations"].values():
            key = ", ".join(f"{k}={v}" for k, v in sorted(d.items()))
            common[key] = common.get(key, 0) + 1
        lines.append("Fold picks that differ from the shipped defaults: "
                     + "; ".join(f"{k} ({n} folds)" for k, n in sorted(common.items(), key=lambda kv: -kv[1])) + ".")
    return lines


def case_rows(results: list[CaseResult]) -> list[dict]:
    return [{
        "case_id": r.case_id, "slice": r.slice, "expected": r.gold_status, "predicted": r.pred_status,
        "verdict_ok": r.score["status_ok"], "step_f1": round(r.score["step_f1"], 4),
        "remediation_f1": round(_phase_f1(r.score, "REMEDIATION"), 4), "exact": r.score["exact"],
        "plan_valid": not r.score["plan_violations"], "llm_calls": r.llm_calls,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": round(r.cost_usd, 6),
        "latency_ms": round(r.latency_ms, 2), "low_confidence": ";".join(r.low_confidence),
    } for r in results]


def _phase_f1(score: dict, phase: str) -> float:
    p = score["per_phase"][phase]
    if not p["gold"] and not p["pred"]:
        return 1.0
    prec = p["tp"] / p["pred"] if p["pred"] else 0.0
    rec = p["tp"] / p["gold"] if p["gold"] else 0.0
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def render(results: list[CaseResult], by_slice: dict, meta: dict, cv: dict | None = None) -> str:
    out = ["# Evaluation results", "",
           f"Generated {meta['generated_at']} at commit `{meta['git_commit']}`. Classifier: "
           f"`{meta['options']['classifier']}`"
           + (f" (`{meta['llm_provider']}` / `{meta['llm_model']}`)" if meta["options"]["classifier"] == "llm" else
              " (no model calls in the per-request path)")
           + f". Pipeline start-up: {meta['startup_s']} s.",
           "", "## Summary", "", *summary_table(by_slice), "",
           "Cost is at paid-tier list price (`extraction/llm.py` PRICING); the Gemini free tier bills $0. "
           "Latency is per case inside a warm process (dataset and evidence loaded once at start-up) and includes "
           "the client's backoff on rate-limited (429) or failed (5xx) calls; see the retries row.", ""]
    if cv:
        out += ["## Leave-one-out cross-validation (public samples)", "", *cv_table(cv), ""]
    out += ["## Per case", "", "| case | slice | expected | predicted | verdict | step F1 | remediation F1 | exact | "
            "tokens | cost (USD) | latency (ms) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in case_rows(results):
        out.append(f"| {r['case_id']} | {r['slice']} | {r['expected']} | {r['predicted']} | "
                   f"{'✓' if r['verdict_ok'] else '✗'} | {r['step_f1']:.3f} | {r['remediation_f1']:.3f} | "
                   f"{'✓' if r['exact'] else '✗'} | {r['input_tokens'] + r['output_tokens']} | {r['cost_usd']:.5f} | "
                   f"{r['latency_ms']:.1f} |")
    diffs = [d for res in results if (d := "\n".join(res.score["diff"]))]
    if diffs:
        out += ["", f"## Differences from the labels ({len(diffs)} cases)", "", "```", *diffs, "```"]
    return "\n".join(out) + "\n"


def write(out_dir: Path, results: list[CaseResult], by_slice: dict, run_info: dict, cv: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = metadata(run_info)
    (out_dir / "results.md").write_text(render(results, by_slice, meta, cv), encoding="utf-8")
    rows = case_rows(results)
    with open(out_dir / "results.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["case_id"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    payload = {"meta": meta, "summary": by_slice, "cv": cv,
               "cases": [{**asdict(r), "score": {k: v for k, v in r.score.items() if k != "diff"}} for r in results]}
    (out_dir / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_dir / "results.md"
