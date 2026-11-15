# HTTP API

```bash
pip install -e '.[api]'
erasure-audit serve --host 0.0.0.0 --port 8000      # interactive docs at /docs, schema at /openapi.json
```

The checked-in schema is [openapi.json](openapi.json), and a test fails if it drifts from the code.

## Lifecycle

```
POST /v1/audits ──► RUNNING ──► COMPLETED                 (COMPLETE or NOT_APPLICABLE: nothing to remediate)
                       │
                       ├──► AWAITING_APPROVAL ──approve──► REMEDIATION_ISSUED   (plan handed to the executor)
                       │                     ──reject───► REJECTED
                       │                     ──timeout──► EXPIRED              (temporal mode, M3)
                       └──► FAILED                        (the error is recorded on the audit)
```

| method | path | purpose |
|---|---|---|
| POST | `/v1/audits` | audit one request. Returns 201 for a new audit, 200 if the identical request was already submitted, 409 if the same `case_id` arrives with different fields |
| POST | `/v1/audits/batch` | up to `ERASURE_BATCH_LIMIT` requests (413 above it) |
| GET | `/v1/audits?state=&limit=` | list audits, newest first |
| GET | `/v1/audits/{audit_id}` | one audit: request, result (status, steps, diagnostics, usage), approval, remediation ticket |
| POST | `/v1/audits/{audit_id}/approval` | `{"decision": "approve" \| "reject", "approver": "...", "comment": "..."}`. Returns 409 unless the audit is AWAITING_APPROVAL |
| GET | `/v1/policy/rules` | the deletion policy in force |
| GET | `/healthz`, `/readyz` | liveness, and readiness (dataset loaded, backend reachable) |

Errors are RFC 9457 `application/problem+json` and carry the `request_id`. Every response echoes or generates
`X-Request-ID` and includes `Server-Timing`.

## Example

```bash
curl -s localhost:8000/v1/audits -H 'content-type: application/json' -d '{
  "case_id": "case_05", "email": "...", "requested_at": "2026-08-20T09:00:00Z",
  "reported_status": "COMPLETE", "reported_at": "2026-08-22T09:00:00Z", "audit_at": "2026-08-25T09:00:00Z",
  "request_text": "Please delete my account."}'
# -> 201 {"audit_id": "audit-case_05", "state": "AWAITING_APPROVAL", "result": {"deletion_status": "INCOMPLETE",
#         "steps": [...], "diagnostics": {...}, "usage": {...}, "duration_ms": 0.4}, ...}

curl -s localhost:8000/v1/audits/audit-case_05/approval -H 'content-type: application/json' \
  -d '{"decision": "approve", "approver": "dpo@example.com"}'
# -> 200 {"state": "REMEDIATION_ISSUED", "remediation": {"ticket_id": "rem-audit-case_05", "executor": "dry_run", ...}}
```

## Design notes

- **Backend seam.** Routes depend only on `AuditBackend`. `ERASURE_EXECUTION_MODE=sync` runs audits in-process,
  in a worker thread so a slow model call never blocks the event loop, and keeps records in memory, which suits
  one replica, tests and demos. `temporal` (M3) runs each audit as a durable workflow and keeps the same API.
- **Idempotency.** `audit_id` is derived from `case_id`, so client retries are safe. Temporal's workflow-id
  uniqueness gives the same guarantee in M3.
- **No remediation without a human.** Remediation is only issued after an explicit approval, through a
  `RemediationExecutor`. The shipped executor is a dry run: it writes to a JSONL outbox and changes nothing.
  It is idempotent per audit.
- **Account-join context.** Requests whose `case_id` is in the dataset get the full cross-request account join.
  Ad-hoc requests are linked from evidence only, because the join needs the whole batch of requests up front.
