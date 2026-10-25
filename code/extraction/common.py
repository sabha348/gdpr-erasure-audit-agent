"""Shared pieces of the extraction layer: the cached-document envelope and identifier scanning.

Every evidence file becomes one JSON document:

    {
      "schema_version", "file_id", "file_path", "origin", "captured_at", "sha256", "size_bytes",
      "format":   sql | log | json | text | pdf | image,
      "parser":   name@version of the deterministic parser (or the LLM prompt) that produced it,
      "status":   ok | partial | needs_llm | error,
      "meta":     format-specific header facts (snapshot scope, topology, export counters, ...),
      "observations": [ Observation, ... ],
      "llm_calls": [ token-usage record per model call ],
      "warnings": [ str, ... ]
    }

An Observation is one record / log event / ledger line / document row, kept faithful to the source:

    {
      "obs_id": "<file_id>#<n>", "source": sql_row | log_event | ...,
      "observed_at": timestamp of the observation (row/line/snapshot time) or null,
      "system": source system id as written (account_service, support_replica, ...) or null,
      "collection": table / collection / record_type, "copy": primary | read_replica | ... | null,
      "resource_id": str | null, "event": log event name | null,
      "fields": {raw attributes}, "identifiers": {emails, user_ids, hashes, resource_ids},
      "location": where in the file it came from
    }

The layer does not decide whether a record is present, absent, active or redacted, or who it belongs to.
That interpretation belongs to the pipeline.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

SCHEMA_VERSION = 1

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
USER_ID_RE = re.compile(r"\buser_\d+\b")
# 64-hex email hashes; integrity seals ("sha256:000...") are excluded by the lookbehind.
HASH_RE = re.compile(r"(?<![0-9a-fA-F:])[0-9a-f]{64}(?![0-9a-fA-F])")
RESOURCE_RE = re.compile(
    r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|"
    r"legal_hold|marketing|object|cache|email_delivery)_\d+\b"
)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def scan_identifiers(*values: Any) -> dict[str, list[str]]:
    """Collect identifier-shaped strings from any nested values. Emails are lower-cased."""
    emails, users, hashes, resources = set(), set(), set(), set()
    for s in (s for v in values for s in _strings(v)):
        emails.update(e.lower().rstrip(".") for e in EMAIL_RE.findall(s))
        users.update(USER_ID_RE.findall(s))
        hashes.update(HASH_RE.findall(s))
        resources.update(RESOURCE_RE.findall(s))
    return {
        "emails": sorted(emails),
        "user_ids": sorted(users),
        "hashes": sorted(hashes),
        "resource_ids": sorted(resources),
    }


class ObservationSink:
    """Collects observations for one file and numbers them deterministically."""

    def __init__(self, file_id: str):
        self.file_id = file_id
        self.items: list[dict] = []

    def add(self, source: str, *, fields: dict, observed_at=None, system=None, collection=None, copy=None,
            resource_id=None, event=None, location=None, extra_identifier_values: Any = None) -> dict:
        if resource_id is None:
            rid = fields.get("resource_id")
            resource_id = rid if isinstance(rid, str) else None
        obs = {
            "obs_id": f"{self.file_id}#{len(self.items) + 1}",
            "source": source,
            "observed_at": observed_at,
            "system": system,
            "collection": collection,
            "copy": copy,
            "resource_id": resource_id,
            "event": event,
            "fields": fields,
            "identifiers": scan_identifiers(fields, extra_identifier_values),
            "location": location,
        }
        self.items.append(obs)
        return obs


def parsed(fmt: str, parser: str, sink: ObservationSink, meta: dict | None = None, warnings: list | None = None,
           status: str = "ok", llm_calls: list | None = None) -> dict:
    """Body of an extraction document; the file-level envelope fields are added by the runner."""
    return {
        "format": fmt,
        "parser": parser,
        "status": status,
        "meta": meta or {},
        "observations": sink.items,
        "llm_calls": llm_calls or [],
        "warnings": warnings or [],
    }
