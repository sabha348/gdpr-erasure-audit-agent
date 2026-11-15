"""The LLM backend's control flow with a fake model (no network, no key). The request text is taken from the
public dataset; the fake model's answer is arbitrary and only exercises caching, fallback and disagreement."""
import pytest

from conftest import DATASET
from erasure_audit.contract import read_csv
from erasure_audit.extraction import llm
from erasure_audit.pipeline import classify as clf

TEXT = read_csv(DATASET / "sample_deletion_requests.csv")[0]["request_text"]


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        answer = {"is_deletion": not clf.classify(TEXT)["is_deletion"], "request_type": "other", "language": "en",
                  "decisive_phrase": "x", "confidence": "high"}
        record = {"provider": "fake", "model": "fake-1", "input_tokens": 100, "output_tokens": 20,
                  "thinking_tokens": 0, "estimated_cost_usd": 0.001}
        llm.record_usage(record)
        return answer, record

    monkeypatch.setattr(llm, "call_structured", fake)
    return calls


def test_llm_answer_is_cached_and_cache_hits_are_recorded(fake_model):
    with llm.capture_usage() as first:
        a = clf.classify_llm(TEXT)
    with llm.capture_usage() as second:
        b = clf.classify_llm(TEXT)
    assert len(fake_model) == 1
    assert a["method"] == b["method"] == "llm"
    assert not first[0].get("cached") and second[0]["cached"] is True


def test_disagreement_with_rules_is_flagged(fake_model):
    result = clf.classify_llm(TEXT + " ")  # same text after strip -> cached answer
    assert result["disagreement"] is True


def test_unavailable_model_falls_back_to_rules(monkeypatch):
    def unavailable(**_):
        raise llm.LLMUnavailable("no key")

    monkeypatch.setattr(llm, "call_structured", unavailable)
    result = clf.classify_llm(TEXT + " unique suffix so the cache misses")
    assert result["method"] == "rules_fallback"
    assert result["is_deletion"] == clf.classify(TEXT + " unique suffix so the cache misses")["is_deletion"]


def test_daily_quota_is_not_retried():
    msg = "Quota exceeded for metric: ..._free_tier_requests, description: Generate content requests per day limit exceeded"
    assert llm.is_daily_quota(msg)
    assert not llm.is_daily_quota("Resource exhausted: requests per minute")
