"""End-to-end smoke test against a running stack (compose or local). Standard library only.

    python scripts/smoke.py [--base-url http://localhost:8000] [--timeout 120]

Waits for /readyz, audits one COMPLETE and one INCOMPLETE public sample, approves the INCOMPLETE one and checks
that a remediation ticket was issued. Uses fresh case_ids so it can run repeatedly against the same stack.
Exit code 0 on success.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"content-type": "application/json", "x-request-id": f"smoke-{uuid.uuid4().hex[:8]}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    deadline = time.monotonic() + args.timeout
    while True:
        try:
            status, body = call(base, "GET", "/readyz")
            if status == 200:
                print("ready:", body)
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        if time.monotonic() > deadline:
            print("API never became ready", file=sys.stderr)
            return 1
        time.sleep(2)

    with open(DATASET / "sample_deletion_requests.csv", encoding="utf-8", newline="") as f:
        requests = {r["case_id"]: r for r in csv.DictReader(f)}
    with open(DATASET / "sample_output.csv", encoding="utf-8", newline="") as f:
        gold = {r["case_id"]: r["deletion_status"] for r in csv.DictReader(f)}
    tag = uuid.uuid4().hex[:6]
    picks = {s: next(dict(q, case_id=f"smoke-{tag}-{s.lower()}") for c, q in requests.items() if gold[c] == s)
             for s in ("COMPLETE", "INCOMPLETE")}

    failures = []

    def check(cond: bool, what: str):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            failures.append(what)

    status, rec = call(base, "POST", "/v1/audits", picks["COMPLETE"])
    check(status == 201 and rec.get("state") == "COMPLETED", f"COMPLETE sample -> 201 COMPLETED ({status} {rec.get('state')})")
    status, again = call(base, "POST", "/v1/audits", picks["COMPLETE"])
    check(status == 200 and again.get("audit_id") == rec.get("audit_id"), f"resubmit is idempotent ({status})")

    status, rec = call(base, "POST", "/v1/audits", picks["INCOMPLETE"])
    check(status == 201 and rec.get("state") == "AWAITING_APPROVAL",
          f"INCOMPLETE sample -> 201 AWAITING_APPROVAL ({status} {rec.get('state')})")
    if rec.get("audit_id"):
        status, done = call(base, "POST", f"/v1/audits/{rec['audit_id']}/approval",
                            {"decision": "approve", "approver": "smoke-test"})
        check(status == 200 and done.get("state") == "REMEDIATION_ISSUED",
              f"approval -> REMEDIATION_ISSUED ({status} {done.get('state')})")
        check(bool((done.get("remediation") or {}).get("ticket_id")), "remediation ticket issued")
    status, _ = call(base, "GET", "/v1/audits/audit-does-not-exist")
    check(status == 404, f"unknown audit -> 404 ({status})")

    print("SMOKE " + ("PASSED" if not failures else f"FAILED ({len(failures)})"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
