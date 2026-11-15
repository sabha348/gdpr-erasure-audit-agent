# Custom (held-out) evaluation cases

Each `*.json` file here is one labelled case, validated against `eval/schema/case.schema.json`. These cases are
**written by the repository owner only**. The agent's settings were never tuned on them, so they form the
held-out slice in the results table. The 32 official public samples are loaded directly from `dataset/` and do
not need copies here.

## Format

```json
{
  "request": {
    "case_id": "custom_01",
    "email": "<current or historical email of a user in dataset/user_identities.csv / user_audit_log.csv>",
    "requested_at": "2026-08-01T09:00:00Z",
    "reported_status": "COMPLETE | PARTIALLY_COMPLETE | NOT_STARTED",
    "reported_at": "2026-08-02T09:00:00Z",
    "audit_at": "2026-08-05T09:00:00Z",
    "request_text": "<the person's request, any language>"
  },
  "expected_status": "COMPLETE | INCOMPLETE | NOT_APPLICABLE",
  "expected_steps": [
    {"step_no": 1, "phase": "FINDING", "system_id": "<system>", "resource_id": "<resource>",
     "operation": "<operation>", "state": "<state>", "evidence_id": "<file_id / activity_id / audit_id>"}
  ],
  "note": "<what this case tests and how the expected answer was derived>"
}
```

Rules (enforced by the loader):

- `case_id` must be new: it can't reuse an id from `dataset/deletion_requests.csv` or the samples.
- `NOT_APPLICABLE` needs `expected_steps: []`. Every other status needs at least one step.
- `step_no` runs 1..n in order. Each operation fixes its phase and state (see `docs/challenge/problem_statement.md`).
- Remediation steps, and verification steps of an `INCOMPLETE` case, use `"evidence_id": ""`.
- Only cite evidence captured at or before `audit_at`.

Check your files with `erasure-audit eval --set custom --check`.
