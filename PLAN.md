# PLAN — from challenge entry to production-grade service

Status: **draft v2, waiting for approval**. No code has been changed yet.

## Decisions taken with the owner (2026-11-15)

| Topic | Decision |
|---|---|
| Hackathon version | Commit `ab47ec2` (2026-10-25) is tagged `hackerrank-orchestrate-submission` once confirmed. The README opens with the ranking line and the tag. All upgrade work happens on `upgrade/eval-temporal`, never on the tag. |
| Branches / PRs | M1 is a PR from `upgrade/eval-temporal` to `main`. Later milestones use stacked branches (`upgrade/m2-api`, `upgrade/m3-temporal`, `upgrade/m4-ship`), each opened as its own PR for the owner to merge. |
| Labelled set | The 32 official public samples (`sample_output.csv`, organiser ground truth published to participants) are the labelled set; nothing is invented. They are reported as **in-sample**, because the settings were tuned on them, next to a **leave-one-out cross-validated** score: each case is scored with settings chosen on the other 31. Label-free invariance tests are added as well. `eval/cases/custom/` stays open for owner-written held-out cases, reported as a separate slice. |
| Per-request model | A Gemini request classifier becomes the setting `classifier=llm`, with the regex rules as the fallback. Cost is shown at paid-tier list price, with a note that the free tier bills $0 and is rate-limited. |
| After approval | (a) The approved plan is written to an outbox and the audit is marked APPROVED, behind a `RemediationExecutor` interface. The default implementation is a dry run. |
| Restructure | `code/` moves to `src/erasure_audit/`, and the challenge files (`problem_statement.md`, `AGENTS.md`, `agent_trace_instructions.md`, `log.txt`) move to `docs/challenge/`. The `log.txt` logging rule ends because the challenge is over. |
| Dataset | `dataset/` stays in the repo: it is fictional, the challenge already published it, and the evaluation and CI need it. The README credits HackerRank as its source. |


## 0. Where the repo is today

| Area | Today |
|---|---|
| Layout | `code/` holds a flat Python tree (`main.py`, `contract.py`, `extraction/`, `pipeline/`, `evaluation/`, `docs/`). Root keeps the HackerRank starter files (`README.md`, `problem_statement.md`, `AGENTS.md`, `log.txt`) and `dataset/` (4.1 MB, 94 evidence files). |
| Extraction | `extraction/` turns every evidence file into a list of *observations*, cached by file hash in `code/cache/`. SQL runs in in-memory sqlite3 (ATTACH denied), 7 log formats go through regexes, JSON/TXT go through record walkers, PDFs through `pdfplumber`. Only the 11 screenshots need a vision model (Gemini or Anthropic, strict JSON schema). |
| Audit | `pipeline/` is **deterministic Python**. It classifies the request with multilingual regexes, resolves the subject from current and historical emails and hashes, and links resources by identifier match, catalog LINK rows and a workflow-row join. It then rebuilds each resource's state at `audit_at` (newest observation wins, absence by omission from full snapshots, workflow results treated as claims), maps the category to `deletion_rules.csv`, and emits ordered, cited steps. Nine judgment calls are named options in `pipeline/config.py`. |
| Run | `python code/main.py` writes `output.csv` (518 rows, about 1 s) and `output.diagnostics.jsonl`. `code/evaluation/main.py` scores against the 32 public samples, `validate_output.py` checks the format and `sweep.py` sweeps the options. |
| Score (re-run here) | 32 samples: status 100%, step P/R/F1 98.3/90.7/94.3%, exact sequence 18/32. The full run reproduces the committed `output.csv` byte-for-byte **without any LLM**. |
| Tests | **None.** There is no pytest, no CI and no lint config. The validator and the scorer are scripts, not tests. |

## 1. Blockers and risks for the target state

1. **It is not an LLM agent at runtime.** A model is only called for 11 screenshots, once, offline, and the output is cached. Request classification is regex-based, and the `classifier` option already reserves a slot for an LLM backend. Per-request tokens and cost are 0 today, and the fresh clone has no cache, so screenshots use the rule fallback. Measuring tokens, cost and latency per request only means something if a model is in the per-request path (see Q2). The resume wording should match whatever we ship.
2. **Ground truth is 32 cases, and the defaults were tuned on them.** `sample_output.csv` is the only label set. `sweep.py` picked the option defaults on those same 32 cases, so the 94% F1 is an in-sample number. The 518 evaluation cases have no labels. A held-out set is needed for an honest result table, and **you write it** (see Q1).
3. **`code/` is not a package, and its name shadows the stdlib `code` module.** Eight files use `sys.path.insert` hacks. There is no `pyproject.toml`, no lock file, and `requirements.txt` lists 4 packages (`anthropic` unpinned). `google-genai` and `anthropic` are not installed in this container.
4. **Config is file-only.** `extraction/llm.py` reads API keys only from `<repo>/.env` and deliberately ignores process environment variables. That cannot work with Docker or CI secrets. Paths derive from `__file__`: no absolute paths are hard-coded, but the cache is written into the source tree (`code/cache/`), which breaks in a read-only container image.
5. **Cases are coupled across requests.** `Pipeline._assign_accounts` links accounts by constraint propagation over *all* requests (the eval and sample CSVs together). So one request's answer depends on the other requests. A per-request API or workflow needs this context precomputed once (at startup or as its own activity), and ad-hoc requests from outside the dataset only get evidence-based linking. This limitation will be documented.
6. **One big in-memory `Dataset`.** All sources are loaded and indexed together in `Dataset.__init__`, and observations are not partitioned by source. Making "each evidence source an activity" needs a refactor into per-source collectors with a serialisable observation type. This is the largest code change in the plan.
7. **The environment can't run Docker.** The Docker CLI is installed but there is no daemon, so I can write and lint `docker-compose.yml` but cannot `docker compose up` here; CI will build the images. Temporal tests use `temporalio.testing.WorkflowEnvironment`, which downloads a test-server binary. I will check whether the network allows that, and if it doesn't I'll stop and tell you.
8. **The challenge leaves loose ends.** `evaluation/evaluation_report.md` and `usage_report.md` are referenced by AGENTS.md but missing. `log.txt` contains a Windows user path. `dataset/` and `problem_statement.md` are HackerRank material in a public repo (see Q5). Every case is flagged `low_confidence` because of the regex classifier, which makes the diagnostics noisy.

## 2. Target architecture

```
           ┌────────────── FastAPI (api) ──────────────┐
client ──► │ POST /v1/audits   GET /v1/audits/{id}     │
           │ POST /v1/audits/{id}/approval             │
           └───────────────┬───────────────────────────┘
                           │ start workflow / signal / query
                           ▼
                 Temporal dev server (compose)
                           │
                           ▼
 worker: ErasureAuditWorkflow(request)
   1 classify_request            (activity; rules or LLM)
   2 resolve_identity            (activity)
   3 collect_* in parallel       (one activity per evidence source:
        sql · logs · records · pdf · screenshots(LLM) · activity_stream
        each with its own start_to_close timeout + RetryPolicy;
        LLMUnavailable / bad input = non-retryable)
   4 assess                      (activity; deterministic policy + plan)
   5 if INCOMPLETE → AWAITING_APPROVAL, wait for signal `approve_remediation`
        (approve | reject, approver, comment; timeout → EXPIRED)
   6 issue_remediation           (activity; see Q3)
   queries: status, result
```

Package layout after M1: `src/erasure_audit/{contract,config,extraction,pipeline,evaluation,api,workflows}`, `tests/`, `eval/`, `docs/`.

## 3. Milestones

Each bullet is one small commit. Every commit keeps `output.csv` byte-identical, and a golden test enforces that from commit M1.3 onward unless a commit says otherwise.

### M1 — Packaging, tests, evaluation harness

1. **Move to an installable package.** `git mv code/* src/erasure_audit/` (history kept), add `pyproject.toml` (deps pinned, `[project.scripts] erasure-audit = ...`, extras `llm`, `api`, `worker`, `dev`), drop every `sys.path.insert`, and keep `code/main.py` as a 5-line shim so the challenge command still works.
   Files: `pyproject.toml`, `src/erasure_audit/**`, `code/main.py`, `.gitignore`.
2. **Env-based settings.** Add `config/settings.py` (pydantic-settings: `ERASURE_DATASET_DIR`, `ERASURE_CACHE_DIR`, `LLM_PROVIDER`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, ...). The settings read process env first and `.env` optionally, and keys are `SecretStr`. The cache moves out of the source tree (default `~/.cache/erasure-audit` or `$ERASURE_CACHE_DIR`).
   Files: `src/erasure_audit/config/settings.py`, `extraction/llm.py`, `extraction/run.py`, `.env.example`.
3. **pytest scaffold and golden tests.** One test checks that the full run equals the committed `output.csv`; a second checks that the 32-sample scores are at or above today's baseline.
   Files: `tests/conftest.py`, `tests/test_golden.py`.
4. **Unit tests.** They cover the contract (op→phase/state, ordering, serialisation), the validator rules, the classifier (en/es/pt/fr/ja/ko, plus negations), identity resolution through email-change chains, and each parser on small fixtures.
   Files: `tests/unit/test_*.py`, `tests/fixtures/`.
5. **Labelled-case format and loader.** `eval/cases/<case_id>.json` holds the request, the expected status, the expected steps and an optional note. A JSON Schema plus a loader rejects malformed labels, and `eval/cases/README.md` explains how to write a case. **The directory stays empty, apart from the public samples if you allow them (Q1), until you add cases.**
   Files: `src/erasure_audit/evaluation/cases.py`, `eval/schema/case.schema.json`, `eval/cases/README.md`.
6. **Scorer refactor.** The scorer becomes a pure function with metrics for verdict accuracy and confusion; step P/R/F1 overall and **per phase** (remediation-step correctness); remediation-order validity (flows and restores before records, every remediation has its verification); field accuracy; and exact sequence accuracy.
   Files: `src/erasure_audit/evaluation/metrics.py`, `tests/unit/test_metrics.py`.
7. **Instrumented runner.** The runner times each case (`perf_counter`), collects the LLM usage records each case produces (tokens in and out, cost), and reports p50/p95 latency.
   Files: `src/erasure_audit/evaluation/runner.py`, `extraction/llm.py` (usage callback instead of a global file).
8. **Results writer and CLI.** `erasure-audit eval --cases eval/cases --out eval/results/<run>` writes `results.md` (table), `results.csv`, `metrics.json` and per-case diffs.
   Files: `src/erasure_audit/evaluation/report.py`, `cli.py`.
9. **(Depends on Q2) LLM request-classifier backend** behind `classifier=llm`. It uses a strict JSON schema, keeps the rules as fallback, and caches by text hash, so per-request tokens and cost are real and comparable in the table.
   Files: `pipeline/classify.py`, `extraction/prompts.py`.
10. **Docs.** `docs/evaluation.md` explains the method and in-sample vs held-out results, replacing the missing `evaluation_report.md`.

**PR #1:** packaging, config, tests and evaluation harness.

### M2 — FastAPI service

1. **Typed domain models (pydantic v2).** These cover `DeletionRequest`, `Step` (with enums for Phase, Operation and State, plus a validator that ties the operation to its phase and state), `AuditResult`, `Diagnostics` and `ApprovalDecision`. The pipeline returns these models, and `contract.py` derives its constants from them.
   Files: `src/erasure_audit/models.py`, `pipeline/__init__.py`, `contract.py`.
2. **`AuditService`.** It loads the dataset, the extraction cache and the cross-request account context once and exposes `audit(request) -> AuditResult`. The process holds it as a singleton, and it is injected through `Depends`.
   Files: `src/erasure_audit/service.py`.
3. **App factory and routes.** The app gets a lifespan hook plus `GET /healthz`, `GET /readyz`, `POST /v1/audits` (synchronous mode), `GET /v1/audits/{id}`, `POST /v1/audits:batch` and `GET /v1/policy/rules`, with a consistent error model (422/404/503).
   Files: `src/erasure_audit/api/{app,routes,deps,errors}.py`.
4. **Result store interface.** It ships an in-memory implementation; the sync mode uses it, and Temporal replaces it in M3.
   Files: `src/erasure_audit/api/store.py`.
5. **Observability basics.** This adds structured JSON logging, a request-id middleware and timing headers.
   Files: `src/erasure_audit/api/middleware.py`, `logging.py`.
6. **API tests.** They use `httpx`/`TestClient` with a stubbed and a real service, and include a contract test that every sample case round-trips through the API with output identical to the CLI.
   Files: `tests/api/test_*.py`.
7. **Docs.** This commit adds the API section and an OpenAPI export (`docs/openapi.json`, generated and checked in CI).

**PR #2:** FastAPI service.

### M3 — Temporal workflows

1. **Split `Dataset` into per-source collectors.** Each collector returns a serialisable `Observation` list filtered to the subject's tokens and `audit_at`. The assessment then runs on the union of those lists, which keeps the logic the same and the golden test green.
   Files: `pipeline/sources/{sql,logs,records,pdf,screenshots,activity}.py`, `pipeline/data.py`, `pipeline/assess.py`.
2. **Activities.** The activities are `classify_request`, `resolve_identity`, `collect_<source>` ×6, `assess` and `issue_remediation`. Payloads are pydantic models (Temporal pydantic data converter) and heavy state stays in the worker process.
   Files: `src/erasure_audit/workflows/activities.py`.
3. **`ErasureAuditWorkflow`.** Collectors run in parallel. Every collector has its own timeout and `RetryPolicy`, and the screenshot collector gets a longer timeout and backoff. The workflow exposes `status` and `result` queries, a signal `approve_remediation`, and `wait_condition` with an approval timeout that leads to EXPIRED. NOT_APPLICABLE and COMPLETE skip approval.
   Files: `src/erasure_audit/workflows/audit_workflow.py`.
4. **Worker entry point.** `erasure-audit worker` starts the worker, with task queue and namespace taken from settings.
   Files: `src/erasure_audit/workflows/worker.py`, `cli.py`.
5. **API on Temporal.** `ERASURE_EXECUTION_MODE=temporal|sync`. `POST /v1/audits` starts the workflow (workflow id `audit-<case_id>`, so duplicates are idempotent), `POST /v1/audits/{id}/approval` sends the signal and `GET` runs the query.
   Files: `api/routes.py`, `api/temporal_client.py`.
6. **Workflow tests** run in the time-skipping environment. They cover the happy path, approval, rejection, approval timeout, retry after a transient failure in a collector and a non-retryable LLM failure that degrades to the fallback, plus a replay test for determinism.
   Files: `tests/workflows/test_*.py`.
7. **Docs.** This commit adds a sequence diagram for the workflow and approval.

**PR #3:** Temporal workflows with human approval.

### M4 — Containers, CI, README

1. **`Dockerfile`.** It is multi-stage, runs as a non-root user, mounts the dataset read-only and puts the cache on a volume. One image serves both the `api` and `worker` commands.
2. **`docker-compose.yml`.** It runs `temporal` (`temporalio/temporal server start-dev`, which includes the UI), `api` and `worker`, with healthchecks and `.env` passthrough. A `Makefile` adds `make up`, `make test` and `make eval`.
3. **`.github/workflows/ci.yml`.** CI runs ruff (lint and format), mypy on new modules, pytest (unit, API, workflows, golden), an eval regression gate on the labelled set (fails below the baseline), a docker build and a compose smoke test (`/healthz` plus one audit end to end).
4. **README rewrite.** The new README covers the problem, a Mermaid architecture diagram, quickstart (local, compose), API examples, the eval results table (generated), design decisions and limitations. The challenge files move to `docs/challenge/`.
5. **Clean-up.** This commit adds a pre-commit config, `CONTRIBUTING`-style dev notes and removes the stale starter README content.

**PR #4:** containers, CI and documentation.

## 4. Working rules

- I stop for: labels or test cases (I never invent them), environment, key or deploy blockers, and the end of each milestone (one PR, with interview-ready design notes).
- The golden `output.csv` test guards every refactor, and any intended change in behaviour gets its own commit and is called out in the PR.
- The `log.txt` entries required by AGENTS.md continue unless you drop that rule (Q4).
