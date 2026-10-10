"""Security test suite verifying Phase 3 API protection requirements:
- Keep the API on localhost unless access controls are verified.
- Protect secrets and sensitive exports.
- Validate incoming requests and restrict dangerous operations.
- Verify unauthorized API calls are rejected.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

TEST_VALID_TOKEN = "a" * 32
TEST_INVALID_TOKEN = "wrong_secret_token_value_here_123"

from api import main as api_main
from api.main import app


def service_client(client=("127.0.0.1", 50000)):
    return TestClient(
        app,
        client=client,
        headers={"Authorization": f"Bearer {TEST_VALID_TOKEN}"},
    )


@pytest.fixture(autouse=True)
def stable_test_service_token(monkeypatch):
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    monkeypatch.setenv("FINOPS_API_TOKEN", TEST_VALID_TOKEN)


def test_public_health_endpoint_accessible():
    """Verify /health is accessible to clients without authentication."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "llm-finops-backend"}


def test_remote_client_rejected_when_no_token_configured(monkeypatch):
    """Verify remote clients are blocked (403) when no FINOPS_API_TOKEN is set."""
    monkeypatch.delenv("FINOPS_API_TOKEN", raising=False)
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    client = TestClient(app, client=("198.51.100.25", 54321))
    response = client.get("/api/v1/summary")
    assert response.status_code == 403
    assert "Remote access is disabled" in response.json()["detail"]


def test_localhost_client_requires_user_or_service_credentials(monkeypatch):
    """Loopback transport is not itself an account credential."""
    monkeypatch.setenv("FINOPS_API_TOKEN", TEST_VALID_TOKEN)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/api/v1/options")
    assert response.status_code == 401


def test_unauthorized_call_rejected_without_bearer_token(monkeypatch):
    """Verify unauthorized calls receive 401 when FINOPS_API_TOKEN is configured."""
    monkeypatch.setenv("FINOPS_API_TOKEN", TEST_VALID_TOKEN)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/api/v1/summary")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_unauthorized_call_rejected_with_invalid_token(monkeypatch):
    """Verify calls with incorrect Bearer token are rejected with 401."""
    monkeypatch.setenv("FINOPS_API_TOKEN", TEST_VALID_TOKEN)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get(
        "/api/v1/summary",
        headers={"Authorization": f"Bearer {TEST_INVALID_TOKEN}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_authorized_call_succeeds_with_valid_bearer_token(monkeypatch):
    """Verify valid Bearer token authenticates successfully for both local and remote callers."""
    monkeypatch.setenv("FINOPS_API_TOKEN", TEST_VALID_TOKEN)
    client = service_client(("198.51.100.25", 54321))
    response = client.get(
        "/api/v1/summary",
        headers={"Authorization": f"Bearer {TEST_VALID_TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_spend_usd" in data
    assert "total_requests" in data


def test_security_response_headers_present():
    """Verify baseline security headers and cache prevention are applied to responses."""
    client = service_client()
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "camera=()" in response.headers["Permissions-Policy"]


def test_request_validation_forbids_extra_fields():
    """Verify request body strictly forbids extraneous fields (mass assignment guard)."""
    client = service_client()
    payload = {
        "request_id": "sec_test_extra_01",
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 50,
        "malicious_injected_admin_field": True,
    }
    response = client.post("/api/v1/requests", json=payload)
    assert response.status_code == 422


def test_request_validation_cached_tokens_cannot_exceed_input():
    """Verify logical consistency: cached_tokens <= input_tokens."""
    client = service_client()
    payload = {
        "request_id": "sec_test_tokens_02",
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 50,
        "cached_tokens": 500,  # exceeds input_tokens
    }
    response = client.post("/api/v1/requests", json=payload)
    assert response.status_code == 422


def test_request_validation_mutually_exclusive_timestamps():
    """Verify providing both timestamp and timestamp_utc is rejected."""
    client = service_client()
    payload = {
        "request_id": "sec_test_timestamps_03",
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 50,
        "timestamp": "2026-10-01T12:00:00Z",
        "timestamp_utc": "2026-10-01T12:00:00Z",
    }
    response = client.post("/api/v1/requests", json=payload)
    assert response.status_code == 422


def test_request_validation_negative_token_count_rejected():
    """Verify negative token counts are rejected."""
    client = service_client()
    payload = {
        "request_id": "sec_test_neg_04",
        "model": "gpt-4o",
        "input_tokens": -10,
        "output_tokens": 50,
    }
    response = client.post("/api/v1/requests", json=payload)
    assert response.status_code == 422


def test_post_requires_content_length():
    """Verify POST requests without Content-Length are rejected with 411 Length Required."""
    client = service_client()
    # Send raw request without content-length header
    response = client.post("/api/v1/requests", content=b"{}", headers={"content-length": ""})
    assert response.status_code in {411, 413}


def test_oversized_payload_rejected_with_413():
    """Verify requests declaring Content-Length > MAX_BODY_BYTES are rejected with 413."""
    client = service_client()
    response = client.post(
        "/api/v1/requests",
        content=b"{}",
        headers={"content-length": "1000000"},  # 1 MB exceeds 64 KiB default
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large"


def test_duplicate_request_id_rejected_with_409():
    """Verify duplicate request_id returns 409 Conflict."""
    client = service_client()
    payload = {
        "request_id": "duplicate_id_sec_test_999",
        "model": "gpt-4o",
        "input_tokens": 50,
        "output_tokens": 20,
    }
    # First write should succeed
    res1 = client.post("/api/v1/requests", json=payload)
    assert res1.status_code == 201

    # Second write with same ID must be rejected with 409
    res2 = client.post("/api/v1/requests", json=payload)
    assert res2.status_code == 409
    assert "already exists" in res2.json()["detail"]


def test_query_validation_date_range_inverted():
    """Verify invalid date ranges return 422."""
    client = service_client()
    response = client.get("/api/v1/summary?start_date=2026-10-10&end_date=2026-10-01")
    assert response.status_code == 422
    assert "start_date must be on or before end_date" in response.json()["detail"]


def test_query_validation_pagination_limits():
    """Verify requests pagination limit cannot exceed 1000."""
    client = service_client()
    response = client.get("/api/v1/requests?limit=5000")
    assert response.status_code == 422
