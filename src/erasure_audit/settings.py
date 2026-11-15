"""Runtime configuration, read from environment variables (and an optional, uncommitted `.env` file).

Process environment wins over `.env`. API keys are `SecretStr`, so they never appear in reprs, logs or
tracebacks. Relative paths resolve against the current working directory, which is the repository root for
local use and `/app` in the container image.

| Variable                 | Default    | Meaning                                            |
|--------------------------|------------|----------------------------------------------------|
| ERASURE_DATASET_DIR      | dataset    | participant-facing dataset directory               |
| ERASURE_CACHE_DIR        | .cache     | extraction cache and LLM usage log                 |
| ERASURE_ENV_FILE         | .env       | optional dotenv file                               |
| LLM_PROVIDER             | gemini     | gemini, anthropic or none                          |
| GEMINI_API_KEY           | (unset)    | key for the Gemini API                             |
| ANTHROPIC_API_KEY        | (unset)    | key for the Anthropic API                          |
| EVIDENCE_LLM_MODEL       | (provider default) | model override for evidence/classifier calls |
| EVIDENCE_LLM_THINKING    | low        | Gemini thinking level                              |
| EVIDENCE_LLM_EFFORT      | medium     | Anthropic effort level                             |
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get("ERASURE_ENV_FILE", ".env"), env_file_encoding="utf-8",
                                      extra="ignore", populate_by_name=True)

    dataset_dir: Path = Field(Path("dataset"), validation_alias=AliasChoices("ERASURE_DATASET_DIR"))
    cache_dir: Path = Field(Path(".cache"), validation_alias=AliasChoices("ERASURE_CACHE_DIR"))

    llm_provider: Literal["gemini", "anthropic", "none"] = Field("gemini", validation_alias=AliasChoices("LLM_PROVIDER"))
    gemini_api_key: SecretStr | None = Field(None, validation_alias=AliasChoices("GEMINI_API_KEY"))
    anthropic_api_key: SecretStr | None = Field(None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    llm_model: str | None = Field(None, validation_alias=AliasChoices("EVIDENCE_LLM_MODEL"))
    llm_thinking: str = Field("low", validation_alias=AliasChoices("EVIDENCE_LLM_THINKING"))
    llm_effort: str = Field("medium", validation_alias=AliasChoices("EVIDENCE_LLM_EFFORT"))

    @property
    def evidence_cache_dir(self) -> Path:
        return self.cache_dir / "evidence"

    @property
    def usage_log(self) -> Path:
        return self.cache_dir / "llm_calls.jsonl"

    def api_key(self, provider: str | None = None) -> str | None:
        secret = {"gemini": self.gemini_api_key, "anthropic": self.anthropic_api_key}.get(provider or self.llm_provider)
        return secret.get_secret_value() if secret else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Forget the cached settings (tests change the environment and call this)."""
    get_settings.cache_clear()
