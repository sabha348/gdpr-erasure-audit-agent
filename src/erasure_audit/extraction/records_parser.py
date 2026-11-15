"""JSON and plain-text record files -> observations.

JSON layouts (docs/DATA_NOTES.md section 4.3):
  * snapshot bundle:  {exported_at, snapshots: [{captured_at, data_store, collection, records: [...]}]}
  * service export:   {exported_at, service_exports: {system: {list_name: [{observed_at, ...}]}}}
  * record ledger:    {exported_at, records: [{recorded_at, system, resource_id, record_type, result, subject_reference}]}
  * single capture:   {captured_at, collection, records: [...]}
Unknown layouts fall back to a walk that emits every dict carrying a resource_id.
Every snapshot / list, including empty ones, is recorded in meta, because an empty snapshot is evidence of absence.

Text: the shift-handover format `- TS | system | resource_id | RECORD_TYPE | RESULT | subject`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import ObservationSink, parsed

JSON_PARSER = "records_json@1"
TEXT_PARSER = "records_text@1"
LEDGER_LINE_RE = re.compile(
    r"^-\s*(\d{4}-\d{2}-\d{2}T[\d:]+Z)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.+?)\s*$"
)


def _is_ledger_row(rec: dict) -> bool:
    return "record_type" in rec and "result" in rec and "system" in rec


def _add_record(sink, rec, *, observed_at, system, collection, location):
    if _is_ledger_row(rec):  # ledger rows name their own system and time
        sink.add("ledger_row", fields=rec, observed_at=rec.get("recorded_at") or observed_at,
                 system=rec["system"], collection=rec["record_type"], location=location)
    else:
        sink.add("json_record", fields=rec, observed_at=rec.get("observed_at") or observed_at,
                 system=system, collection=collection, location=location)


def _walk(value, path, sink, observed_at):
    if isinstance(value, dict):
        if "resource_id" in value:
            _add_record(sink, value, observed_at=observed_at, system=None, collection=None, location={"path": path})
            return
        for k, v in value.items():
            _walk(v, f"{path}.{k}", sink, observed_at)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", sink, observed_at)


def parse_json(path: Path, file_id: str) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    sink = ObservationSink(file_id)
    meta: dict = {k: v for k, v in doc.items() if isinstance(v, (str, int, float, bool)) or v is None}
    warnings: list[str] = []
    file_time = doc.get("exported_at") or doc.get("captured_at")

    if isinstance(doc.get("snapshots"), list):
        meta["layout"] = "snapshot_bundle"
        meta["snapshots"] = []
        for i, snap in enumerate(doc["snapshots"]):
            records = snap.get("records") or []
            meta["snapshots"].append({"index": i, "captured_at": snap.get("captured_at"), "system": snap.get("data_store"),
                                      "collection": snap.get("collection"), "record_count": len(records)})
            for j, rec in enumerate(records):
                _add_record(sink, rec, observed_at=snap.get("captured_at"), system=snap.get("data_store"),
                            collection=snap.get("collection"), location={"snapshot": i, "record": j})
    elif isinstance(doc.get("service_exports"), dict):
        meta["layout"] = "service_export"
        meta["lists"] = []
        for system, lists in doc["service_exports"].items():
            for list_name, items in (lists or {}).items():
                items = items or []
                meta["lists"].append({"system": system, "list": list_name, "record_count": len(items)})
                for j, rec in enumerate(items):
                    _add_record(sink, rec, observed_at=file_time, system=system, collection=list_name,
                                location={"system": system, "list": list_name, "record": j})
    elif isinstance(doc.get("records"), list):
        records = doc["records"]
        meta["layout"] = "ledger" if records and all(_is_ledger_row(r) for r in records) else "single_collection"
        meta["record_count"] = len(records)
        for j, rec in enumerate(records):
            _add_record(sink, rec, observed_at=file_time, system=doc.get("source"),
                        collection=doc.get("collection"), location={"record": j})
    else:
        meta["layout"] = "unknown"
        warnings.append("unrecognised JSON layout; used generic resource_id walk")
        _walk(doc, "$", sink, file_time)
    return parsed("json", JSON_PARSER, sink, meta, warnings, "ok" if not warnings else "partial")


def parse_text(path: Path, file_id: str) -> dict:
    sink = ObservationSink(file_id)
    meta: dict = {"other_lines": []}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = LEDGER_LINE_RE.match(line.strip())
        if m:
            ts, system, rid, rtype, result, subject = m.groups()
            fields = {"recorded_at": ts, "system": system, "resource_id": rid, "record_type": rtype,
                      "result": result, "subject_reference": subject}
            sink.add("ledger_row", fields=fields, observed_at=ts, system=system, collection=rtype,
                     location={"line": n})
            continue
        snap = re.match(r"^Snapshot:\s*(\S+)", line)
        if snap:
            meta["snapshot_at"] = snap.group(1)
        meta["other_lines"].append(line.strip())
    status = "ok" if sink.items else "partial"
    warnings = [] if sink.items else ["no ledger lines recognised"]
    return parsed("text", TEXT_PARSER, sink, meta, warnings, status)
