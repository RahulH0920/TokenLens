"""Tests for proxy routing: Real-time API key usage for Gemini & ChatGPT, dummy data for Claude."""

import pytest
from starlette.testclient import TestClient

from scripts.mock_proxy import app


@pytest.fixture
def proxy_client():
    return TestClient(app)


def test_proxy_health_contains_all_three_models(proxy_client):
    res = proxy_client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "models" in data
    assert "chatgpt" in data["models"]
    assert "gemini" in data["models"]
    assert "claude" in data["models"]
    assert data["models"]["chatgpt"]["model"] == "gpt-4o"
    assert data["models"]["chatgpt"]["mode"] == "real-time"
    assert data["models"]["gemini"]["model"] == "gemini-1.5-flash"
    assert data["models"]["gemini"]["mode"] == "real-time"
    assert data["models"]["claude"]["model"] == "claude-3-5-sonnet"
    assert data["models"]["claude"]["mode"] == "dummy-data"


def test_dummy_model_chat_completions_succeeds_without_api_key(proxy_client):
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Analyze our infrastructure spending."}],
    }
    headers = {
        "x-team": "engineering",
        "x-feature": "code-review",
        "x-user": "test_user_42",
        "x-env": "production",
    }
    res = proxy_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["model"] == "claude-3-5-sonnet"
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert "usage" in data
    assert data["usage"]["prompt_tokens"] > 0
    assert data["usage"]["completion_tokens"] > 0
    assert res.headers.get("x-finops-dummy-mode") == "true"
    assert "x-finops-request-id" in res.headers
    assert "x-finops-cost-usd" in res.headers
    assert float(res.headers["x-finops-cost-usd"]) > 0


def test_dummy_model_anthropic_messages_endpoint(proxy_client):
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Help summarize our FinOps findings."}],
    }
    headers = {
        "x-team": "product",
        "x-feature": "doc-search",
    }
    res = proxy_client.post("/v1/messages", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["model"] == "claude-3-5-sonnet"
    assert "usage" in data
    assert res.headers.get("x-finops-dummy-mode") == "true"


def test_unregistered_model_rejected(proxy_client):
    payload = {
        "model": "unregistered-custom-model",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    res = proxy_client.post("/v1/chat/completions", json=payload)
    assert res.status_code == 400
    assert "not in the active Model Registry" in res.json()["detail"]


def test_real_time_openai_without_key_returns_503(proxy_client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello OpenAI"}],
    }
    res = proxy_client.post("/v1/chat/completions", json=payload)
    assert res.status_code == 503
    assert "OPENAI_API_KEY" in res.json()["detail"]


def test_real_time_gemini_without_key_returns_503(proxy_client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    payload = {
        "contents": [{"parts": [{"text": "Hello Gemini"}]}],
    }
    res = proxy_client.post("/v1beta/models/gemini-1.5-flash:generateContent", json=payload)
    assert res.status_code == 503
    assert "GEMINI_API_KEY" in res.json()["detail"]
