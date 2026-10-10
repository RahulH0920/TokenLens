"""Local role-based access control for the Streamlit TokenLens instance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import unicodedata
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping


class Role(str, Enum):
    ORG_HEAD = "org_head"
    MANAGER = "manager"
    TEAM_LEADER = "team_leader"


ROLE_LABELS = {
    Role.ORG_HEAD: "Organization Head",
    Role.MANAGER: "Manager",
    Role.TEAM_LEADER: "Team Leader",
}

_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}\Z")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 5
_SCRYPT_MAXMEM = 128 * 1024 * 1024
_DUMMY_SALT = b"TokenLens-dummy-salt"


@dataclass(frozen=True)
class Principal:
    username: str
    display_name: str
    role: Role
    teams: tuple[str, ...]
    active: bool = True
    auth_version: str = ""


class AccessControlStore:
    """SQLite identity store kept separate from the analytics and usage ledgers."""

    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS access_users (
                        username TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('org_head', 'manager', 'team_leader')),
                        password_salt TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        teams_json TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS access_audit (
                        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        actor_username TEXT NOT NULL,
                        action TEXT NOT NULL,
                        target_username TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS access_sessions (
                        token_hash TEXT PRIMARY KEY,
                        username TEXT NOT NULL REFERENCES access_users(username),
                        auth_version TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        revoked_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_access_audit_created
                        ON access_audit(created_at);
                    CREATE INDEX IF NOT EXISTS idx_access_sessions_user
                        ON access_sessions(username, expires_at);
                    """
                )
            finally:
                connection.close()

    @staticmethod
    def normalize_username(username: str) -> str:
        if not isinstance(username, str):
            raise ValueError("Username must be text.")
        normalized = username.strip().casefold()
        if not _USERNAME.fullmatch(normalized):
            raise ValueError("Username must be 3-64 characters using letters, numbers, '.', '_' or '-'.")
        return normalized

    @staticmethod
    def normalize_team(team: str) -> str:
        if not isinstance(team, str):
            raise ValueError("Team names must be text.")
        normalized = team.strip().casefold()
        if not normalized or len(normalized) > 100 or any(unicodedata.category(char) == "Cc" for char in normalized):
            raise ValueError("Team name is invalid.")
        return normalized

    @classmethod
    def normalize_teams(cls, teams: Iterable[str], role: Role) -> tuple[str, ...]:
        normalized = tuple(sorted({cls.normalize_team(team) for team in teams}))
        if role == Role.ORG_HEAD and normalized:
            raise ValueError("Organization heads receive organization-wide access and do not have team scopes.")
        if role != Role.ORG_HEAD and not normalized:
            raise ValueError("Managers and team leaders must be granted at least one team.")
        return normalized

    @staticmethod
    def validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
            raise ValueError("Password must be between 12 and 1024 characters.")
        if any(unicodedata.category(char) == "Cc" for char in password):
            raise ValueError("Password must not contain control characters.")

    @classmethod
    def _hash_password(cls, password: str, salt: bytes | None = None) -> tuple[str, str]:
        cls.validate_password(password)
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
            maxmem=_SCRYPT_MAXMEM,
        )
        return base64.urlsafe_b64encode(salt).decode("ascii"), base64.urlsafe_b64encode(digest).decode("ascii")

    @classmethod
    def _verify_password(cls, password: str, salt_text: str, expected_hash: str) -> bool:
        try:
            salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=_SCRYPT_N,
                r=_SCRYPT_R,
                p=_SCRYPT_P,
                dklen=32,
                maxmem=_SCRYPT_MAXMEM,
            )
            actual = base64.urlsafe_b64encode(digest).decode("ascii")
        except (ValueError, TypeError, UnicodeError):
            return False
        return hmac.compare_digest(actual, expected_hash)

    @staticmethod
    def _validate_display_name(display_name: str) -> str:
        if not isinstance(display_name, str):
            raise ValueError("Display name must be text.")
        display_name = display_name.strip()
        if not 2 <= len(display_name) <= 100 or any(unicodedata.category(char) == "Cc" for char in display_name):
            raise ValueError("Display name must be 2-100 characters without control characters.")
        return display_name

    @classmethod
    def _principal(cls, row: sqlite3.Row) -> Principal:
        try:
            teams = tuple(cls.normalize_team(team) for team in json.loads(row["teams_json"]))
            role = Role(row["role"])
        except (ValueError, TypeError, json.JSONDecodeError):
            # Malformed persisted authorization data must never grant access.
            teams = ()
            role = None
        if role is None:
            raise ValueError("Stored account access policy is invalid.")
        return Principal(
            username=row["username"],
            display_name=row["display_name"],
            role=role,
            teams=teams,
            active=bool(row["active"]),
            auth_version=row["updated_at"],
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        actor_username: str,
        action: str,
        target_username: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO access_audit (actor_username, action, target_username, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                actor_username,
                action,
                target_username,
                json.dumps(dict(details), sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    @staticmethod
    def _insert_user(
        connection: sqlite3.Connection,
        username: str,
        display_name: str,
        role: Role,
        teams: tuple[str, ...],
        password_salt: str,
        password_hash: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO access_users (
                username, display_name, role, password_salt, password_hash,
                teams_json, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (username, display_name, role.value, password_salt, password_hash, json.dumps(teams), now, now),
        )

    def user_count(self) -> int:
        with self._lock:
            connection = self._connect()
            try:
                return int(connection.execute("SELECT COUNT(*) FROM access_users").fetchone()[0])
            finally:
                connection.close()

    def bootstrap_org_head(self, username: str, display_name: str, password: str) -> Principal:
        """Atomically create the first organization head; all later signup is closed."""
        username = self.normalize_username(username)
        display_name = self._validate_display_name(display_name)
        password_salt, password_hash = self._hash_password(password)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT COUNT(*) FROM access_users").fetchone()[0] != 0:
                    raise PermissionError("Initial organization-head setup has already been completed.")
                self._insert_user(connection, username, display_name, Role.ORG_HEAD, (), password_salt, password_hash)
                self._audit(connection, "bootstrap", "bootstrap_org_head", username, {"role": Role.ORG_HEAD.value})
                row = connection.execute("SELECT * FROM access_users WHERE username = ?", (username,)).fetchone()
                connection.commit()
                return self._principal(row)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def authenticate(self, username: str, password: str) -> Principal | None:
        """Authenticate without revealing whether an account exists or is disabled."""
        if not isinstance(password, str) or len(password) > 1024:
            password = "invalid credential"
        try:
            normalized = self.normalize_username(username)
        except ValueError:
            normalized = ""
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT * FROM access_users WHERE username = ?", (normalized,)).fetchone()
            finally:
                connection.close()
        if row is None:
            self._hash_password("dummy-password-value", _DUMMY_SALT)
            return None
        if not self._verify_password(password, row["password_salt"], row["password_hash"]):
            return None
        principal = self._principal(row)
        return principal if principal.active else None

    def issue_session(self, username: str, ttl_seconds: int = 3600) -> tuple[str, datetime]:
        """Issue a short-lived opaque API credential; only its SHA-256 digest is stored."""
        username = self.normalize_username(username)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 86400:
            raise ValueError("Session lifetime must be between 60 and 86400 seconds.")
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        created_at = datetime.now(timezone.utc)
        expires_at = created_at.timestamp() + ttl_seconds
        expiry = datetime.fromtimestamp(expires_at, timezone.utc)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM access_users WHERE username = ? AND active = 1", (username,)
                ).fetchone()
                if row is None:
                    raise PermissionError("An active account is required to create a session.")
                connection.execute(
                    "INSERT INTO access_sessions (token_hash, username, auth_version, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (token_hash, username, row["updated_at"], created_at.isoformat(), expiry.isoformat()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return raw_token, expiry

    def authenticate_session(self, raw_token: str) -> Principal | None:
        """Resolve a bearer credential, rejecting expired, revoked, or stale sessions."""
        if not isinstance(raw_token, str) or not 32 <= len(raw_token) <= 256:
            return None
        try:
            token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        except UnicodeError:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT s.auth_version AS session_auth_version, s.expires_at,
                           s.revoked_at, u.*
                    FROM access_sessions AS s
                    JOIN access_users AS u ON u.username = s.username
                    WHERE s.token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
            finally:
                connection.close()
        if row is None or row["revoked_at"] is not None or not bool(row["active"]):
            return None
        try:
            expiry = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now or row["session_auth_version"] != row["updated_at"]:
            return None
        return self._principal(row)

    def revoke_session(self, raw_token: str) -> None:
        """Revoke an opaque bearer credential without storing or logging its value."""
        if not isinstance(raw_token, str) or not 32 <= len(raw_token) <= 256:
            return
        try:
            token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        except UnicodeError:
            return
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "UPDATE access_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), token_hash),
                )
            finally:
                connection.close()

    def get_user(self, username: str, *, include_inactive: bool = False) -> Principal | None:
        try:
            normalized = self.normalize_username(username)
        except ValueError:
            return None
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT * FROM access_users WHERE username = ?", (normalized,)).fetchone()
            finally:
                connection.close()
        if row is None or (not include_inactive and not bool(row["active"])):
            return None
        return self._principal(row)

    def _active_org_head(self, connection: sqlite3.Connection, actor_username: str) -> sqlite3.Row:
        username = self.normalize_username(actor_username)
        actor = connection.execute("SELECT * FROM access_users WHERE username = ?", (username,)).fetchone()
        if actor is None or not actor["active"] or actor["role"] != Role.ORG_HEAD.value:
            raise PermissionError("Only an active organization head can manage accounts and grants.")
        return actor

    def create_user(
        self,
        actor_username: str,
        username: str,
        display_name: str,
        password: str,
        role: Role | str,
        teams: Iterable[str] = (),
    ) -> Principal:
        username = self.normalize_username(username)
        display_name = self._validate_display_name(display_name)
        role = Role(role)
        normalized_teams = self.normalize_teams(teams, role)
        password_salt, password_hash = self._hash_password(password)
        actor_username = self.normalize_username(actor_username)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._active_org_head(connection, actor_username)
                self._insert_user(connection, username, display_name, role, normalized_teams, password_salt, password_hash)
                self._audit(connection, actor_username, "create_user", username, {"role": role.value, "teams": normalized_teams})
                row = connection.execute("SELECT * FROM access_users WHERE username = ?", (username,)).fetchone()
                connection.commit()
                return self._principal(row)
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ValueError("That username already exists.") from exc
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def update_user_access(
        self,
        actor_username: str,
        username: str,
        role: Role | str,
        teams: Iterable[str],
        active: bool,
    ) -> Principal:
        if not isinstance(active, bool):
            raise ValueError("Account active state must be boolean.")
        actor_username = self.normalize_username(actor_username)
        username = self.normalize_username(username)
        role = Role(role)
        normalized_teams = self.normalize_teams(teams, role)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._active_org_head(connection, actor_username)
                target = connection.execute("SELECT * FROM access_users WHERE username = ?", (username,)).fetchone()
                if target is None:
                    raise ValueError("Account does not exist.")
                removes_head = target["role"] == Role.ORG_HEAD.value and (role != Role.ORG_HEAD or not active)
                if removes_head:
                    active_heads = connection.execute(
                        "SELECT COUNT(*) FROM access_users WHERE role = ? AND active = 1", (Role.ORG_HEAD.value,)
                    ).fetchone()[0]
                    if active_heads <= 1:
                        raise ValueError("The last active organization head cannot be demoted or disabled.")
                connection.execute(
                    "UPDATE access_users SET role = ?, teams_json = ?, active = ?, updated_at = ? WHERE username = ?",
                    (role.value, json.dumps(normalized_teams), int(bool(active)), datetime.now(timezone.utc).isoformat(), username),
                )
                self._audit(
                    connection,
                    actor_username,
                    "update_user_access",
                    username,
                    {"role": role.value, "teams": normalized_teams, "active": bool(active)},
                )
                row = connection.execute("SELECT * FROM access_users WHERE username = ?", (username,)).fetchone()
                connection.commit()
                return self._principal(row)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def reset_password(self, actor_username: str, username: str, new_password: str) -> None:
        actor_username = self.normalize_username(actor_username)
        username = self.normalize_username(username)
        password_salt, password_hash = self._hash_password(new_password)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._active_org_head(connection, actor_username)
                if connection.execute("SELECT 1 FROM access_users WHERE username = ?", (username,)).fetchone() is None:
                    raise ValueError("Account does not exist.")
                connection.execute(
                    "UPDATE access_users SET password_salt = ?, password_hash = ?, updated_at = ? WHERE username = ?",
                    (password_salt, password_hash, datetime.now(timezone.utc).isoformat(), username),
                )
                self._audit(connection, actor_username, "reset_password", username, {})
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def list_users(self, actor_username: str) -> list[Principal]:
        actor_username = self.normalize_username(actor_username)
        with self._lock:
            connection = self._connect()
            try:
                self._active_org_head(connection, actor_username)
                rows = connection.execute("SELECT * FROM access_users ORDER BY username").fetchall()
            finally:
                connection.close()
        return [self._principal(row) for row in rows]

    def list_team_leaders(self, actor: Principal, team: str) -> list[Principal]:
        current_actor = self.get_user(actor.username)
        if current_actor is None or current_actor.auth_version != actor.auth_version or not can_manage_team(current_actor, team):
            raise PermissionError("You are not authorized to assign work in this team.")
        team = self.normalize_team(team)
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT * FROM access_users WHERE role = ? AND active = 1 ORDER BY display_name",
                    (Role.TEAM_LEADER.value,),
                ).fetchall()
            finally:
                connection.close()
        return [
            principal for principal in (self._principal(row) for row in rows)
            if team in principal.teams
        ]

    def audit_events(self, actor_username: str, limit: int = 100) -> list[dict[str, Any]]:
        actor_username = self.normalize_username(actor_username)
        limit = max(1, min(int(limit), 500))
        with self._lock:
            connection = self._connect()
            try:
                self._active_org_head(connection, actor_username)
                rows = connection.execute(
                    "SELECT * FROM access_audit ORDER BY audit_id DESC LIMIT ?", (limit,)
                ).fetchall()
            finally:
                connection.close()
        return [
            {
                "actor": row["actor_username"],
                "action": row["action"],
                "target": row["target_username"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def record_event(
        self,
        actor_username: str,
        action: str,
        target_username: str,
        details: Mapping[str, Any],
    ) -> None:
        """Append a task-related audit event for an authenticated account."""
        actor_username = self.normalize_username(actor_username)
        target_username = str(target_username)[:200]
        if not action or len(action) > 80:
            raise ValueError("Audit action is invalid.")
        with self._lock:
            connection = self._connect()
            try:
                actor = connection.execute(
                    "SELECT active FROM access_users WHERE username = ?", (actor_username,)
                ).fetchone()
                if actor is None or not actor["active"]:
                    raise PermissionError("An active account is required to record this action.")
                self._audit(connection, actor_username, action, target_username, details)
            finally:
                connection.close()

    def record_auth_audit(
        self,
        actor_username: str,
        action: str,
        target_username: str,
        details: Mapping[str, Any],
    ) -> None:
        """Record an authentication audit event without requiring prior user existence."""
        try:
            actor = self.normalize_username(actor_username) if actor_username else "anonymous"
        except ValueError:
            actor = str(actor_username)[:64] or "anonymous"
        target = str(target_username)[:200]
        action = str(action)[:80]
        with self._lock:
            connection = self._connect()
            try:
                self._audit(connection, actor, action, target, details)
            finally:
                connection.close()


def can_access_team(principal: Principal, team: str) -> bool:
    if not principal.active:
        return False
    if principal.role == Role.ORG_HEAD:
        return True
    try:
        return AccessControlStore.normalize_team(team) in principal.teams
    except ValueError:
        return False


def can_manage_team(principal: Principal, team: str) -> bool:
    return principal.role in {Role.ORG_HEAD, Role.MANAGER} and can_access_team(principal, team)


def can_assign_team_leader(principal: Principal, team: str, leader: Principal) -> bool:
    return (
        can_manage_team(principal, team)
        and leader.active
        and leader.role == Role.TEAM_LEADER
        and can_access_team(leader, team)
    )


def can_view_task(principal: Principal, task: Mapping[str, Any]) -> bool:
    if not principal.active:
        return False
    if principal.role == Role.ORG_HEAD:
        return True
    if principal.role == Role.MANAGER:
        return can_access_team(principal, str(task.get("department", "")))
    if principal.role == Role.TEAM_LEADER:
        return (
            can_access_team(principal, str(task.get("department", "")))
            and str(task.get("team_leader", "")).strip().casefold() == principal.username
        )
    return False


def can_update_task_status(principal: Principal, task: Mapping[str, Any]) -> bool:
    return principal.role in {Role.ORG_HEAD, Role.MANAGER, Role.TEAM_LEADER} and can_view_task(principal, task)


def can_revoke_task(principal: Principal, task: Mapping[str, Any]) -> bool:
    return principal.role in {Role.ORG_HEAD, Role.MANAGER} and can_view_task(principal, task)
