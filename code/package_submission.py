"""Build code.zip (README.md + code/) without generated or secret files. Works on Windows, macOS and Linux.

Usage (from the repository root):  python code/package_submission.py
Excluded: code/cache/ (extraction cache), code/evaluation/results/ (scorer output), *.diagnostics.jsonl,
__pycache__, virtual environments, .env files, and nested archives (which the submission rules reject).
output.csv is a separate deliverable and is not part of code.zip.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_PATHS = {Path("code/cache"), Path("code/evaluation/results")}
EXCLUDED_SUFFIXES = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}


def included(rel: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if any(rel == p or p in rel.parents for p in EXCLUDED_PATHS):
        return False
    name = rel.name
    return not (name == ".env" or name.endswith(".diagnostics.jsonl") or rel.suffix in EXCLUDED_SUFFIXES or name == ".DS_Store")


def main() -> int:
    out = ROOT / "code.zip"
    files = [ROOT / "README.md"] + sorted(p for p in (ROOT / "code").rglob("*") if p.is_file())
    files = [p for p in files if included(p.relative_to(ROOT))]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())
    print(f"Wrote {out} with {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
