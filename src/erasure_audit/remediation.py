"""What happens after a human approves a remediation plan.

`RemediationExecutor` is the seam between the audit and the systems that hold the data. The shipped
implementation, `DryRunExecutor`, changes nothing: it appends the approved plan to a JSONL outbox (transactional
-outbox style), from which a real integration (ticketing, a deletion worker, a queue) would pick it up. Issuing
is idempotent per audit: issuing the same audit twice returns the first ticket, so a retried call or a replayed
Temporal activity never duplicates work.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import Approval, RemediationTicket, Step


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RemediationExecutor(Protocol):
    name: str

    def issue(self, audit_id: str, steps: list[Step], approval: Approval) -> RemediationTicket: ...


class DryRunExecutor:
    name = "dry_run"

    def __init__(self, outbox: Path):
        self.outbox = Path(outbox)
        self._lock = threading.Lock()

    def _existing(self, audit_id: str) -> RemediationTicket | None:
        if not self.outbox.exists():
            return None
        with open(self.outbox, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry["audit_id"] == audit_id:
                    return RemediationTicket(**entry["ticket"])
        return None

    def issue(self, audit_id: str, steps: list[Step], approval: Approval) -> RemediationTicket:
        with self._lock:
            existing = self._existing(audit_id)
            if existing:
                return existing
            ticket = RemediationTicket(ticket_id=f"rem-{audit_id}", executor=self.name, issued_at=utc_now(),
                                       steps=steps)
            entry = {"audit_id": audit_id, "approval": approval.model_dump(mode="json"),
                     "ticket": ticket.model_dump(mode="json")}
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return ticket
