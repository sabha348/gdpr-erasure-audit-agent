"""Log files -> observations. Handles every line format found in the dataset:

  TS [LEVEL] [service] [trace] event: k="v" ...            application_trace_*
  TS level=L service=S ... event=E k=v                     service_events_*, resource_lookup_trace_*, support_replica
  TS INFO service=S event=E k="v"                          search_index_diagnostic, notification_delivery
  TS {json}                                                structured_service_events_*
  TS <pri>1 node service trace - - level=L k="v"           syslog_archive_*
  TS | LEVEL | service | trace | event | k="v"             operations_stream_*
  TS requestId=T level=L function=S message="k=\"v\" ..."  cloud_runtime_export_*

Events sharing a trace id are joined: the subject key of a `*_started` line (lookup_key, subject_key, ...)
is attached as `trace_keys` to the other events of that trace. This matters because the `*_completed` line,
which carries the resource_id, usually has no subject identifier of its own.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from .common import ObservationSink, parsed

PARSER = "log_regex@1"

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
BRACKET_RE = re.compile(r"^\[(\w+)\] \[([\w.-]+)\] \[([\w.-]+)\] (\w+):\s*(.*)$")
SYSLOG_RE = re.compile(r"^<\d+>\d+ (\S+) (\S+) (\S+) - - (.*)$")
PIPE_RE = re.compile(r"^\| (\w+) \| (\S+) \| (\S+) \| (\w+) \|\s*(.*)$")
LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "TRACE"}
# Fields that name the subject a lookup/scan was about.
SUBJECT_KEYS = ("lookup_key", "subject_key", "match_key", "owner_key", "scan_key", "subject_ref",
                "correlation_key", "matched_value", "subject_reference", "query")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def parse_kv(text: str) -> dict[str, str]:
    return {k: _unquote(v) for k, v in KV_RE.findall(text)}


def parse_line(line: str) -> dict | None:
    """Return {ts, level, service, trace_id, event, kv} or None if the line is not recognised."""
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    level = service = trace = event = None
    if rest.startswith("{"):
        try:
            kv = {k: v if isinstance(v, str) else json.dumps(v) for k, v in json.loads(rest).items()}
        except json.JSONDecodeError:
            return None
        level = kv.pop("severity", None) or kv.pop("level", None)
        kv.pop("logger", None)
    elif (b := BRACKET_RE.match(rest)):
        level, service, trace, event, tail = b.groups()
        kv = parse_kv(tail)
    elif (s := SYSLOG_RE.match(rest)):
        _node, service, trace, tail = s.groups()
        kv = parse_kv(tail)
    elif (p := PIPE_RE.match(rest)):
        level, service, trace, event, tail = p.groups()
        kv = parse_kv(tail)
    else:
        kv = parse_kv(rest)
        if "message" in kv and "function" in kv:  # cloud runtime export: payload nested in message="..."
            inner = parse_kv(kv.pop("message"))
            service, trace = kv.pop("function"), kv.pop("requestId", None)
            kv = {**kv, **inner}
        first = rest.split(" ", 1)[0]
        if first in LEVELS:
            level = first
        if not kv:
            return None
    level = level or kv.pop("level", None)
    kv.pop("level", None)
    service = service or kv.pop("service", None)
    kv.pop("service", None)
    trace = trace or kv.pop("trace_id", None) or kv.pop("requestId", None)
    event = kv.pop("event", None) or event
    return {"ts": ts, "level": level, "service": service, "trace_id": trace, "event": event, "kv": kv}


def parse_log(path: Path, file_id: str) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    parsed_lines, warnings = [], []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        p = parse_line(line)
        if p is None:
            warnings.append(f"line {n}: unrecognised format: {line[:120]}")
            continue
        p["line_no"] = n
        parsed_lines.append(p)

    trace_keys: dict[str, dict] = collections.defaultdict(dict)
    for p in parsed_lines:
        if p["trace_id"]:
            for k in SUBJECT_KEYS:
                if k in p["kv"] and p["kv"][k] not in ("", "null"):
                    trace_keys[p["trace_id"]].setdefault(k, p["kv"][k])

    meta: dict = {"line_count": len(lines), "parsed_lines": len(parsed_lines), "unparsed_lines": len(warnings),
                  "first_event_at": None, "last_event_at": None, "collector": {}}
    sink = ObservationSink(file_id)
    for p in parsed_lines:
        meta["first_event_at"] = min(filter(None, [meta["first_event_at"], p["ts"]]))
        meta["last_event_at"] = max(filter(None, [meta["last_event_at"], p["ts"]]))
        if p["service"] in ("log_router", "readback_collector") and p["event"] in ("export_started", "export_completed"):
            meta["collector"][p["event"]] = {"at": p["ts"], **p["kv"]}
        tk = trace_keys.get(p["trace_id"], {}) if p["trace_id"] else {}
        fields = dict(p["kv"])
        own = {k for k in SUBJECT_KEYS if k in fields}
        joined = {k: v for k, v in tk.items() if k not in own}
        if joined:
            fields["trace_keys"] = joined
        sink.add("log_event", fields=fields, observed_at=p["ts"], system=p["service"],
                 collection=fields.get("record_type") or fields.get("index"), event=p["event"],
                 resource_id=fields.get("resource_id"),
                 location={"line": p["line_no"], "trace_id": p["trace_id"], "level": p["level"]})
    dropped = meta["collector"].get("export_completed", {})
    meta["dropped_lines_reported"] = int(dropped.get("dropped_auxiliary") or dropped.get("dropped") or 0)
    status = "ok" if not warnings else "partial"
    return parsed("log", PARSER, sink, meta, warnings, status)
