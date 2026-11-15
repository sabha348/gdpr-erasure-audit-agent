# Evaluation

`erasure-audit eval` scores the agent on labelled cases and writes `eval/results/<UTC timestamp>/`:

| file | contents |
|---|---|
| `results.md` | per-slice summary, the leave-one-out table (with `--cv`), a per-case table and diffs against the labels |
| `results.csv` | one row per case: verdict, step F1, remediation F1, exact match, plan validity, tokens, cost, latency |
| `metrics.json` | everything above plus run metadata (commit, options, provider and model, start-up time) |

```bash
erasure-audit eval --cv                                   # public samples + any custom cases, with LOO CV
erasure-audit eval --option classifier=llm                # Gemini request classifier (needs GEMINI_API_KEY)
erasure-audit eval --set custom --check                   # validate owner-written cases only
erasure-audit eval --min-verdict-accuracy 1.0 --min-step-f1 0.94   # CI regression gate
```

## Labelled cases

There are two slices, and they are reported separately.

| slice | source | how to read its score |
|---|---|---|
| `public_sample` | the 32 official answers published with the challenge (`dataset/sample_output.csv`), loaded directly | **in-sample**: the option defaults were picked on these 32 cases, and some rules were written after reading them |
| `custom` | `eval/cases/custom/*.json`, written by the repository owner only ([format](../eval/cases/custom/README.md)) | **held-out**: nothing was tuned on them |

No labels are generated. Building the labelled set out of model-written or self-written answers would grade the
agent against its own beliefs, so the only labels are the organisers' answers and the owner's own cases.

### Why leave-one-out, and what it shows

With only 32 labels, a separate test split would be too small to mean anything. Instead, `--cv` checks the one
step that was fitted on the labels: choosing the named options (`pipeline/config.py`). For each of the 32 cases
it re-runs the one-factor-at-a-time sweep from **untuned** options on the other 31 cases, predicts the held-out
case with whatever that sweep picked, and then scores the 32 held-out predictions together.

Current result: every fold picks exactly the shipped defaults, and the held-out scores equal the in-sample scores
(verdict 100%, step F1 94.3%, 18/32 exact). The option tuning is therefore not overfitted to individual cases.
What LOO cannot measure is the bias from rules written by reading the samples, such as the account-join rule
(DECISIONS D3); only the `custom` slice can.

## Metrics

| metric | definition |
|---|---|
| verdict accuracy | predicted `deletion_status` equals the label |
| step precision / recall / F1 | multiset match on (phase, system_id, resource_id, operation, state, evidence_id), micro-averaged over steps |
| finding / remediation / verification F1 | the same, restricted to one phase. **Remediation-step F1** is the remediation-correctness score |
| field accuracy | each gold step is aligned with the best predicted step for the same system_id and resource_id, and each field is compared; unaligned gold steps count as wrong |
| exact sequence | status and the whole ordered step list are identical, with step_no running 1..n |
| plan validity | label-free check: every flow is disabled and every restore blocked before any record is changed, and every remediated resource has its matching verification |
| tokens, cost | the case's model calls, captured with `capture_usage()`, at paid-tier list price (`extraction/llm.py` PRICING); cache hits are counted separately and cost $0 |
| latency p50 / p95 | wall time of `Pipeline.assess` per case in a warm process; dataset loading is reported once as start-up time |

## Where the model is used

| step | backend | per request? |
|---|---|---|
| request classification | `classifier=rules` (default) or `classifier=llm` (Gemini, zero-shot, schema-constrained, rules as fallback) | yes, with `classifier=llm` |
| screenshot transcription | vision model, run once per file and cached by file hash | no (offline extraction) |

With the default `rules` backend, the token and cost columns are zero; that is accurate, not missing data.

## Measured runs (32 public samples)

| run | verdict | step F1 | remediation F1 | exact | model calls | tokens in / out | cost (list price) | latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| `classifier=rules` | 100% | 94.3% | 100% | 18/32 | 0 | 0 / 0 | $0 | 0.3 ms / 0.5 ms |
| `classifier=llm`, gemini-3.6-flash, prompt v1 (2026-11-15) | 84.4% | 89.2% | 98.8% | 17/32 | 32 | 8,329 / 1,801 | $0.013 ($0.0004/case) | 6.1 s / 202 s |
| `classifier=llm`, prompt v2 | not run yet: the free tier's 20-requests/day quota ran out | | | | | | | |

**What prompt v1 got wrong.** All 5 verdict errors were follow-ups such as "Can you verify that my account was
deleted?", which v1 classified as non-erasure (`account_help` / `other`). The problem statement settles this:
`NOT_APPLICABLE` is for requests where "no deletion **or deletion verification** is authorized". Prompt v2 adds
that rule. Because the gap was found by reading sample failures, a v2 score on the samples is partly
in-sample; the rule itself comes from the spec, not from any sample text.

**Latency.** The p95 is free-tier rate limiting, not model time: one call spent 202 s in 429 backoff. The report
now shows API retries and rule fallbacks, and the client gives up at once on a per-day quota 429 instead of
retrying.

**Takeaway so far.** At this point the regex rules beat the zero-shot model on this dataset. The model's value is
as a second opinion: when it disagrees with the rules, the case is flagged for human review.
