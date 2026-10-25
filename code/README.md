# Ghost Data Still Exists: deletion-audit agent

For every request in `dataset/deletion_requests.csv`, this system decides whether the person asked for deletion.
If they did, it finds every record that still belongs to them and works out each record's state at `audit_at`.
It applies `deletion_rules.csv` and writes `output.csv`. That file holds the status (`COMPLETE`, `INCOMPLETE` or
`NOT_APPLICABLE`) and the ordered, evidence-cited findings, remediation and verification steps.

The pipeline itself is deterministic Python. A language model is used for one thing only: transcribing the 11
screenshots. Even that is optional, because a rule-based fallback takes over without it.

---

## Setup

Requirements: Python 3.10+ (developed on 3.12), run from the repository root.

```bash
pip install -r code/requirements.txt
```

Optional, for screenshot transcription: create `.env` at the repository root (template: `.env.example`).

```text
LLM_PROVIDER=gemini          # gemini (default) | anthropic | none
GEMINI_API_KEY=...           # keys are read only from .env, never from the shell environment
```

Without a key, or with `LLM_PROVIDER=none`, the system still runs end to end. Screenshots then use the rule-based
fallback and are flagged low-confidence.

## Run

```bash
python code/extract_evidence.py          # 1. extract every evidence file once (cached; safe to re-run)
python code/main.py                      # 2. write output.csv (+ output.diagnostics.jsonl)
python code/evaluation/validate_output.py   # 3. check output.csv against every format rule
```

`main.py` runs extraction automatically for anything not cached, so step 1 is only needed to transcribe screenshots up
front. A clean run over all 518 cases takes a few seconds once evidence is cached.

| Command | Purpose |
|---|---|
| `python code/main.py --requests dataset/sample_deletion_requests.csv --output sample.csv` | run on the 32 public samples |
| `python code/evaluation/main.py` | run the pipeline on the samples and score it against `sample_output.csv` (case, step, field and sequence accuracy, per-case diffs) |
| `python code/evaluation/sweep.py` | compare every alternative of every named decision on the samples |
| `python code/main.py --option name=value` | switch a decision, e.g. `--option citation=case_dominant` (all options: `--help`) |
| `python code/extract_evidence.py --only evidence_04 --show` | print a screenshot's transcription |
| `python code/package_submission.py` | build `code.zip` without caches, results, diagnostics or `.env` |

---

## Approach

```
dataset/ ──► extraction/  (one structured JSON per evidence file, cached by file hash)
                 │
                 ▼
          pipeline/  per case:  request ► subject ► linked records ► state at audit_at ► policy ► steps
                 │
                 ▼
          output.csv  +  output.diagnostics.jsonl (why each decision was made, low-confidence flags)
```

### 1. Evidence extraction (`extraction/`)
Every file in `evidence_files.csv` is converted once into the same shape, a list of *observations*. An observation
holds the system, collection, replica copy, resource id, timestamp, raw fields, and the emails, user ids and hashes
found in it. Each format is read deterministically:

| Format | How it is read |
|---|---|
| SQL snapshots | executed in an in-memory `sqlite3` database (`ATTACH` blocked); primary and replica tables kept apart so replication drift is visible |
| Logs (7 line formats) | regex parsers; events of one trace are joined, so the email on a lookup line travels with the resource id on its result line |
| JSON / TXT records | snapshot bundles, service exports, ledgers, handover text |
| PDFs | `pdfplumber` text and tables (a model only if a page has no text layer) |
| Screenshots | vision model with a strict JSON schema, transcription only (no interpretation) |

The cache key is the file hash plus the parser and prompt version. Every model call is logged with its tokens and cost
in `code/cache/llm_calls.jsonl`. All prompts live in `extraction/prompts.py`.

### 2. The audit for one case (`pipeline/`)
1. **Is it a deletion request?** Multilingual rules (en, es, pt, fr, ja, ko). Scoped requests such as "marketing
   lists only", access, export or correction requests, and explicit "do not delete" wording are `NOT_APPLICABLE`.
   The 10 such eval requests agree with the independent CLASSIFY_REQUEST activity rows.
2. **Who is the person?** The request email is matched case-insensitively against current and historical emails (the
   audit log, following chains). Their identifiers are the user id, every email and every email hash, current and old.
3. **Which records are theirs?** These come from exact identifier matches in evidence visible at `audit_at`, plus
   data-catalog link rows. When no evidence names their account, it is joined through the deletion workflow's own
   account row: that row falls just before `reported_at`, its result equals the reported status, and each row
   belongs to one request (resolved by constraint propagation). Decoys such as `.jr` emails and look-alike names never
   match.
4. **What is each record's state at `audit_at`?** Only files captured at or before `audit_at` and activity at or
   before it are used. The newest observation wins. A copy in any replica counts as present. A snapshot that should
   list a record and doesn't is proof it is absent, unless the snapshot is empty or doesn't cover that system.
   Workflow results ("SUCCESS") are claims; checks ("connector remains enabled") and evidence files are observations.
5. **What does policy require?** The category comes from the resource and its evidence, and the action from
   `deletion_rules.csv`. Examples: profiles, search entries and exports are deleted; support messages and tombstones
   are redacted; analytics are anonymized; security and legal records are kept. Data flows are disabled and backups
   are blocked from restoring, never deleted.
6. **Steps.** Findings come first (citing the latest visible file that shows the state), then the remediation plan
   (flows and restores first), then the checks that must pass. The account is always verified absent, and retained
   records are verified present. Steps are sorted by phase, operation order, `system_id` and `resource_id`. A case is
   `INCOMPLETE` if any finding exists.

Every judgment call is a **named option** (`pipeline/config.py`), and the defaults were chosen by measurement on
the samples.

---

## Results on the 32 public samples

| Metric | Value |
|---|---|
| Case-status accuracy | **100%** |
| Step precision / recall / F1 | 98.3% / 90.7% / **94.3%** |
| Exact sequence accuracy | **18 / 32** |

The remaining gaps are steps for records that visible evidence never links to the person. Examples: an account that
appears in no input row, and audit records that exist only in files captured after `audit_at`. On the records it does
find, the system has not produced a wrong operation, state or citation. The derivations are in
`docs/SAMPLE_ANALYSIS.md`, and the measured decisions in `docs/DECISIONS.md`.

**Full run:** 518 rows, validator PASS: 357 INCOMPLETE, 151 COMPLETE and 10 NOT_APPLICABLE. Every uncertain decision
is flagged per case in `output.diagnostics.jsonl`: rule-based classification, tie-break account matches, and
screenshot-based states.

**Model usage:** screenshots only, with `gemini-3.8-flash` at temperature 0. That is about 1,300 input and 550 output
tokens per screenshot, roughly $0.003 each and about $0.03 for all 11. Every call, including failed or retried ones,
is recorded in `code/cache/llm_calls.jsonl`.

---

## Layout

```
code/
  main.py                  pipeline entry point (writes output.csv)
  extract_evidence.py      extraction entry point
  contract.py              output contract: operations, states, ordering, serialization
  extraction/              per-format parsers, LLM client (llm.py), prompts (prompts.py), cache (run.py)
  pipeline/                config (named options), data loading, classifier, evidence interpretation, assessment
  evaluation/              scorer (main.py), validator, option sweep, reports
  docs/                    DATA_NOTES.md, SAMPLE_ANALYSIS.md, DECISIONS.md
  package_submission.py    builds code.zip
  requirements.txt
```

Generated and excluded from git and from `code.zip`: `code/cache/`, `code/evaluation/results/`,
`output.diagnostics.jsonl` and `.env`.
