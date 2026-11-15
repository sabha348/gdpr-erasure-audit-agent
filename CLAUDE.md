# Notes for coding agents

- Package: `src/erasure_audit/` (install with `pip install -e '.[dev]'`). CLI: `erasure-audit`.
- Run `ruff check src tests` and `pytest` before committing.
- `tests/golden/output.csv` pins the full 518-case output; a behaviour change must update it in its own commit
  and say why.
- Never invent evaluation labels. Labelled cases come only from `dataset/sample_output.csv` or the repo owner.
- Secrets come from environment variables (or an uncommitted `.env`); never commit or log them.
- The HackerRank challenge rules (AGENTS.md, log.txt logging) ended with the challenge; they are archived in
  `docs/challenge/` for reference only.
