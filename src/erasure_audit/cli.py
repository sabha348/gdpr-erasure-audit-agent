"""Command line for the deletion-audit agent.

    erasure-audit run [--requests CSV] [--output CSV] [--option NAME=VALUE ...]
    erasure-audit extract [--no-llm] [--only ids] [--force] [--show]
    erasure-audit validate [PREDICTIONS] [--requests CSV]
    erasure-audit eval [--set all|public|custom] [--cv] [--out DIR] [--option NAME=VALUE ...] [--check]
    erasure-audit score [--predictions CSV]          # legacy report: 32 public samples vs sample_output.csv
    erasure-audit serve [--host 0.0.0.0] [--port 8000]   # HTTP API (needs the api extra)
    erasure-audit sweep                              # one-factor-at-a-time option sweep on the samples

`python -m erasure_audit ...` is equivalent. Evidence is read through the extraction cache; anything not cached
is extracted on first use. `run` writes rows in input order and per-case diagnostics (every low-confidence
decision) to <output>.diagnostics.jsonl.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import CHOICES, Options, build, read_requests, run_cases, write_diagnostics, write_output
from .settings import get_settings


def cmd_run(argv) -> int:
    ap = argparse.ArgumentParser(prog="erasure-audit run")
    ap.add_argument("--dataset", default=None, help="default: $ERASURE_DATASET_DIR or ./dataset")
    ap.add_argument("--requests", default=None, help="default: <dataset>/deletion_requests.csv")
    ap.add_argument("--output", default="output.csv")
    ap.add_argument("--option", action="append", default=[], metavar="NAME=VALUE",
                    help="; ".join(f"{k}: {'|'.join(v)}" for k, v in CHOICES.items()))
    args = ap.parse_args(argv)

    dataset = Path(args.dataset or get_settings().dataset_dir)
    requests = read_requests(Path(args.requests) if args.requests else dataset / "deletion_requests.csv")
    options = Options.parse(args.option)
    results = run_cases(build(dataset, options), requests)
    out = Path(args.output)
    write_output(results, out)
    write_diagnostics(results, out.with_suffix(".diagnostics.jsonl"))

    statuses: dict[str, int] = {}
    for _cid, status, _steps, _diag in results:
        statuses[status] = statuses.get(status, 0) + 1
    low = sum(1 for r in results if r[3].get("low_confidence"))
    print(f"Wrote {len(results)} rows to {out} {statuses}; options: {options}")
    print(f"{low} cases carry low-confidence decisions (see {out.with_suffix('.diagnostics.jsonl').name})")
    return 0


def cmd_eval(argv) -> int:
    from .evaluation import report
    from .evaluation.cases import CUSTOM_DIR, load_cases
    from .evaluation.cv import leave_one_out
    from .evaluation.runner import run_cases as run_labelled
    from .evaluation.runner import summarise_by_slice

    ap = argparse.ArgumentParser(prog="erasure-audit eval", description="Score the agent on labelled cases.")
    ap.add_argument("--set", default="all", choices=("all", "public", "custom"))
    ap.add_argument("--custom-dir", default=str(CUSTOM_DIR))
    ap.add_argument("--out", default=None, help="default: eval/results/<UTC timestamp>")
    ap.add_argument("--option", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--cv", action="store_true", help="add leave-one-out cross-validation of the option selection")
    ap.add_argument("--check", action="store_true", help="only validate the labelled cases, then exit")
    ap.add_argument("--min-verdict-accuracy", type=float, default=None, help="exit 1 if verdict accuracy is lower")
    ap.add_argument("--min-step-f1", type=float, default=None, help="exit 1 if overall step F1 is lower")
    args = ap.parse_args(argv)

    dataset = Path(get_settings().dataset_dir)
    cases = load_cases(dataset, args.set, Path(args.custom_dir))
    if args.check:
        print(f"{len(cases)} labelled cases valid ({', '.join(sorted({c.slice for c in cases})) or 'none'})")
        return 0
    if not cases:
        print("no labelled cases found", file=sys.stderr)
        return 2
    results, info = run_labelled(cases, dataset, Options.parse(args.option))
    by_slice = summarise_by_slice(results)
    cv = leave_one_out(cases, dataset) if args.cv and any(c.slice == "public_sample" for c in cases) else None
    from datetime import UTC, datetime
    out = Path(args.out or Path("eval") / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    path = report.write(out, results, by_slice, info, cv)
    print("\n".join(report.summary_table(by_slice)))
    if cv:
        print()
        print("\n".join(report.cv_table(cv)))
    print(f"\nWrote {path}, results.csv and metrics.json")
    overall = by_slice["all"]
    failed = []
    if args.min_verdict_accuracy is not None and overall["status_accuracy"] < args.min_verdict_accuracy:
        failed.append(f"verdict accuracy {overall['status_accuracy']:.4f} < {args.min_verdict_accuracy}")
    if args.min_step_f1 is not None and overall["step_f1"] < args.min_step_f1:
        failed.append(f"step F1 {overall['step_f1']:.4f} < {args.min_step_f1}")
    for f in failed:
        print(f"REGRESSION: {f}", file=sys.stderr)
    return 1 if failed else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "run":
        return cmd_run(rest)
    if command == "eval":
        return cmd_eval(rest)
    if command == "serve":
        ap = argparse.ArgumentParser(prog="erasure-audit serve")
        ap.add_argument("--host", default="127.0.0.1")
        ap.add_argument("--port", type=int, default=8000)
        args = ap.parse_args(rest)
        import uvicorn
        uvicorn.run("erasure_audit.api.app:create_app", factory=True, host=args.host, port=args.port,
                    log_config=None, access_log=False)  # RequestContextMiddleware logs requests
        return 0
    if command == "extract":
        from .extraction.run import main as extract_main
        return extract_main(rest)
    if command == "validate":
        from .evaluation.validate import main as validate_main
        return validate_main(rest)
    if command == "score":
        from .evaluation.score import main as score_main
        return score_main(rest)
    if command == "sweep":
        from .evaluation.sweep import main as sweep_main
        return sweep_main()
    print(f"unknown command {command!r}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
