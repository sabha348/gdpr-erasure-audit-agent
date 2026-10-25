"""SQL snapshot dumps -> observations, by executing them in an in-memory sqlite3 database.

Three schema families exist (see docs/DATA_NOTES.md section 4.1):
  * generic JSON-document tables `customer_data_<copy>(resource_id, origin_system, collection_name,
    record_json, updated_at)`;
  * typed inventory tables (`customer_profiles`, ..., plus `snapshot_scope`);
  * small bespoke tables (`customer_profiles_primary`, `search_documents_primary`, ...).
Every table, including empty ones, is listed in meta, because an empty table is evidence of absence.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .common import ObservationSink, parsed

PARSER = "sql_sqlite3@1"

# Typed table (or table-name prefix) -> source system.
TABLE_SYSTEMS = [
    ("customer_profiles", "account_service"),
    ("customer_search_documents", "search_service"),
    ("search_documents", "search_service"),
    ("customer_analytics_profiles", "analytics_platform"),
    ("support_records", "support_platform"),
    ("customer_exports", "export_service"),
    ("connector_subscriptions", "integration_service"),
    ("backup_restore_entries", "backup_service"),
    ("security_audit_records", "security_service"),
]
COPY_SUFFIX_RE = re.compile(r"_(primary|read_replica|reporting_replica|failover_replica|replica_[a-z_]+)$")


def _deny_attach(action, *_args):
    # A dump must not be able to reach other files on disk.
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _table_copy(table: str) -> str | None:
    m = COPY_SUFFIX_RE.search(table)
    return m.group(1) if m else None


def _table_system(table: str) -> str | None:
    for prefix, system in TABLE_SYSTEMS:
        if table.startswith(prefix):
            return system
    return None


def parse_sql(path: Path, file_id: str) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict = {"tables": {}}
    for key, pattern in (("captured_at", r"^-- Captured at:\s*(\S+)"), ("topology", r"^-- Database topology:\s*(\S+)"),
                         ("snapshot_completed_at", r"^-- snapshot_completed_at=(\S+)")):
        m = re.search(pattern, text, re.M)
        if m:
            meta[key] = m.group(1)

    conn = sqlite3.connect(":memory:")
    conn.set_authorizer(_deny_attach)
    conn.executescript(text)
    sink = ObservationSink(file_id)
    warnings: list[str] = []
    snapshot_time = meta.get("captured_at") or meta.get("snapshot_completed_at")

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for table in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        if table == "snapshot_scope":
            meta["scope"] = [{"system": r[0], "captured_at": r[1]} for r in rows]
            continue
        copy = _table_copy(table)
        meta["tables"][table] = {"columns": cols, "row_count": len(rows), "copy": copy, "system": _table_system(table)}
        for n, row in enumerate(rows, 1):
            rec = dict(zip(cols, row))
            location = {"table": table, "row": n}
            if "record_json" in rec:
                try:
                    doc = json.loads(rec["record_json"])
                except json.JSONDecodeError as e:
                    warnings.append(f"{table} row {n}: invalid record_json ({e})")
                    doc = {"_raw": rec["record_json"]}
                fields = {**doc, "_updated_at": rec.get("updated_at")}
                sink.add("sql_row", fields=fields, observed_at=snapshot_time, system=rec.get("origin_system"),
                         collection=rec.get("collection_name"), copy=copy,
                         resource_id=rec.get("resource_id"), location=location)
            else:
                sink.add("sql_row", fields=rec, observed_at=snapshot_time, system=_table_system(table),
                         collection=COPY_SUFFIX_RE.sub("", table), copy=copy,
                         resource_id=rec.get("resource_id"), location=location)
    conn.close()
    return parsed("sql", PARSER, sink, meta, warnings)
