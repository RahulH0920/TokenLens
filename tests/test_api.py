"""Tests for FastAPI backend endpoints, cached tokens, and exception inspection."""

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_api_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_api_summary_includes_cached_metrics():
    res = client.get("/api/v1/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_cached_tokens" in data
    assert "total_cached_cost_usd" in data
    assert data["total_cached_tokens"] == 420691
    assert data["missing_pricing_requests"] == 6
    # Mathematical reconciliation: input_cost + output_cost + cached_cost == total_spend
    calc_sum = data["total_input_cost_usd"] + data["total_output_cost_usd"] + data["total_cached_cost_usd"]
    assert abs(data["total_spend_usd"] - calc_sum) < 0.0001


def test_api_exceptions_endpoint():
    res = client.get("/api/v1/exceptions")
    assert res.status_code == 200
    data = res.json()
    assert data["duplicate_requests_count"] == 5
    assert data["rejected_records_count"] == 8
    assert data["missing_pricing_count"] == 6
    assert len(data["duplicate_records"]) == 5
