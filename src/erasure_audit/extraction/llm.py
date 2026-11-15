"""Pluggable model client for the few files deterministic parsers cannot read.

Provider and keys come from erasure_audit.settings (environment variables, then an optional .env):
LLM_PROVIDER = gemini (default) | anthropic | none, GEMINI_API_KEY, ANTHROPIC_API_KEY, EVIDENCE_LLM_MODEL,
EVIDENCE_LLM_THINKING, EVIDENCE_LLM_EFFORT. Keys are SecretStr and are never logged.

Every request that reaches an API appends one JSON line to the usage log (provider, model, input, output and
thinking tokens, estimated cost), and the same record is stored in the extraction document's `llm_calls`.
evaluation/usage_report.md is built from these records.

Determinism: Gemini runs at temperature 0. The Anthropic API rejects `temperature` on every model since
2026-10-21, so that path relies on the strict JSON schema and fixed prompts instead.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from ..settings import get_settings

DEFAULT_MODELS = {"gemini": "gemini-3.8-flash", "anthropic": "claude-opus-5-5"}
MAX_TOKENS = 16000
RETRIES, RETRY_BASE_S = 4, 5  # transient API errors: retry after 5 s, 15 s, 45 s, 135 s

# USD per million tokens: input, output (incl. thinking), cache write, cache read.
# Gemini: ai.google.dev/gemini-api/docs/pricing, paid standard tier, prices valid through 2026-12-31.
PRICING = {
    "gemini-3.8-flash": (0.75, 3.75, 0.0, 0.075),
    # gemini-3.6-flash: introductory standard-tier price through 2026-12-31; $1.50 / $7.50 from 2027-01-01.
    "gemini-3.6-flash": (0.75, 3.75, 0.0, 0.075),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.0, 0.03),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
}


_CAPTURE: ContextVar[list[dict] | None] = ContextVar("llm_usage_capture", default=None)


@contextmanager
def capture_usage() -> Iterator[list[dict]]:
    """Collect the usage record of every model call made inside the block (per evaluation case, per API request).
    Context-local, so concurrent requests or workflow activities never see each other's calls."""
    calls: list[dict] = []
    token = _CAPTURE.set(calls)
    try:
        yield calls
    finally:
        _CAPTURE.reset(token)


def record_usage(record: dict) -> None:
    """Add a usage record to the active capture (if any). Used for real calls and for cache hits."""
    calls = _CAPTURE.get()
    if calls is not None:
        calls.append(record)


class LLMUnavailable(RuntimeError):
    """No provider configured or no usable key; the caller records the file as needs_llm and continues."""


class LLMError(RuntimeError):
    """A request was made but did not produce a usable result."""


def settings() -> dict:
    cfg = get_settings()
    return {
        "provider": cfg.llm_provider,
        "model": cfg.model_for() or DEFAULT_MODELS.get(cfg.llm_provider),
        "thinking": cfg.llm_thinking,
        "effort": cfg.llm_effort,
        "key": cfg.api_key(),
    }


def estimate_cost(model: str, usage: dict) -> float | None:
    price = PRICING.get(model)
    if not price:
        return None
    p_in, p_out, p_cw, p_cr = price
    billed_in = usage["input_tokens"] - usage["cache_read_input_tokens"]
    return round((billed_in * p_in + (usage["output_tokens"] + usage["thinking_tokens"]) * p_out
                  + usage["cache_creation_input_tokens"] * p_cw + usage["cache_read_input_tokens"] * p_cr) / 1e6, 6)


def is_daily_quota(message: str) -> bool:
    """A per-day quota 429 will not clear within any retry budget: give up at once instead of backing off."""
    m = message.lower()
    return "per day" in m or "perday" in m or "requests_per_day" in m


# --------------------------------------------------------------------------------------------- Gemini
_GEMINI_MODELS: set[str] | None = None


def _gemini(cfg, system, user_text, attachments, schema, record):
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError as e:
        raise LLMUnavailable("google-genai not installed (pip install 'erasure-audit[llm]')") from e
    client = genai.Client(api_key=cfg["key"])
    global _GEMINI_MODELS
    try:
        if _GEMINI_MODELS is None:  # verify the configured model really exists for this key
            _GEMINI_MODELS = {m.name.removeprefix("models/") for m in client.models.list()}
        if cfg["model"] not in _GEMINI_MODELS:
            flash = sorted(m for m in _GEMINI_MODELS if "flash" in m)
            raise LLMUnavailable(f"model {cfg['model']!r} not available for this key; Flash models: {flash}")
        record["sent"] = True
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            max_output_tokens=MAX_TOKENS,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=cfg["thinking"]),
        )
        contents = [*(types.Part.from_bytes(data=data, mime_type=mime) for data, mime in attachments), user_text]
        for attempt in range(1, RETRIES + 2):
            record["attempts"] = attempt
            try:
                response = client.models.generate_content(model=cfg["model"], contents=contents, config=config)
                break
            except errors.APIError as e:  # overload (503), other 5xx and rate limits (429) are transient
                if e.code == 429 and is_daily_quota(str(e.message)):
                    raise LLMUnavailable(f"Gemini daily quota exhausted: {e.message}") from e
                if attempt > RETRIES or not (e.code == 429 or (e.code or 0) >= 500):
                    raise
                time.sleep(RETRY_BASE_S * 3 ** (attempt - 1))
    except errors.ClientError as e:
        if e.code in (401, 403):
            raise LLMUnavailable(f"Gemini rejected the key ({e.code})") from e
        raise LLMError(f"Gemini client error {e.code}: {e.message}") from e
    except errors.APIError as e:
        raise LLMError(f"Gemini API error {e.code}: {e.message}") from e

    u = response.usage_metadata
    record.update({
        "model": getattr(response, "model_version", None) or cfg["model"],
        "input_tokens": (u.prompt_token_count or 0) if u else 0,
        "output_tokens": (u.candidates_token_count or 0) if u else 0,
        "thinking_tokens": (u.thoughts_token_count or 0) if u else 0,
        "cache_read_input_tokens": (u.cached_content_token_count or 0) if u else 0,
    })
    finish = response.candidates[0].finish_reason if response.candidates else None
    record["stop_reason"] = str(getattr(finish, "name", finish))
    if record["stop_reason"] not in ("STOP", "None"):
        raise LLMError(f"Gemini finished with {record['stop_reason']}")
    return response.text


# --------------------------------------------------------------------------------------------- Anthropic
def _anthropic(cfg, system, user_text, attachments, schema, record):
    try:
        import anthropic
    except ImportError as e:
        raise LLMUnavailable("anthropic SDK not installed (pip install 'erasure-audit[llm]')") from e
    import base64
    client = anthropic.Anthropic(api_key=cfg["key"], max_retries=4)
    content = []
    for data, mime in attachments:
        kind = "document" if mime == "application/pdf" else "image"
        content.append({"type": kind, "source": {"type": "base64", "media_type": mime,
                                                 "data": base64.standard_b64encode(data).decode("ascii")}})
    content.append({"type": "text", "text": user_text})
    try:
        record["sent"] = True
        response = client.messages.create(
            model=cfg["model"], max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": schema}},
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise LLMUnavailable(f"Anthropic rejected the key: {e}") from e
    except anthropic.RateLimitError as e:
        raise LLMError(f"rate limited after retries: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"connection error: {e}") from e
    u = response.usage
    record.update({
        "model": response.model,
        "input_tokens": (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0),
        "output_tokens": u.output_tokens or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "stop_reason": response.stop_reason,
    })
    if response.stop_reason != "end_turn":
        raise LLMError(f"Anthropic stopped with {response.stop_reason}")
    return next((b.text for b in response.content if b.type == "text"), None)


# --------------------------------------------------------------------------------------------- entry point
def call_structured(*, purpose: str, file_id: str, sha256: str, prompt_id: str, system: str, user_text: str,
                    attachments: list[tuple[bytes, str]], schema: dict, usage_log: Path) -> tuple[dict, dict]:
    """One request constrained to `schema`. attachments = [(bytes, mime_type)]. Returns (parsed_json, usage_record).
    On failure raises LLMUnavailable / LLMError with `.record` set when the request reached an API."""
    cfg = settings()
    record = {
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": purpose, "file_id": file_id, "sha256": sha256, "prompt_id": prompt_id,
        "provider": cfg["provider"], "requested_model": cfg["model"], "model": cfg["model"],
        "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "stop_reason": None, "duration_s": None, "estimated_cost_usd": None, "error": None, "sent": False,
    }
    started = time.monotonic()
    try:
        if cfg["provider"] == "none":
            raise LLMUnavailable("LLM_PROVIDER=none")
        if not cfg["key"]:
            raise LLMUnavailable(f"{cfg['provider'].upper()}_API_KEY is not set")
        call = _gemini if cfg["provider"] == "gemini" else _anthropic
        text = call(cfg, system, user_text, attachments, schema, record)
        if not text:
            raise LLMError("empty response")
        try:
            return json.loads(text), record
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON from model: {e}") from e
    except (LLMUnavailable, LLMError) as e:
        record["error"] = str(e)
        e.record = record if record["sent"] else None
        raise
    finally:
        record["duration_s"] = round(time.monotonic() - started, 2)
        sent = record.pop("sent")
        if sent:  # every request that reached an API, including failed ones
            record["estimated_cost_usd"] = estimate_cost(record["model"], record) or estimate_cost(record["requested_model"], record)
            usage_log.parent.mkdir(parents=True, exist_ok=True)
            with open(usage_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")
            record_usage(record)
