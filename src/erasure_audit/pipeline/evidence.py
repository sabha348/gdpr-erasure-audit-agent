"""Observation -> state assertions, and snapshot-based proofs of absence.

An assertion says what one piece of evidence shows about one resource at one moment:
    PRESENT | ABSENT | EXPOSED (present with direct identifiers) | CLEAN (present, identifiers removed)
    ACTIVE | DISABLED (data flows) | RESTORABLE | BLOCKED (backups)
plus a category hint (record_type, attachment vs message, ...) when the evidence carries one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX_SYSTEM = {
    "account": "account_service", "search": "search_service", "analytics": "analytics_platform",
    "support": "support_platform", "export": "export_service", "data_flow": "integration_service",
    "backup": "backup_service", "audit_record": "security_service", "tombstone": "identity_service",
    "legal_hold": "legal_service", "marketing": "marketing_platform", "object": "object_storage",
}
PREFIX_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE", "export": "USER_EXPORT", "data_flow": "DATA_FLOW", "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG", "tombstone": "ACCOUNT_TOMBSTONE", "legal_hold": "LEGAL_HOLD_RECORD",
    "marketing": "MARKETING_PROFILE", "object": "SUPPORT_ATTACHMENT",
}
# DECISIONS D1: cache_* / email_delivery_* never carry evidence; they are not assessed.
IGNORED_PREFIXES = {"cache", "email_delivery"}
DIRECT_ID_FIELDS = ("user_id", "email", "phone", "email_hash", "customer_reference", "name", "full_name",
                    "display_name", "requester_email", "requester_name", "requester_phone", "owner_email")
LEDGER_RESULT = {"FOUND": "PRESENT", "NOT_FOUND": "ABSENT", "IDENTIFIABLE": "EXPOSED", "ANONYMIZED": "CLEAN",
                 "REDACTED": "CLEAN", "DISABLED": "DISABLED", "BLOCKED": "BLOCKED"}


def prefix_of(resource_id: str) -> str:
    return resource_id.rsplit("_", 1)[0]


def system_of(resource_id: str) -> str | None:
    return PREFIX_SYSTEM.get(prefix_of(resource_id))


@dataclass
class Assertion:
    resource_id: str
    kind: str
    at: str                 # when the state was observed
    file_id: str | None     # evidence file, or None for activity-based assertions
    file_at: str | None     # capture time of that file
    activity_id: str | None = None
    hint: str | None = None
    claim: bool = False     # True for workflow results that only claim a state
    confidence: str = "high"
    retention_until: str | None = None


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def _nonnull(v) -> bool:
    return v not in (None, "", "null", "None", "[REDACTED]", "—", "-")


def _content_kind(fields: dict, extra_id_keys=(), stable_ref_allowed: bool = False) -> str:
    """EXPOSED if a direct identifier is still present. For records that are redacted rather than anonymised
    (support messages), the stable internal reference may stay (sample cases 26-28: customer_reference only = REDACTED)."""
    keys = DIRECT_ID_FIELDS + tuple(extra_id_keys)
    if stable_ref_allowed:
        keys = tuple(k for k in keys if k not in ("customer_reference", "user_id"))
    exposed = any(_nonnull(fields.get(k)) for k in keys)
    text = " ".join(str(fields.get(k) or "") for k in ("message_text", "message_excerpt"))
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+|\+\d[\d ()-]{6,}", text):
        exposed = True
    return "EXPOSED" if exposed else "CLEAN"


def _retention(fields: dict, text: str = "") -> str | None:
    for k in ("retention_until", "retained_until"):
        if fields.get(k):
            return fields[k]
    m = re.search(r"retain(?:ed)? until (\d{1,2} \w{3} \d{4})", text, re.I)
    if m:
        from datetime import datetime
        try:
            return datetime.strptime(m.group(1), "%d %b %Y").strftime("%Y-%m-%dT23:59:59Z")
        except ValueError:
            return None
    return None


def interpret(obs) -> list[tuple[str, str, str | None, str | None]]:
    """Return [(resource_id, kind, hint, retention_until)] for one Obs (pipeline.data.Obs)."""
    rid = obs.resource_id
    if not rid or prefix_of(rid) in IGNORED_PREFIXES or prefix_of(rid) not in PREFIX_SYSTEM:
        return []
    raw, f = obs.raw, obs.raw["fields"]
    src, event, pre = raw["source"], raw.get("event") or "", prefix_of(rid)
    ret = _retention(f)

    if src == "image_fallback":
        return [(rid, f["assumed_state"], f["category_hint"], None)] if f.get("assumed_state") else []
    if src == "pdf_document":
        hint = "SUPPORT_ATTACHMENT" if pre == "support" else None
        return [(rid, "PRESENT", hint, _retention(f, f.get("text", "")))]
    if src in ("pdf_line", "pdf_table"):
        return []

    # Ledger rows (JSON/TXT) and diagnostic record_lookup log lines carry an explicit result.
    if "result" in f and "record_type" in f:
        rtype, result = f["record_type"], f["result"]
        if result == "ACTIVE":
            kind = "RESTORABLE" if rtype == "BACKUP_ARCHIVE" else "ACTIVE"
        else:
            kind = LEDGER_RESULT.get(result)
        return [(rid, kind, rtype, ret)] if kind else []

    if src == "log_event":
        if event == "profile_lookup_completed":
            gone = f.get("rows") == "0" or f.get("http_status") == "404"
            return [(rid, "ABSENT" if gone else "PRESENT", None, None)]
        if event == "index_query_completed":
            return [(rid, "PRESENT" if f.get("result") == "hit" else "ABSENT", None, None)]
        if event in ("analytics_profile_read", "analytics_profile_read_completed"):
            exposed = (f.get("direct_identifiers") not in (None, "0")) or _content_kind(f) == "EXPOSED"
            return [(rid, "EXPOSED" if exposed else "CLEAN", None, None)]
        if event == "analytics_identity_scan":
            return [(rid, "EXPOSED", None, None)]
        if event == "contact_lookup_completed":
            return [(rid, "EXPOSED" if f.get("rows") not in ("0", None) else "ABSENT", "CONTACT_RECORD", None)]
        if event == "message_pii_scan":
            return [(rid, "EXPOSED", "SUPPORT_MESSAGE", None)]
        if event == "attachment_pii_scan":
            return [(rid, "PRESENT", "SUPPORT_ATTACHMENT", None)]
        if event in ("message_read", "support_ticket_read_completed"):
            return [(rid, _content_kind(f, stable_ref_allowed=True), "SUPPORT_MESSAGE", None)]
        if event == "contact_query_completed":
            hint = "SECONDARY_IDENTIFIER" if "legacy_contact_index" in str(f.get("trace_keys", "")) or f.get("source") == "legacy_contact_index" else None
            return [(rid, "PRESENT" if f.get("rows") not in ("0", None) else "ABSENT", hint or "SECONDARY_IDENTIFIER", None)]
        if event == "deletion_marker_check":
            return [(rid, "PRESENT" if not _truthy(f.get("marker_found")) else "ABSENT", None, None)]
        if event in ("export_download_check_completed", "export_metadata_read_completed"):
            return [(rid, "PRESENT" if _truthy(f.get("available")) else "ABSENT", None, None)]
        if event in ("connector_heartbeat", "connector_config_loaded", "connector_config_read_completed"):
            return [(rid, "ACTIVE" if _truthy(f.get("enabled")) else "DISABLED", None, None)]
        if event == "restore_candidate_evaluated":
            risky = _truthy(f.get("eligible")) and not _truthy(f.get("deletion_block"))
            return [(rid, "RESTORABLE" if risky else "BLOCKED", None, None)]
        if event == "restore_authorization_completed":
            return [(rid, "BLOCKED" if f.get("decision") == "deny" else "RESTORABLE", None, None)]
        if event == "restore_policy_read_completed":
            if f.get("archive_present") is not None and not _truthy(f.get("archive_present")):
                return [(rid, "ABSENT", None, None)]
            return [(rid, "RESTORABLE" if _truthy(f.get("restore_allowed")) else "BLOCKED", None, None)]
        if event in ("audit_record_read_completed", "retained_order_query_completed", "retained_order_read_completed"):
            return [(rid, "PRESENT" if f.get("rows") != "0" else "ABSENT", None, ret)]
        return []

    # Records from SQL dumps and JSON snapshots / exports.
    if src in ("sql_row", "json_record", "ledger_row"):
        coll = raw.get("collection") or ""
        if pre == "account":
            if "status_code" in f:
                return [(rid, "ABSENT" if str(f["status_code"]) == "404" else "PRESENT", None, None)]
            return [(rid, "PRESENT", None, None)]
        if pre == "search":
            if "hits" in f:
                return [(rid, "PRESENT" if (f["hits"] or {}).get("total") else "ABSENT", None, None)]
            return [(rid, "PRESENT", None, None)]
        if pre == "analytics":
            return [(rid, _content_kind(f), None, None)]
        if pre == "support":
            if "file_name" in f or "uploaded_by" in f or coll == "ticket_attachments":
                return [(rid, "PRESENT", "SUPPORT_ATTACHMENT", None)]
            return [(rid, _content_kind(f, stable_ref_allowed=True), "SUPPORT_MESSAGE", None)]
        if pre == "export":
            if "download_available" in f:
                return [(rid, "PRESENT" if _truthy(f["download_available"]) else "ABSENT", None, None)]
            return [(rid, "PRESENT", None, None)]
        if pre == "data_flow":
            return [(rid, "ACTIVE" if _truthy(f.get("enabled")) else "DISABLED", None, None)]
        if pre == "backup":
            return [(rid, "RESTORABLE" if _truthy(f.get("restore_allowed")) else "BLOCKED", None, None)]
        if pre == "tombstone":  # only the stable subject_reference may remain
            exposed = any(_nonnull(f.get(k)) for k in ("name", "email", "phone", "email_hash"))
            return [(rid, "EXPOSED" if exposed else "CLEAN", None, None)]
        if pre in ("audit_record", "legal_hold"):
            return [(rid, "PRESENT", None, ret)]
        return [(rid, "PRESENT", None, None)]
    return []


# --------------------------------------------------------------------------- snapshot units (absence by omission)

def snapshot_units(doc: dict):
    """Yield (system, captured_at, resource_ids_present, full_coverage) for every part of a file that lists a
    store's contents. full_coverage=True means a missing record is absent from the store (scoped inventory
    or a dedicated table); False means the unit is a partial capture, usable only next to other subject records."""
    fmt, meta, obs = doc["format"], doc["meta"], doc["observations"]
    if not obs:  # DECISIONS #16: an empty capture is not trustworthy proof of absence (e.g. evidence_90)
        return
    if fmt == "sql":
        at = meta.get("captured_at") or meta.get("snapshot_completed_at") or doc["captured_at"]
        if "scope" in meta:  # inventory snapshot with explicit scope
            for s in meta["scope"]:
                present = {o["resource_id"] for o in obs if o["system"] == s["system"]}
                yield s["system"], s["captured_at"], present, True
            return
        generic = any(t.startswith("customer_data_") for t in meta.get("tables", {}))
        systems = {o["system"] for o in obs if o["system"]} | {t["system"] for t in meta.get("tables", {}).values() if t.get("system")}
        if generic:
            systems |= set(PREFIX_SYSTEM.values())
        for system in systems:
            present = {o["resource_id"] for o in obs if o["system"] == system}
            dedicated = not generic and any(t.get("system") == system for t in meta.get("tables", {}).values())
            yield system, at, present, dedicated
    elif fmt == "json" and meta.get("layout") == "snapshot_bundle":
        for snap in meta["snapshots"]:
            if not snap.get("system") or snap.get("collection") == "operational_record_history":
                continue
            present = {o["resource_id"] for o in obs if o["location"] and o["location"].get("snapshot") == snap["index"]}
            yield snap["system"], snap["captured_at"], present, False
