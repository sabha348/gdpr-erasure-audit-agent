"""Ghost Data Still Exists: deletion-audit pipeline.

Usage (from the repository root):
    python code/main.py                                   # dataset/deletion_requests.csv -> output.csv
    python code/main.py --requests dataset/sample_deletion_requests.csv --output sample_predictions.csv
    python code/main.py --option citation=case_dominant --option latest_state=latest_file

Evidence is read through the extraction cache (code/extract_evidence.py runs automatically for anything not
cached). Rows are written in the input order (numeric case order). Per-case diagnostics, including every
low-confidence decision, go to <output>.diagnostics.jsonl.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import CHOICES, Options, build, read_requests, run_cases, write_diagnostics, write_output  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(REPO_ROOT / "dataset"))
    ap.add_argument("--requests", default=None, help="default: <dataset>/deletion_requests.csv")
    ap.add_argument("--output", default=str(REPO_ROOT / "output.csv"))
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

    statuses = {}
    for _cid, status, _steps, _diag in results:
        statuses[status] = statuses.get(status, 0) + 1
    low = sum(1 for r in results if r[3].get("low_confidence"))
    print(f"Wrote {len(results)} rows to {out} {statuses}; options: {options}")
    print(f"{low} cases carry low-confidence decisions (see {out.with_suffix('.diagnostics.jsonl').name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
