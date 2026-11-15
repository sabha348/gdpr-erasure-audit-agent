"""Where audit records live in sync mode. In temporal mode the workflow's own state is the record (M3)."""
from __future__ import annotations

import threading
from typing import Protocol

from ..models import AuditRecord, AuditState


class AuditStore(Protocol):
    def get(self, audit_id: str) -> AuditRecord | None: ...
    def put(self, record: AuditRecord) -> None: ...
    def put_if_absent(self, record: AuditRecord) -> tuple[AuditRecord, bool]: ...
    def list(self, state: AuditState | None = None, limit: int = 100) -> list[AuditRecord]: ...


class InMemoryAuditStore:
    """Thread-safe and process-local; fine for a single API replica and for tests."""

    def __init__(self):
        self._records: dict[str, AuditRecord] = {}
        self._lock = threading.Lock()

    def get(self, audit_id: str) -> AuditRecord | None:
        with self._lock:
            return self._records.get(audit_id)

    def put(self, record: AuditRecord) -> None:
        with self._lock:
            self._records[record.audit_id] = record

    def put_if_absent(self, record: AuditRecord) -> tuple[AuditRecord, bool]:
        with self._lock:
            existing = self._records.get(record.audit_id)
            if existing:
                return existing, False
            self._records[record.audit_id] = record
            return record, True

    def list(self, state: AuditState | None = None, limit: int = 100) -> list[AuditRecord]:
        with self._lock:
            records = [r for r in self._records.values() if state is None or r.state == state]
        return sorted(records, key=lambda r: r.created_at, reverse=True)[:limit]
