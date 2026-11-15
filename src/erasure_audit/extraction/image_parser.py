"""Screenshots -> observations via a vision model (no deterministic parser can read them).

The model only transcribes (prompts.SCREENSHOT_*); this module turns the transcription into observations:
  image_header    application, breadcrumb, title, subtitle, badges, search box
  image_card      one summary card (label/value)
  image_row       one table row as {column: value}
  image_panel     one detail panel as {label: value}
  image_activity  one timeline/activity entry
  image_text      other visible text
Identifiers are then scanned deterministically from the transcribed strings.
"""
from __future__ import annotations

from pathlib import Path

from . import llm, prompts
from .common import RESOURCE_RE, ObservationSink, parsed

PARSER = f"vision@{prompts.PROMPT_VERSION}"
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _first_resource(*values) -> str | None:
    for v in values:
        m = RESOURCE_RE.search(v or "")
        if m:
            return m.group(0)
    return None


def to_observations(sink: ObservationSink, t: dict) -> None:
    header = {k: t[k] for k in ("application", "breadcrumb", "page_title", "subtitle", "badges", "search_query")}
    sink.add("image_header", fields=header, resource_id=_first_resource(t["page_title"], t["subtitle"]),
             location={"part": "header"})
    for i, card in enumerate(t["summary_cards"]):
        sink.add("image_card", fields=dict(card), location={"part": "summary_cards", "index": i})
    for ti, table in enumerate(t["tables"]):
        cols = table["columns"]
        for ri, row in enumerate(table["rows"]):
            values = {(cols[c] if c < len(cols) and cols[c] else f"col_{c}"): v for c, v in enumerate(row)}
            sink.add("image_row", fields={"table": table["title"], "values": values},
                     resource_id=_first_resource(*row), location={"part": "tables", "table": ti, "row": ri})
    for pi, panel in enumerate(t["detail_panels"]):
        values = {f["label"]: f["value"] for f in panel["fields"]}
        sink.add("image_panel", fields={"title": panel["title"], "values": values},
                 resource_id=_first_resource(panel["title"], *values.values()), location={"part": "detail_panels", "index": pi})
    for ai, item in enumerate(t["activity"]):
        sink.add("image_activity", fields=dict(item), resource_id=_first_resource(item["text"]),
                 location={"part": "activity", "index": ai})
    if t["other_text"]:
        sink.add("image_text", fields={"lines": t["other_text"]}, location={"part": "other_text"})


def parse_image(path: Path, file_id: str, *, sha256: str, allow_llm: bool, usage_log: Path) -> dict:
    sink = ObservationSink(file_id)
    meta = {"media_type": MEDIA_TYPES.get(path.suffix.lower(), "image/png")}
    if not allow_llm:
        return parsed("image", PARSER, sink, meta, ["vision extraction skipped (--no-llm)"], status="needs_llm")
    try:
        transcription, call = llm.call_structured(
            purpose="screenshot_transcription", file_id=file_id, sha256=sha256,
            prompt_id=f"screenshot@{prompts.PROMPT_VERSION}", system=prompts.SCREENSHOT_SYSTEM,
            user_text=prompts.SCREENSHOT_USER, attachments=[(path.read_bytes(), meta["media_type"])],
            schema=prompts.SCREENSHOT_SCHEMA, usage_log=usage_log)
    except (llm.LLMUnavailable, llm.LLMError) as e:
        status = "needs_llm" if isinstance(e, llm.LLMUnavailable) else "error"
        calls = [e.record] if getattr(e, "record", None) else []
        return parsed("image", PARSER, sink, meta, [f"vision extraction failed: {e}"], status=status, llm_calls=calls)
    meta["transcription"] = transcription
    to_observations(sink, transcription)
    return parsed("image", PARSER, sink, meta, llm_calls=[call])
