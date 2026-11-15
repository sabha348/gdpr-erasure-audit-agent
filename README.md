# GDPR Erasure Audit Agent

## Hackathon origin

Ranked 1,073 of 4,094 in HackerRank Orchestrate (Oct 2026). Submission preserved at tag `hackerrank-orchestrate-submission`. Everything after that tag is post-hackathon work.

## What it does

A deletion workflow can report *Complete* while personal data is still in replicas, logs, exports, support
attachments, search indexes or backups, and sync or restore jobs can bring deleted data back. For each erasure
request this agent decides whether deletion was actually requested, reconstructs the person's current and
historical identifiers, rebuilds every linked record's state at the audit time from SQL snapshots, logs, PDFs,
JSON and screenshots, applies the deletion policy, and returns an evidence-cited verdict with an ordered
remediation and verification plan as structured JSON.

## Quickstart

```bash
pip install -e '.[dev]'            # add ,llm for model-backed extraction/classification
erasure-audit run                  # dataset/deletion_requests.csv -> output.csv
erasure-audit eval --cv            # score on labelled cases (+ leave-one-out CV) -> eval/results/
pytest
```

The production upgrade (evaluation harness, FastAPI service, Temporal workflows, Docker Compose, CI) is tracked
in [PLAN.md](PLAN.md). Approach and design notes: [docs/approach.md](docs/approach.md),
[docs/DECISIONS.md](docs/DECISIONS.md). The dataset in `dataset/` is the fictional challenge dataset published by
HackerRank; the original challenge files are in [docs/challenge/](docs/challenge/).
