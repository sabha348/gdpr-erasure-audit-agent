"""PDF documents -> observations via pdfplumber; an LLM transcription is used only when a page has no text layer.

Observations:
  pdf_line   one text line (with `cells` when the line is pipe-separated, e.g. register rows);
  pdf_table  one table as detected by pdfplumber, which groups a "Record" line with its "Detail" lines,
             so related facts (event reference, subject, retain-until) stay together.
"""
from __future__ import annotations

from pathlib import Path

from . import llm, prompts
from .common import ObservationSink, parsed

PARSER = "pdf_pdfplumber@1"
MIN_PAGE_CHARS = 20  # below this a page is treated as having no usable text layer


def _add_lines(sink: ObservationSink, page_no: int, lines: list[str], source_parser: str) -> None:
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        fields = {"text": line, "page": page_no, "parser": source_parser}
        if "|" in line:
            fields["cells"] = [c.strip() for c in line.split("|")]
        sink.add("pdf_line", fields=fields, location={"page": page_no, "line": n})


def parse_pdf(path: Path, file_id: str, *, sha256: str, allow_llm: bool, usage_log: Path) -> dict:
    import pdfplumber

    sink = ObservationSink(file_id)
    meta: dict = {"pages": []}
    warnings: list[str] = []
    blank_pages = []
    with pdfplumber.open(path) as pdf:
        meta["page_count"] = len(pdf.pages)
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            meta["pages"].append({"page": page_no, "chars": len(text), "tables": len(tables), "images": len(page.images)})
            if len(text.strip()) < MIN_PAGE_CHARS:
                blank_pages.append(page_no)
                continue
            _add_lines(sink, page_no, text.splitlines(), PARSER)
            for t, table in enumerate(tables, 1):
                rows = [[(c or "").strip() for c in row] for row in table]
                sink.add("pdf_table", fields={"page": page_no, "table": t, "rows": rows},
                         location={"page": page_no, "table": t})

    if not blank_pages:
        return parsed("pdf", PARSER, sink, meta, warnings)

    warnings.append(f"pages without text layer: {blank_pages}")
    if not allow_llm:
        return parsed("pdf", PARSER, sink, meta, warnings, status="needs_llm")
    try:
        result, call = llm.call_structured(purpose="pdf_transcription", file_id=file_id, sha256=sha256,
                                           prompt_id=f"pdf@{prompts.PROMPT_VERSION}", system=prompts.PDF_SYSTEM,
                                           user_text=prompts.PDF_USER, attachments=[(path.read_bytes(), "application/pdf")],
                                           schema=prompts.PDF_SCHEMA, usage_log=usage_log)
    except (llm.LLMUnavailable, llm.LLMError) as e:
        warnings.append(f"LLM fallback failed: {e}")
        status = "needs_llm" if isinstance(e, llm.LLMUnavailable) else "partial"
        calls = [e.record] if getattr(e, "record", None) else []
        return parsed("pdf", PARSER, sink, meta, warnings, status=status, llm_calls=calls)
    for page in result["pages"]:
        if page["page_number"] in blank_pages:
            _add_lines(sink, page["page_number"], page["lines"], f"pdf@{prompts.PROMPT_VERSION}")
    return parsed("pdf", f"{PARSER}+llm_pdf@{prompts.PROMPT_VERSION}", sink, meta, warnings, llm_calls=[call])
