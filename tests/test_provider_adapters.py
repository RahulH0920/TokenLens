"""Tests for multi-provider adapters (OpenAI, Anthropic, Google, Simulated) and Phase 4 FinOps endpoints."""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from pathlib import Path

from core.provider_adapters import ProviderAdapterRegistry
from core.cost_engine import CostEngine
from core.importer import DataImporter
from api.main import app


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def engine():
    importer = DataImporter()
    pricing = importer.load_pricing(Path("data/model_pricing.csv"))
    eng = CostEngine()
    eng.load_pricing_records(pricing)
    return eng


def test_openai_format_adapter(engine):
    registry = ProviderAdapterRegistry()
    openai_payload = {
        "id": "chatcmpl-test-12345",
        "object": "chat.completion",
        "created": 1768000000,  # 2026 timestamp
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
        "model": "claude-3-haiku",
        "usage": {
            "input_tokens": 2000,
            "output_tokens": 400,
            "cache_read_input_tokens": 500
        },
        "metadata": {
            "user_id": "usr_analyst_01",
            "team": "research",
            "feature": "doc-search"
        }
    }

    record, fmt = registry.parse_payload(anthropic_payload)
    assert fmt == "anthropic"
    assert record.provider == "anthropic"
    assert record.model == "claude-3-haiku"
    assert record.input_tokens == 2000
    assert record.output_tokens == 400
    assert record.cached_tokens == 500
    assert record.team == "research"
    assert record.feature == "doc-search"

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # claude-3-haiku: in=0.25, out=1.25, cached=0.03 per 1M
    # cost = (2000 * 0.25 + 400 * 1.25 + 500 * 0.03) / 1M = (500 + 500 + 15) / 1M = 0.001015
    assert priced.total_cost_usd == Decimal("0.001015")


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

    priced = engine.calculate_request_cost(record)
    assert not priced.missing_price
    # simulated-fast-llm: in=0.50, out=1.50, cached=0.05 per 1M
    # cost = (4000 * 0.5 + 1000 * 1.5 + 800 * 0.05) / 1M = (2000 + 1500 + 40) / 1M = 0.003540
    assert priced.total_cost_usd == Decimal("0.003540")


def test_api_multi_provider_ingestion_endpoint(test_client):
    # Test OpenAI format ingestion
    resp_openai = test_client.post(
        "/api/v1/ingest/provider",
        json={
            "id": "chatcmpl_live_test_01",
            "model": "gpt-4o-mini",
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 800,
                "prompt_tokens_details": {"cached_tokens": 300}
            },
            "metadata": {"team": "support", "feature": "agent-chat"}
        }
    )
    assert resp_openai.status_code == 200
    data_o = resp_openai.json()
    assert data_o["detected_provider_format"] == "openai"
    assert data_o["team"] == "support"
    assert data_o["model"] == "gpt-4o-mini"
    assert not data_o["missing_price"]

    # Test Anthropic format ingestion
    resp_anthropic = test_client.post(
        "/api/v1/ingest/provider",
        json={
            "id": "msg_live_test_02",
            "model": "claude-3-haiku",
            "usage": {
                "input_tokens": 3000,
                "output_tokens": 600,
                "cache_read_input_tokens": 400
            },
            "metadata": {"team": "marketing", "feature": "summarisation"}
        }
    )
    assert resp_anthropic.status_code == 200
    data_a = resp_anthropic.json()
    assert data_a["detected_provider_format"] == "anthropic"
    assert data_a["provider"] == "anthropic"

    # Test Simulated provider format ingestion
    resp_sim = test_client.post(
        "/api/v1/ingest/provider",
        json={
            "simulated_request_id": "sim_live_test_03",
            "provider": "simulated",
            "model": "simulated-fast-llm",
            "tokens": {"input_tokens": 500, "output_tokens": 150, "cached_tokens": 50},
            "tags": {"team": "engineering", "feature": "doc-search"}
        }
    )
    assert resp_sim.status_code == 200
    data_s = resp_sim.json()
    assert data_s["detected_provider_format"] == "simulated"
    assert data_s["provider"] == "simulated"


def test_api_budgets_endpoint(test_client):
    resp = test_client.get("/api/v1/budgets")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_budget_usd" in data
    assert "overall_utilization_pct" in data
    assert "departments" in data
    assert len(data["departments"]) >= 5
    teams = [d["team"] for d in data["departments"]]
    assert "engineering" in teams
    assert "research" in teams


def test_api_insights_and_recommendations_endpoints(test_client):
    resp_ins = test_client.get("/api/v1/insights")
    assert resp_ins.status_code == 200
    ins_data = resp_ins.json()
    assert "top_drivers" in ins_data
    assert "model_concentration" in ins_data
    assert ins_data["model_concentration"]["top_2_concentration_pct"] > 50

    resp_rec = test_client.get("/api/v1/recommendations")
    assert resp_rec.status_code == 200
    recs = resp_rec.json()
    assert len(recs) >= 1
    top_rec = recs[0]
    assert "projected_savings_usd" in top_rec
    assert "workload_feature" in top_rec
