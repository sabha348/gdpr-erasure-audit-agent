"""Every prompt the extraction layer sends to a model. Bump PROMPT_VERSION when any text or schema
here changes; the version is part of the cache key, so cached LLM extractions are redone."""

PROMPT_VERSION = "2026-10-25.1"

# --------------------------------------------------------------------------------------------
# Screenshots of internal admin consoles (dataset/evidence/images/*.png)
# --------------------------------------------------------------------------------------------

SCREENSHOT_SYSTEM = """\
You transcribe screenshots of internal business software into structured JSON for an audit system.
You are a careful transcriber, not an analyst: record what is visibly written on the screen and nothing else.

Rules:
- Copy every identifier exactly as displayed: emails, user_* references, resource ids such as account_123,
  search_45, backup_9, data_flow_7, ticket and order references, phone numbers, file names, timestamps.
  Preserve case, punctuation, digits and spacing. Never normalise, complete, or correct a value.
- Do not infer values that are not visible, and do not summarise. If a field shows a dash or placeholder
  (for example "—"), record that character. If a section is absent, return an empty list or empty string.
- Transcribe every row of every table, every label/value pair of every side or detail panel, every summary card,
  and every activity or timeline entry, in on-screen order (top to bottom, left to right).
- Put any remaining visible text that fits nowhere else (footers, tab names, button labels) in other_text.
"""

SCREENSHOT_USER = "Transcribe this screenshot into the JSON structure."

_LABEL_VALUE = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
    "required": ["label", "value"],
    "additionalProperties": False,
}

SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "application": {"type": "string", "description": "Product or console name shown in the top bar"},
        "breadcrumb": {"type": "string"},
        "page_title": {"type": "string"},
        "subtitle": {"type": "string", "description": "Line under the title, e.g. ids or filters"},
        "badges": {"type": "array", "items": {"type": "string"}},
        "search_query": {"type": "string", "description": "Text in the search box, empty if placeholder only"},
        "summary_cards": {"type": "array", "items": _LABEL_VALUE},
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                },
                "required": ["title", "columns", "rows"],
                "additionalProperties": False,
            },
        },
        "detail_panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "fields": {"type": "array", "items": _LABEL_VALUE}},
                "required": ["title", "fields"],
                "additionalProperties": False,
            },
        },
        "activity": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel": {"type": "string"},
                    "time": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["panel", "time", "text"],
                "additionalProperties": False,
            },
        },
        "other_text": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["application", "breadcrumb", "page_title", "subtitle", "badges", "search_query",
                 "summary_cards", "tables", "detail_panels", "activity", "other_text"],
    "additionalProperties": False,
}

# --------------------------------------------------------------------------------------------
# PDFs without a usable text layer (fallback only; all dataset PDFs currently parse with pdfplumber)
# --------------------------------------------------------------------------------------------

PDF_SYSTEM = """\
You transcribe business documents into structured JSON for an audit system.
Record the visible text of each page line by line, in reading order. Copy identifiers, names, emails,
phone numbers, references, amounts and dates exactly as printed. Do not summarise, correct, or add anything.
"""

PDF_USER = "Transcribe every page of this document into the JSON structure."

PDF_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_number": {"type": "integer"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["page_number", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pages"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------------------------
# Request classification (pipeline/classify.py, option classifier=llm)
# Zero-shot on purpose: no labelled sample text appears in the prompt, so evaluation stays uncontaminated.
# --------------------------------------------------------------------------------------------

CLASSIFIER_PROMPT_VERSION = "2026-11-15.2"

CLASSIFIER_SYSTEM = """\
You triage privacy requests sent to an e-commerce company. Decide whether the request authorises erasure
of the person's account or personal data under GDPR Article 17 (right to erasure).

It IS an erasure request when the person asks to delete, erase, remove or close their account or their
personal data, in any language, including indirect wording ("I no longer want you to hold my data").
It is ALSO an erasure request when the person asks you to verify, confirm, check or audit that a deletion
of their account or data was carried out or is complete (a request for deletion verification authorises
the same audit as the deletion itself).
It is NOT an erasure request when the person asks only for something narrower or different:
- access to, or a copy / export / download of, their data;
- correction or update of data (e.g. a misspelled name or an outdated email);
- unsubscribing or opting out of marketing, newsletters or promotional lists only;
- help with login, passwords, invoices or orders;
- anything that explicitly says to keep the account or not to delete it.
If the person mixes an erasure request with other asks, it is still an erasure request, unless they
explicitly limit the deletion to a narrower scope (e.g. "remove me from marketing lists only").

Judge only from the text. Quote the decisive phrase verbatim in its original language.
"""

CLASSIFIER_USER = "Request text:\n<<<\n{text}\n>>>"

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "is_deletion": {"type": "boolean"},
        "request_type": {"type": "string", "enum": ["erasure", "access", "export", "correction",
                                                     "marketing_opt_out", "account_help", "other"]},
        "language": {"type": "string", "description": "ISO 639-1 code of the request text"},
        "decisive_phrase": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["is_deletion", "request_type", "language", "decisive_phrase", "confidence"],
    "additionalProperties": False,
}
