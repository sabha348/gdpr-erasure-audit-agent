from pathlib import Path

from erasure_audit.settings import Settings


def test_process_env_wins_over_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=anthropic\nERASURE_CACHE_DIR=/from/dotenv\n", encoding="utf-8")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ERASURE_CACHE_DIR", "/from/env")
    s = Settings(_env_file=env)
    assert s.llm_provider == "anthropic"
    assert s.cache_dir == Path("/from/env")
    assert s.usage_log == Path("/from/env/llm_calls.jsonl")


def test_keys_never_leak_in_repr(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key-123")
    s = Settings(_env_file=None)
    assert s.api_key("gemini") == "not-a-real-key-123"
    assert "not-a-real-key-123" not in repr(s)
    assert "not-a-real-key-123" not in s.model_dump_json()


def test_unknown_provider_is_rejected(monkeypatch):
    import pydantic
    import pytest
    monkeypatch.setenv("LLM_PROVIDER", "mystery")
    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None)
