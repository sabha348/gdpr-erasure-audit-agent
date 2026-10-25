"""Extract every evidence file listed in dataset/evidence_files.csv into code/cache/evidence/.

Cache entries are named `<file_id>__<sha256[:12]>.json`. An entry is reused when the file hash, the schema
version and the parser (or prompt) version all still match. Entries that are waiting for an LLM (needs_llm)
or failed (error) are retried whenever the LLM is allowed. Changing an evidence file therefore re-extracts
only that file, and stale entries for the same file_id are removed.

Usage (from the repository root):
    python code/extract_evidence.py                 # extract everything that is not cached yet
    python code/extract_evidence.py --no-llm        # deterministic parsers only; images become needs_llm
    python code/extract_evidence.py --only evidence_04,evidence_09 --force
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import image_parser, log_parser, pdf_parser, prompts, records_parser, sql_parser
from .common import SCHEMA_VERSION

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DEFAULT_DATASET = REPO_ROOT / "dataset"
CACHE_DIR = CODE_DIR / "cache" / "evidence"
USAGE_LOG = CODE_DIR / "cache" / "llm_calls.jsonl"

FORMATS = {".sql": "sql", ".log": "log", ".json": "json", ".txt": "text", ".pdf": "pdf",
           ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image"}
CURRENT_PARSERS = {
    "sql": {sql_parser.PARSER},
    "log": {log_parser.PARSER},
    "json": {records_parser.JSON_PARSER},
    "text": {records_parser.TEXT_PARSER},
    "pdf": {pdf_parser.PARSER, f"{pdf_parser.PARSER}+llm_pdf@{prompts.PROMPT_VERSION}"},
    "image": {image_parser.PARSER},
}
RETRY_STATUSES = {"needs_llm", "error"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_path(file_id: str, sha: str, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"{file_id}__{sha[:12]}.json"


def resolve_evidence(dataset_dir: Path, rel: str) -> Path:
    path = (dataset_dir / rel).resolve()
    if dataset_dir.resolve() not in path.parents:
        raise ValueError(f"manifest path escapes the dataset directory: {rel}")
    return path


def _cached(path: Path, fmt: str, allow_llm: bool) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if doc.get("schema_version") != SCHEMA_VERSION or doc.get("parser") not in CURRENT_PARSERS[fmt]:
        return None
    if allow_llm and doc.get("status") in RETRY_STATUSES:
        return None
    return doc


def extract_one(row: dict, dataset_dir: Path, *, allow_llm: bool, force: bool,
                cache_dir: Path = CACHE_DIR, usage_log: Path = USAGE_LOG) -> tuple[dict, bool]:
    """Return (document, from_cache)."""
    file_id = row["file_id"]
    path = resolve_evidence(dataset_dir, row["file_path"])
    fmt = FORMATS.get(path.suffix.lower())
    sha = sha256_of(path)
    target = cache_path(file_id, sha, cache_dir)
    if fmt and not force:
        doc = _cached(target, fmt, allow_llm)
        if doc is not None:
            return doc, True

    try:
        if fmt == "sql":
            body = sql_parser.parse_sql(path, file_id)
        elif fmt == "log":
            body = log_parser.parse_log(path, file_id)
        elif fmt == "json":
            body = records_parser.parse_json(path, file_id)
        elif fmt == "text":
            body = records_parser.parse_text(path, file_id)
        elif fmt == "pdf":
            body = pdf_parser.parse_pdf(path, file_id, sha256=sha, allow_llm=allow_llm, usage_log=usage_log)
        elif fmt == "image":
            body = image_parser.parse_image(path, file_id, sha256=sha, allow_llm=allow_llm, usage_log=usage_log)
        else:
            raise ValueError(f"unsupported file type {path.suffix!r}")
    except Exception as e:  # one bad file must not stop the run; it is recorded and retried next time
        body = {"format": fmt or "unknown", "parser": None, "status": "error", "meta": {}, "observations": [],
                "llm_calls": [], "warnings": [f"{type(e).__name__}: {e}"]}

    doc = {
        "schema_version": SCHEMA_VERSION,
        "file_id": file_id,
        "file_path": row["file_path"],
        "origin": row["origin"],
        "captured_at": row["captured_at"],
        "sha256": sha,
        "size_bytes": path.stat().st_size,
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **body,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    for stale in cache_dir.glob(f"{file_id}__*.json"):
        if stale != target:
            stale.unlink()
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc, False


def run(dataset_dir: Path = DEFAULT_DATASET, *, only: set[str] | None = None, allow_llm: bool = True,
        force: bool = False, cache_dir: Path = CACHE_DIR, usage_log: Path = USAGE_LOG, verbose: bool = True) -> dict[str, dict]:
    with open(dataset_dir / "evidence_files.csv", encoding="utf-8", newline="") as f:
        manifest = list(csv.DictReader(f))
    docs, index, new_calls = {}, {}, []
    for row in manifest:
        if only and row["file_id"] not in only:
            continue
        doc, from_cache = extract_one(row, dataset_dir, allow_llm=allow_llm, force=force,
                                      cache_dir=cache_dir, usage_log=usage_log)
        docs[row["file_id"]] = doc
        if not from_cache:
            new_calls += doc["llm_calls"]
        index[row["file_id"]] = {
            "cache_file": cache_path(row["file_id"], doc["sha256"], cache_dir).name,
            "format": doc["format"], "parser": doc["parser"], "status": doc["status"],
            "observations": len(doc["observations"]), "warnings": len(doc["warnings"]),
            "llm_calls": len(doc["llm_calls"]),
        }
        if verbose:
            print(f"{row['file_id']:12} {doc['format']:6} {doc['status']:9} {len(doc['observations']):5} obs "
                  f"{'(cached)' if from_cache else '':8} {row['file_path']}")
    if not only:
        (cache_dir / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    if verbose:
        by_status: dict[str, int] = {}
        for v in index.values():
            by_status[v["status"]] = by_status.get(v["status"], 0) + 1
        tokens_in = sum(c.get("input_tokens", 0) for c in new_calls)
        tokens_out = sum(c.get("output_tokens", 0) for c in new_calls)
        cost = sum(c.get("estimated_cost_usd") or 0 for c in new_calls)
        print(f"\n{len(index)} files: {by_status}")
        print(f"LLM calls this run: {len(new_calls)}  input tokens: {tokens_in}  output tokens: {tokens_out}  "
              f"est. cost: ${cost:.4f}  (log: {usage_log})")
        pending = [k for k, v in index.items() if v["status"] in RETRY_STATUSES]
        if pending:
            print(f"Pending/failed ({len(pending)}): {', '.join(pending)}")
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--only", help="comma-separated file_ids")
    ap.add_argument("--no-llm", action="store_true", help="never call a model; leave such files as needs_llm")
    ap.add_argument("--force", action="store_true", help="ignore the cache and re-extract")
    ap.add_argument("--show", action="store_true", help="print the model transcription of every screenshot extracted")
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    docs = run(Path(args.dataset), only=only, allow_llm=not args.no_llm, force=args.force)
    if args.show:
        for fid, doc in docs.items():
            if doc["format"] != "image":
                continue
            calls = doc["llm_calls"]
            who = f"{calls[-1]['provider']}/{calls[-1]['model']}" if calls else "no model call"
            print(f"\n=== {fid}  {doc['file_path']}  [{doc['status']}; {who}]")
            transcription = doc["meta"].get("transcription")
            print(json.dumps(transcription, indent=2, ensure_ascii=False) if transcription else "  " + "; ".join(doc["warnings"]))
    return 1 if any(d["status"] == "error" for d in docs.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
