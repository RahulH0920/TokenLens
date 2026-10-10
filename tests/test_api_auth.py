"""Credential login, short-lived bearer sessions, and API team scoping."""

import sqlite3

import pytest
from starlette.testclient import TestClient

from api import main as api_main
from api.main import LoginRateLimiter
from core.access_control import AccessControlStore, Role


@pytest.fixture
def api_accounts(tmp_path, monkeypatch):
    store = AccessControlStore(tmp_path / "api-access.sqlite3")
    head = store.bootstrap_org_head("org.head", "Organization Head", "Head-Password-2026!")
    manager = store.create_user(
        head.username,
        "manager.eng",
        "Engineering Manager",
        "Manager-Password-2026!",
        Role.MANAGER,
        ["engineering"],
    )
    leader = store.create_user(
        head.username,
        "leader.eng",
        "Engineering Lead",
        "Leader-Password-2026!",
        Role.TEAM_LEADER,
        ["engineering"],
    )
    monkeypatch.setattr(api_main, "access_store", store)
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    monkeypatch.delenv("FINOPS_API_TOKEN", raising=False)
    monkeypatch.setenv("FINOPS_ALLOW_REMOTE_LOGIN", "false")
    api_main.login_rate_limiter.reset()
    return store, head, manager, leader


@pytest.fixture
def empty_api_store(tmp_path, monkeypatch):
    store = AccessControlStore(tmp_path / "empty-api-access.sqlite3")
    monkeypatch.setattr(api_main, "access_store", store)
    monkeypatch.setattr(api_main, "API_TOKEN", None)
    monkeypatch.delenv("FINOPS_API_TOKEN", raising=False)
    monkeypatch.delenv("TOKENLENS_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.setenv("FINOPS_ALLOW_REMOTE_LOGIN", "false")
    api_main.login_rate_limiter.reset()
    return store


@pytest.fixture
def local_client():
    return TestClient(api_main.app, client=("127.0.0.1", 50000))


def login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_issues_hashed_scoped_session_and_logout_revokes_it(api_accounts, local_client):
    store, _, manager, _ = api_accounts
    response = login(local_client, manager.username, "Manager-Password-2026!")
    assert response.status_code == 200
    result = response.json()
    assert result["token_type"] == "bearer"
    assert result["role"] == "manager"
    assert result["teams"] == ["engineering"]

    raw_token = result["access_token"]
    with sqlite3.connect(store.path) as connection:
        (token_hash,) = connection.execute("SELECT token_hash FROM access_sessions").fetchone()
    assert token_hash != raw_token

    headers = {"Authorization": f"Bearer {raw_token}"}
    identity = local_client.get("/auth/me", headers=headers)
    assert identity.status_code == 200
    assert identity.json()["teams"] == ["engineering"]
    options = local_client.get("/api/v1/options", headers=headers)
    assert options.status_code == 200
    assert {team.casefold() for team in options.json()["team"]} == {"engineering"}
    assert local_client.get("/api/v1/summary?team=product", headers=headers).status_code == 403
    assert local_client.get("/api/v1/summary", headers=headers).status_code == 200

    logout_response = local_client.post("/auth/logout", headers=headers)
    assert logout_response.status_code == 204
    assert local_client.get("/api/v1/summary", headers=headers).status_code == 401


def test_login_rejects_bad_credentials_and_user_role_limits_writes(api_accounts, local_client):
    _, _, manager, leader = api_accounts
    assert login(local_client, manager.username, "wrong-password").status_code == 401

    manager_login = login(local_client, manager.username, "Manager-Password-2026!")
    manager_headers = {"Authorization": f"Bearer {manager_login.json()['access_token']}"}
    body = {
        "request_id": "api-auth-out-of-scope",
        "team": "product",
        "model": "gpt-4o",
        "input_tokens": 5,
        "output_tokens": 2,
    }
    assert local_client.post("/api/v1/requests", json=body, headers=manager_headers).status_code == 403
    assert local_client.get("/api/v1/reconciliation", headers=manager_headers).status_code == 403

    leader_login = login(local_client, leader.username, "Leader-Password-2026!")
    leader_headers = {"Authorization": f"Bearer {leader_login.json()['access_token']}"}
    body["request_id"] = "api-auth-team-leader-write-denied"
    body["team"] = "engineering"
    assert local_client.post("/api/v1/requests", json=body, headers=leader_headers).status_code == 403


def test_cors_preflight_does_not_authorize_followup_api_call(api_accounts, local_client):
    preflight = local_client.options(
        "/api/v1/summary",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.text == "OK"
    assert local_client.get("/api/v1/summary").status_code == 401


def test_login_remote_requires_the_existing_service_token(api_accounts, monkeypatch):
    _, _, manager, _ = api_accounts
    client = TestClient(api_main.app, client=("198.51.100.25", 54321))
    assert login(client, manager.username, "Manager-Password-2026!").status_code == 403

    service_token = "s" * 32
    monkeypatch.setenv("FINOPS_API_TOKEN", service_token)
    response = login(client, manager.username, "Manager-Password-2026!")
    assert response.status_code == 401
    response = client.post(
        "/auth/login",
        json={"username": manager.username, "password": "Manager-Password-2026!"},
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert response.status_code == 200


def test_public_auth_status_and_bootstrap_requires_one_time_secret(empty_api_store, local_client, monkeypatch):
    status = local_client.get("/auth/status")
    assert status.status_code == 200
    assert status.json()["is_bootstrapped"] is False
    assert "password" not in status.text.lower()

    body = {
        "username": "org.head",
        "display_name": "Organization Head",
        "password": "Head-Password-2026!",
        "bootstrap_token": "wrong-token",
    }
    assert local_client.post("/auth/bootstrap", json=body).status_code == 503

    setup_secret = "x" * 10
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", setup_secret)
    assert local_client.post("/auth/bootstrap", json=body).status_code == 503

    setup_secret = "x" * 11
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", setup_secret)
    assert local_client.post("/auth/bootstrap", json=body).status_code == 401
    body["bootstrap_token"] = setup_secret
    created = local_client.post("/auth/bootstrap", json=body)
    assert created.status_code == 201
    assert created.json()["user"]["role"] == "org_head"
    assert local_client.post("/auth/bootstrap", json=body).status_code == 403


def test_bootstrap_rate_limiter_blocks_repeated_invalid_tokens(empty_api_store, local_client, monkeypatch):
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "x" * 11)
    monkeypatch.setattr(api_main, "login_rate_limiter", LoginRateLimiter(2, 300, 60))
    body = {
        "username": "org.head",
        "display_name": "Organization Head",
        "password": "Head-Password-2026!",
        "bootstrap_token": "incorrect",
    }

    assert local_client.post("/auth/bootstrap", json=body).status_code == 401
    limited = local_client.post("/auth/bootstrap", json=body)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    body["bootstrap_token"] = "x" * 11
    assert local_client.post("/auth/bootstrap", json=body).status_code == 429


def test_login_rate_limiter_blocks_repeated_failures(api_accounts, local_client, monkeypatch):
    _, _, manager, _ = api_accounts
    monkeypatch.setattr(api_main, "login_rate_limiter", LoginRateLimiter(2, 300, 60))
    assert login(local_client, manager.username, "incorrect-password").status_code == 401
    limited = login(local_client, manager.username, "incorrect-password")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert login(local_client, manager.username, "Manager-Password-2026!").status_code == 429
