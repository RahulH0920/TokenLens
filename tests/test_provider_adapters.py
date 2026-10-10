"""Comprehensive test suite for Multi-Provider Payload Normalization Adapters.

Tests normalization across:
1. OpenAI ChatCompletion format
2. Anthropic Messages API format
3. Google Gemini format
4. Simulated/Custom provider format
5. Format auto-detection and metadata attribution
"""

import pytest
from decimal import Decimal
from datetime import date
from pathlib import Path

from core.provider_adapters import ProviderAdapterRegistry
from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.models import PricingRecord


@pytest.fixture
def engine():
    importer = DataImporter()
    pricing = importer.load_pricing(Path("data/model_pricing.csv"))
    # Also add test rate cards for multi-provider coverage
    pricing.append(
        PricingRecord(
            model="simulated-fast-llm",
            provider="simulated",
            input_usd_per_1m=Decimal("0.50"),
            output_usd_per_1m=Decimal("1.50"),
            cached_usd_per_1m=Decimal("0.05"),
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )
    )
    eng = CostEngine()
    eng.load_pricing_records(pricing)
    return eng


def test_openai_format_adapter(engine):
    registry = ProviderAdapterRegistry()
    openai_payload = {
        "id": "chatcmpl-test-12345",
        "object": "chat.completion",
        "created": 1768000000,
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "prompt_tokens_details": {
                "cached_tokens": 200
            }
        },
        "user": "usr_dev_42",
        "metadata": {
            "team": "engineering",
            "feature": "code-review"
        }
    }

    record, fmt = registry.parse_payload(openai_payload)
    assert fmt == "openai"
    assert record.provider == "openai"
    assert record.model == "gpt-4o"
    assert record.input_tokens == 1000
    assert record.output_tokens == 500
    assert record.cached_tokens == 200
    assert record.team == "engineering"
    assert record.feature == "code-review"
    assert record.user_id == "usr_dev_42"

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # gpt-4o: in=2.50, out=10.00, cached=1.25 per 1M
    # cost = (1000 * 2.5 + 500 * 10 + 200 * 1.25) / 1,000,000 = (2500 + 5000 + 250) / 1,000,000 = 0.007750
    assert priced.total_cost_usd == Decimal("0.007750")


def test_anthropic_format_adapter(engine):
    registry = ProviderAdapterRegistry()
    anthropic_payload = {
        "id": "msg_01XyZ999",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet",
        "usage": {
            "input_tokens": 2000,
            "output_tokens": 400,
            "cache_read_input_tokens": 500
        },
        "metadata": {
            "user_id": "usr_analyst_01",
            "team": "product",
            "feature": "doc-search"
        }
    }

    record, fmt = registry.parse_payload(anthropic_payload)
    assert fmt == "anthropic"
    assert record.provider == "anthropic"
    assert record.model == "claude-3-5-sonnet"
    assert record.input_tokens == 2000
    assert record.output_tokens == 400
    assert record.cached_tokens == 500
    assert record.team == "product"
    assert record.feature == "doc-search"

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # claude-3-5-sonnet: in=3.00, out=15.00, cached=0.30 per 1M
    # cost = (2000 * 3.0 + 400 * 15.0 + 500 * 0.30) / 1M = (6000 + 6000 + 150) / 1M = 0.012150
    assert priced.total_cost_usd == Decimal("0.012150")


def test_google_gemini_format_adapter(engine):
    registry = ProviderAdapterRegistry()
    gemini_payload = {
        "responseId": "gemini_resp_abc123",
        "model": "gemini-1.5-flash",
        "candidates": [{"content": {"parts": [{"text": "Sample completion"}]}}],
        "usageMetadata": {
            "promptTokenCount": 5000,
            "candidatesTokenCount": 1000,
            "cachedContentTokenCount": 1000
        },
        "metadata": {
            "team": "engineering",
            "feature": "automated-testing",
            "user_id": "gemini_agent"
        }
    }

    record, fmt = registry.parse_payload(gemini_payload)
    assert fmt == "google"
    assert record.provider == "google"
    assert record.model == "gemini-1.5-flash"
    assert record.input_tokens == 5000
    assert record.output_tokens == 1000
    assert record.cached_tokens == 1000
    assert record.team == "engineering"
    assert record.feature == "automated-testing"

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # gemini-1.5-flash: in=0.075, out=0.30, cached=0.01875 per 1M
    # cost = (5000 * 0.075 + 1000 * 0.30 + 1000 * 0.01875) / 1M = (375 + 300 + 18.75) / 1M = 0.000694
    assert priced.total_cost_usd == Decimal("0.000694")


def test_simulated_format_adapter(engine):
    registry = ProviderAdapterRegistry()
    simulated_payload = {
        "simulated_request_id": "sim_req_alpha_99",
        "provider": "simulated",
        "model": "simulated-fast-llm",
        "tokens": {
            "input_tokens": 4000,
            "output_tokens": 1000,
            "cached_tokens": 800
        },
        "latency_ms": 145,
        "tags": {
            "team": "product",
            "feature": "agent-chat",
            "user_id": "sim_bot"
        }
    }

    record, fmt = registry.parse_payload(simulated_payload)
    assert fmt == "simulated"
    assert record.provider == "simulated"
    assert record.model == "simulated-fast-llm"
    assert record.input_tokens == 4000
    assert record.output_tokens == 1000
    assert record.cached_tokens == 800
    assert record.team == "product"
    assert record.feature == "agent-chat"
    assert record.latency_ms == 145

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # simulated-fast-llm: in=0.50, out=1.50, cached=0.05 per 1M
    # cost = (4000 * 0.5 + 1000 * 1.5 + 800 * 0.05) / 1M = (2000 + 1500 + 40) / 1M = 0.003540
    assert priced.total_cost_usd == Decimal("0.003540")


def test_format_detection_heuristics():
    registry = ProviderAdapterRegistry()

    # Explicit hints
    assert registry.detect_format({}, format_hint="openai") == "openai"
    assert registry.detect_format({}, format_hint="anthropic") == "anthropic"
    assert registry.detect_format({}, format_hint="gemini") == "google"
    assert registry.detect_format({}, format_hint="simulated") == "simulated"

    # Signature detection
    assert registry.detect_format({"candidates": []}) == "google"
    assert registry.detect_format({"type": "message", "usage": {}}) == "anthropic"
    assert registry.detect_format({"object": "chat.completion"}) == "openai"
    assert registry.detect_format({"simulated_request_id": "sim_1"}) == "simulated"

    # Model prefix fallback
    assert registry.detect_format({"model": "claude-3-opus"}) == "anthropic"
    assert registry.detect_format({"model": "gemini-pro"}) == "google"
    assert registry.detect_format({"model": "sim-llm"}) == "simulated"


def test_header_attribution_injection():
    registry = ProviderAdapterRegistry()
    raw_payload = {
        "id": "chatcmpl-headers-test",
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50}
    }
    custom_headers = {
        "x-team": "growth-marketing",
        "x-feature": "email-generation",
        "x-user-id": "marketer_bob",
        "x-env": "staging"
    }

    record, fmt = registry.parse_payload(raw_payload, headers=custom_headers)
    assert fmt == "openai"
    assert record.team == "growth-marketing"
    assert record.feature == "email-generation"
    assert record.user_id == "marketer_bob"
    assert record.env == "staging"
