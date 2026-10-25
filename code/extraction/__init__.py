"""Evidence extraction layer: every evidence file -> one cached structured JSON document.

    from extraction import load_evidence
    docs = load_evidence()            # {file_id: document}, extracting anything not cached yet

See extraction/common.py for the document and observation format and extraction/run.py for the cache.
"""
from __future__ import annotations

from pathlib import Path

from .run import CACHE_DIR, DEFAULT_DATASET
from .run import run as _run


def load_evidence(dataset_dir: Path = DEFAULT_DATASET, *, allow_llm: bool = True, verbose: bool = False) -> dict[str, dict]:
    return _run(dataset_dir, allow_llm=allow_llm, verbose=verbose)


__all__ = ["load_evidence", "CACHE_DIR"]
