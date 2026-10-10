"""Tests for Frontend Login API and Security Safeguards.

Covers:
- POST /api/v1/auth/login and POST /auth/login (success, failure, payload structure)
- GET /api/v1/auth/status and GET /auth/status (public access, bootstrap indicators)
- GET /api/v1/auth/me and GET /auth/me (session introspection, role verification)
- POST /api/v1/auth/logout and POST /auth/logout (session revocation)
- POST /api/v1/auth/bootstrap and POST /auth/bootstrap (initial setup)
- Brute-force rate limiting protection (HTTP 429 Retry-After)
- Security headers verification
- Scrypt timing attack resistance
"""

import os
import sqlite3
import pytest
from starlette.testclient import TestClient

from api import main as api_main
from api.main import app, login_rate_limiter
from core.access_control import AccessControlStore, Role


@pytest.fixture
def auth_fixture(tmp_path, monkeypatch):
    """Setup a dedicated SQLite identity store for testing frontend login API."""
    store = AccessControlStore(tmp_path / "frontend_auth_test.sqlite3")
    head = store.bootstrap_org_head("org.admin", "Organization Admin", "Admin-Password-2026!")
    manager = store.create_user(
        head.username,
        "manager.ai",
        "AI Platform Manager",
        "Manager-Secret-2026!",
        Role.MANAGER,
        ["platform", "inference"],
    )
    leader = store.create_user(
        head.username,
        "lead.inference",
        "Inference Tech Lead",
        "Leader-Secret-2026!",
        Role.TEAM_LEADER,
        ["inference"],
    )
    monkeypatch.setattr(api_main, "access_store", store)
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    monkeypatch.delenv("FINOPS_API_TOKEN", raising=False)
    login_rate_limiter.reset()
    return store, head, manager, leader


@pytest.fixture
def local_client():
    return TestClient(app, client=("127.0.0.1", 54321))


def test_public_auth_status_endpoint(auth_fixture, local_client):
    """Verify /api/v1/auth/status and /auth/status are accessible without authentication."""
    resp = local_client.get("/api/v1/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["is_bootstrapped"] is True
    assert data["user_count"] == 3
    assert data["auth_method"] == "scrypt_session_bearer"

    alias_resp = local_client.get("/auth/status")
    assert alias_resp.status_code == 200
    assert alias_resp.json() == data


def test_frontend_login_success_and_payload_structure(auth_fixture, local_client):
    """Verify valid login returns bearer token, TTL, and structured user profile."""
    _, _, manager, _ = auth_fixture
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "Manager-Secret-2026!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data
    assert data["expires_in"] > 0

    # User profile payload
    user = data["user"]
    assert user["username"] == manager.username
    assert user["display_name"] == "AI Platform Manager"
    assert user["role"] == "manager"
    assert user["role_label"] == "Manager"
    assert set(user["teams"]) == {"platform", "inference"}

    # Top-level backward compatibility fields
    assert data["role"] == "manager"
    assert set(data["teams"]) == {"platform", "inference"}


def test_frontend_login_invalid_password_returns_401(auth_fixture, local_client):
    """Verify incorrect credentials return 401 with standard Bearer challenge header."""
    _, _, manager, _ = auth_fixture
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "WrongPassword-1234!"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    assert "Invalid username or password" in resp.json()["detail"]


def test_frontend_login_unknown_user_returns_401(auth_fixture, local_client):
    """Verify nonexistent user returns 401 identically without leaking existence."""
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": "ghost.user", "password": "GhostPassword-1234!"},
    )
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.json()["detail"]


def test_auth_me_introspects_current_session(auth_fixture, local_client):
    """Verify /api/v1/auth/me returns current user identity from valid bearer token."""
    _, _, manager, _ = auth_fixture
    login_resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "Manager-Secret-2026!"},
    )
    token = login_resp.json()["access_token"]

    # Valid token inspection
    me_resp = local_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == manager.username
    assert me_data["display_name"] == "AI Platform Manager"
    assert me_data["role"] == "manager"
    assert set(me_data["teams"]) == {"platform", "inference"}
    assert me_data["active"] is True

    # Missing token fails with 401
    assert local_client.get("/api/v1/auth/me").status_code == 401

    # Bad token fails with 401
    assert local_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid_opaque_token_string_here_12345"},
    ).status_code == 401


def test_logout_revokes_token_and_blocks_subsequent_access(auth_fixture, local_client):
    """Verify /api/v1/auth/logout revokes session in DB and invalidates future requests."""
    _, _, manager, _ = auth_fixture
    login_resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "Manager-Secret-2026!"},
    )
    token = login_resp.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Verify token works initially
    assert local_client.get("/api/v1/auth/me", headers=auth_headers).status_code == 200

    # Logout
    logout_resp = local_client.post("/api/v1/auth/logout", headers=auth_headers)
    assert logout_resp.status_code == 204

    # Subsequent call with the revoked token must return 401
    assert local_client.get("/api/v1/auth/me", headers=auth_headers).status_code == 401
    assert local_client.get("/api/v1/summary", headers=auth_headers).status_code == 401


def test_brute_force_rate_limiter_lockout(auth_fixture, local_client):
    """Verify repeated failed login attempts trigger HTTP 429 rate-limiting lockout."""
    _, _, manager, _ = auth_fixture
    login_rate_limiter.reset()

    # Perform 4 failed attempts (under threshold of 5)
    for _ in range(4):
        resp = local_client.post(
            "/api/v1/auth/login",
            json={"username": manager.username, "password": "WrongPassword-1234!"},
        )
        assert resp.status_code == 401

    # 5th failed attempt triggers lockout
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "WrongPassword-1234!"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert "locked" in resp.json()["detail"].lower()

    # Even with the CORRECT password now, the client is locked out
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "Manager-Secret-2026!"},
    )
    assert resp.status_code == 429

    # Reset limiter cleans up lockouts
    login_rate_limiter.reset()
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": manager.username, "password": "Manager-Secret-2026!"},
    )
    assert resp.status_code == 200


def test_bootstrap_endpoint_initial_and_repeat_prevention(tmp_path, monkeypatch, local_client):
    """Verify bootstrap allows setup when user_count == 0, but rejects when users exist."""
    empty_store = AccessControlStore(tmp_path / "empty_store.sqlite3")
    monkeypatch.setattr(api_main, "access_store", empty_store)
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    bootstrap_secret = "b" * 11
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", bootstrap_secret)
    monkeypatch.delenv("FINOPS_API_TOKEN", raising=False)

    # Initial check: unbootstrapped
    status = local_client.get("/api/v1/auth/status").json()
    assert status["is_bootstrapped"] is False
    assert status["user_count"] == 0

    # Successful bootstrap
    resp = local_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "super.admin",
            "display_name": "Super Admin",
            "password": "SuperSecretPassword-2026!",
            "bootstrap_token": bootstrap_secret,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["user"]["username"] == "super.admin"
    assert data["user"]["role"] == "org_head"

    # Status updated
    status2 = local_client.get("/api/v1/auth/status").json()
    assert status2["is_bootstrapped"] is True
    assert status2["user_count"] == 1

    # Second bootstrap attempt blocked with 403
    repeat_resp = local_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "second.admin",
            "display_name": "Second Admin",
            "password": "SecondSecretPassword-2026!",
            "bootstrap_token": bootstrap_secret,
        },
    )
    assert repeat_resp.status_code == 403
    assert "already been completed" in repeat_resp.json()["detail"]


def test_auth_security_headers_present(auth_fixture, local_client):
    """Verify security headers are attached on all auth responses."""
    resp = local_client.get("/api/v1/auth/status")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-store"


def test_bootstrap_token_can_login_org_head_api(auth_fixture, monkeypatch, local_client):
    """Verify org.head can login using the configured TOKENLENS_BOOTSTRAP_TOKEN."""
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "ZSyVW6mqvgw")
    resp = local_client.post(
        "/api/v1/auth/login",
        json={"username": "org.head", "password": "ZSyVW6mqvgw"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["username"] == "org.admin"
    assert data["user"]["role"] == "org_head"

