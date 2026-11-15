import json

import pytest

from erasure_audit.contract import read_csv
from erasure_audit.pipeline import read_requests
from paths import DATASET

SAMPLES = read_requests(DATASET / "sample_deletion_requests.csv")
GOLD = {r["case_id"]: r for r in read_csv(DATASET / "sample_output.csv")}


def first(status):
    return next(q for q in SAMPLES if GOLD[q["case_id"]]["deletion_status"] == status)


def test_health_and_readiness(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz").json()
    assert ready["status"] == "ready" and ready["backend"] == "sync"


def test_every_sample_round_trips_like_the_cli(client, pipeline):
    for q in SAMPLES:
        r = client.post("/v1/audits", json=q)
        assert r.status_code == 201, r.text
        body = r.json()
        status, steps, _ = pipeline.assess(q)
        assert body["result"]["deletion_status"] == status
        assert body["result"]["steps"] == steps
        assert r.headers["Location"] == f"/v1/audits/audit-{q['case_id']}"


def test_resubmission_is_idempotent_and_conflicts_are_rejected(client):
    q = first("COMPLETE")
    assert client.post("/v1/audits", json=q).status_code == 201
    again = client.post("/v1/audits", json=q)
    assert again.status_code == 200
    changed = client.post("/v1/audits", json={**q, "request_text": q["request_text"] + " (edited)"})
    assert changed.status_code == 409
    assert changed.headers["content-type"].startswith("application/problem+json")


def test_complete_audit_needs_no_approval(client):
    body = client.post("/v1/audits", json=first("COMPLETE")).json()
    assert body["state"] == "COMPLETED"
    r = client.post(f"/v1/audits/{body['audit_id']}/approval", json={"decision": "approve", "approver": "dpo"})
    assert r.status_code == 409


def test_approval_issues_remediation_to_the_outbox(client):
    body = client.post("/v1/audits", json=first("INCOMPLETE")).json()
    assert body["state"] == "AWAITING_APPROVAL"
    r = client.post(f"/v1/audits/{body['audit_id']}/approval",
                    json={"decision": "approve", "approver": "dpo@example.test", "comment": "ok"})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["state"] == "REMEDIATION_ISSUED"
    assert rec["remediation"]["executor"] == "dry_run"
    assert {s["phase"] for s in rec["remediation"]["steps"]} == {"REMEDIATION"}
    outbox = client.settings.remediation_outbox.read_text(encoding="utf-8").splitlines()
    assert json.loads(outbox[0])["audit_id"] == body["audit_id"]
    assert client.post(f"/v1/audits/{body['audit_id']}/approval",
                       json={"decision": "approve", "approver": "x"}).status_code == 409  # already decided


def test_rejection(client):
    body = client.post("/v1/audits", json=first("INCOMPLETE")).json()
    rec = client.post(f"/v1/audits/{body['audit_id']}/approval", json={"decision": "reject", "approver": "dpo"}).json()
    assert rec["state"] == "REJECTED" and rec["remediation"] is None


def test_get_list_and_not_found(client):
    q = first("COMPLETE")
    client.post("/v1/audits", json=q)
    assert client.get(f"/v1/audits/audit-{q['case_id']}").json()["request"] == q
    assert [a["audit_id"] for a in client.get("/v1/audits", params={"state": "COMPLETED"}).json()] == [
        f"audit-{q['case_id']}"]
    missing = client.get("/v1/audits/audit-nope")
    assert missing.status_code == 404 and missing.json()["status"] == 404


@pytest.mark.parametrize("patch", [{"audit_at": "yesterday"}, {"reported_status": "DONE"}, {"email": ""},
                                   {"unexpected": 1}])
def test_validation_errors_are_problem_json(client, patch):
    r = client.post("/v1/audits", json={**first("COMPLETE"), **patch})
    assert r.status_code == 422
    assert r.json()["title"] == "Request validation failed"


def test_batch_limit(client):
    ok = client.post("/v1/audits/batch", json={"requests": SAMPLES[:3]})
    assert ok.status_code == 200 and len(ok.json()["audits"]) == 3
    assert client.post("/v1/audits/batch", json={"requests": SAMPLES[:6]}).status_code == 413


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    generated = client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"]
    assert generated != "bad id with spaces" and len(generated) == 32
    assert "app;dur=" in client.get("/healthz").headers["Server-Timing"]


def test_policy_rules(client):
    rules = client.get("/v1/policy/rules").json()
    assert {r["data_category"] for r in rules} >= {"USER_PROFILE", "ACCOUNT_TOMBSTONE", "BACKUP_ARCHIVE"}


def test_failed_audit_is_recorded_not_500(client, monkeypatch):
    def boom(_request):
        raise RuntimeError("evidence store offline")

    monkeypatch.setattr(client.app.state.service, "audit", boom)
    body = client.post("/v1/audits", json=first("COMPLETE")).json()
    assert body["state"] == "FAILED" and "evidence store offline" in body["error"]
