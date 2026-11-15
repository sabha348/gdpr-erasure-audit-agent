"""Evidence sources and their collectors: one collector per source, each run as its own Temporal activity.

A collector answers "what does this source hold that is visible at audit_at, and is it intact?" for one request.
It re-reads the source's files from disk and checks each against the hash it was extracted from, so evidence that
changed after start-up is noticed instead of silently assessed from a stale cache. In a real deployment this is
where the network I/O to a database replica, a log store or object storage would happen, which is why timeouts
and retries are attached here.

The assessment then runs on the union of what the collectors returned (EvidenceScope). If a source cannot be
collected, the audit fails closed: a deletion is never reported COMPLETE with a source unread.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..extraction.run import resolve_evidence, sha256_of
from .assess import EvidenceScope, Pipeline

# source name -> evidence directory under dataset/evidence/
FILE_SOURCES = {
    "sql_snapshots": "databases",
    "logs": "logs",
    "records": "records",
    "documents": "documents",
    "screenshots": "images",
}
ACTIVITY_SOURCE = "activity_stream"
SOURCES = (*FILE_SOURCES, ACTIVITY_SOURCE)


class EvidenceUnavailable(Exception):
    """The source is missing or unreadable: transient from the workflow's point of view (retried)."""


class EvidenceCorrupt(Exception):
    """The source is readable but its content is not usable (failed extraction, changed hash): not retried."""


class SourceEvidence(BaseModel):
    source: str
    file_ids: list[str] = Field(default_factory=list, description="files of this source visible at audit_at")
    subject_file_ids: list[str] = Field(default_factory=list, description="visible files naming the subject")
    observations: int = 0
    activity_rows: int = 0
    degraded: list[str] = Field(default_factory=list,
                                description="files read with a fallback (e.g. screenshot without transcription)")


def source_of(file_path: str) -> str | None:
    parts = file_path.replace("\\", "/").split("/")
    folder = parts[1] if len(parts) > 2 and parts[0] == "evidence" else None
    return next((name for name, d in FILE_SOURCES.items() if d == folder), None)


def collect(pipeline: Pipeline, source: str, audit_at: str, tokens: set[str]) -> SourceEvidence:
    ds = pipeline.ds
    if source == ACTIVITY_SOURCE:
        rows = [a for a in ds.activity if a["event_time"] <= audit_at]
        return SourceEvidence(source=source, activity_rows=len(rows))
    if source not in FILE_SOURCES:
        raise ValueError(f"unknown evidence source {source!r}; known: {SOURCES}")

    out = SourceEvidence(source=source)
    for fid, doc in sorted(ds.docs.items()):
        if source_of(doc["file_path"]) != source or doc["captured_at"] > audit_at:
            continue
        try:
            path = resolve_evidence(ds.dir, doc["file_path"])
            current = sha256_of(path)
        except OSError as e:
            raise EvidenceUnavailable(f"{source}: cannot read {doc['file_path']}: {e}") from e
        if current != doc["sha256"]:
            raise EvidenceCorrupt(f"{source}: {fid} changed since it was extracted; restart the worker to re-extract")
        if doc["status"] == "error":
            raise EvidenceCorrupt(f"{source}: {fid} failed extraction: {'; '.join(doc['warnings'])}")
        if doc["status"] in ("needs_llm", "partial"):
            out.degraded.append(fid)
        out.file_ids.append(fid)
        out.observations += len(doc["observations"])
    subject = {o.file_id for t in tokens for o in ds.by_token.get(t, []) if o.file_id in set(out.file_ids)}
    out.subject_file_ids = sorted(subject)
    return out


def scope_from(collected: list[SourceEvidence]) -> EvidenceScope:
    files = frozenset(f for c in collected for f in c.file_ids)
    return EvidenceScope(files=files, activity=any(c.source == ACTIVITY_SOURCE for c in collected))
