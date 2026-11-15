"""Identity resolution, checked against the dataset's own audit log (no invented people)."""
from conftest import DATASET
from erasure_audit.contract import read_csv

AUDIT = read_csv(DATASET / "user_audit_log.csv")
IDENTITIES = {r["user_id"]: r for r in read_csv(DATASET / "user_identities.csv")}


def test_current_email_resolves(pipeline):
    uid, user = next(iter(IDENTITIES.items()))
    resolved, tokens = pipeline.subject(user["email"].upper())
    assert resolved == uid
    assert {uid, user["email"].lower(), user["email_hash"]} <= tokens


def test_every_historical_email_resolves_to_its_owner(pipeline):
    changes = [a for a in AUDIT if a["field_name"] == "email"]
    assert changes
    for a in changes:
        resolved, tokens = pipeline.subject(a["old_value"])
        assert resolved == a["user_id"], a["audit_id"]
        assert IDENTITIES[a["user_id"]]["email"].lower() in tokens


def test_old_hashes_are_identifiers(pipeline):
    for a in (a for a in AUDIT if a["field_name"] == "email_hash"):
        _, tokens = pipeline.subject(IDENTITIES[a["user_id"]]["email"])
        assert a["old_value"].lower() in tokens


def test_unknown_email_has_no_subject(pipeline):
    assert pipeline.subject("nobody@invalid.example") == (None, set())
