"""Command line for the deletion-audit agent.

    erasure-audit run [--requests CSV] [--output CSV] [--option NAME=VALUE ...]
    erasure-audit extract [--no-llm] [--only ids] [--force] [--show]
    erasure-audit validate [PREDICTIONS] [--requests CSV]
    erasure-audit score [--predictions CSV]          # 32 public samples vs sample_output.csv
    erasure-audit sweep                              # one-factor-at-a-time option sweep on the samples

`python -m erasure_audit ...` is equivalent. Evidence is read through the extraction cache; anything not cached
is extracted on first use. `run` writes rows in input order and per-case diagnostics (every low-confidence
decision) to <output>.diagnostics.jsonl.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contract import REPO_ROOT
from .pipeline import CHOICES, Options, build, read_requests, run_cases, write_diagnostics, write_output


def cmd_run(argv) -> int:
    ap = argparse.ArgumentParser(prog="erasure-audit run")
    ap.add_argument("--dataset", default=str(REPO_ROOT / "dataset"))
    ap.add_argument("--requests", default=None, help="default: <dataset>/deletion_requests.csv")
    ap.add_argument("--output", default="output.csv")
    ap.add_argument("--option", action="append", default=[], metavar="NAME=VALUE",
                    help="; ".join(f"{k}: {'|'.join(v)}" for k, v in CHOICES.items()))
    args = ap.parse_args(argv)

    dataset = Path(args.dataset)
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


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "run":
        return cmd_run(rest)
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
