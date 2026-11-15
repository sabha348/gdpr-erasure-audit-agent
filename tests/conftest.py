from __future__ import annotations

import os

import pytest

from paths import DATASET


@pytest.fixture(scope="session", autouse=True)
def _isolated_settings(tmp_path_factory):
    """Every test session gets its own extraction cache and never sees a developer's .env or API keys."""
    cache = tmp_path_factory.mktemp("cache")
    saved = dict(os.environ)
    os.environ.update({"ERASURE_DATASET_DIR": str(DATASET), "ERASURE_CACHE_DIR": str(cache),
                       "ERASURE_ENV_FILE": str(cache / "no.env"), "LLM_PROVIDER": "none"})
    for key in ("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
                "GEMINI_MODEL", "EVIDENCE_LLM_MODEL"):
        os.environ.pop(key, None)
    from erasure_audit.settings import reset_settings
    reset_settings()
    yield
    os.environ.clear()
    os.environ.update(saved)
    reset_settings()


@pytest.fixture(scope="session")
def pipeline(_isolated_settings):
    from erasure_audit.pipeline import Options, build
    return build(DATASET, Options())
