"""Frontend Authentication Client for TokenLens.

Provides a clean, reusable client for interacting with the TokenLens Frontend Login API
from any frontend application (Streamlit, CLI, automated tests, or external services).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import urllib.parse
import urllib.request
import json


@dataclass(frozen=True)
class UserProfile:
    username: str
    display_name: str
    role: str
    role_label: str
    teams: tuple[str, ...]
    active: bool = True
    auth_version: str = ""


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    token_type: str
    expires_in: int
    expires_at: str
    user: UserProfile


class AuthClientError(Exception):
    """Raised when an authentication request fails."""

    def __init__(self, message: str, status_code: int = 400, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class TokenLensAuthClient:
    """Client for TokenLens Frontend Authentication API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")
        self._current_session: AuthSession | None = None

    @property
    def is_authenticated(self) -> bool:
        return self._current_session is not None

    @property
    def token(self) -> str | None:
        return self._current_session.access_token if self._current_session else None

    @property
    def user(self) -> UserProfile | None:
        return self._current_session.user if self._current_session else None

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        if self._current_session and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {self._current_session.access_token}"

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                return status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                err_json = json.loads(body)
            except Exception:
                err_json = {"detail": body or exc.reason}
            raise AuthClientError(
                err_json.get("detail", str(exc)),
                status_code=exc.code,
                details=err_json,
            ) from exc

    def get_status(self) -> dict[str, Any]:
        """Check system auth and bootstrap status."""
        _, data = self._request("GET", "/api/v1/auth/status")
        return data

    def bootstrap(
        self,
        username: str,
        display_name: str,
        password: str,
        bootstrap_token: str | None = None,
    ) -> dict[str, Any]:
        """Bootstrap initial organization head account when user_count == 0."""
        payload = {
            "username": username,
            "display_name": display_name,
            "password": password,
        }
        if bootstrap_token:
            payload["bootstrap_token"] = bootstrap_token
        _, data = self._request("POST", "/api/v1/auth/bootstrap", payload=payload)
        return data

    def login(self, username: str, password: str) -> AuthSession:
        """Authenticate user credentials and establish a secure bearer session."""
        _, data = self._request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
        )
        user_data = data.get("user", {})
        user_profile = UserProfile(
            username=user_data.get("username", data.get("role", "")),
            display_name=user_data.get("display_name", username),
            role=user_data.get("role", data.get("role", "")),
            role_label=user_data.get("role_label", ""),
            teams=tuple(user_data.get("teams", data.get("teams", []))),
        )
        self._current_session = AuthSession(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            expires_in=data.get("expires_in", 3600),
            expires_at=data.get("expires_at", ""),
            user=user_profile,
        )
        return self._current_session

    def get_me(self) -> UserProfile:
        """Introspect current authenticated bearer session."""
        if not self._current_session:
            raise AuthClientError("No active session", status_code=401)
        _, data = self._request("GET", "/api/v1/auth/me")
        return UserProfile(
            username=data["username"],
            display_name=data["display_name"],
            role=data["role"],
            role_label=data.get("role_label", ""),
            teams=tuple(data.get("teams", [])),
            active=data.get("active", True),
            auth_version=data.get("auth_version", ""),
        )

    def logout(self) -> bool:
        """Revoke current bearer session."""
        if not self._current_session:
            return True
        try:
            self._request("POST", "/api/v1/auth/logout")
        except AuthClientError:
            pass
        finally:
            self._current_session = None
        return True
