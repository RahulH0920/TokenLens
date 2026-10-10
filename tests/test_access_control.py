"""Tests for authentication, scope grants, and delegated-task authorization."""

import sqlite3

import duckdb
import pytest

from core.access_control import (
    AccessControlStore,
    Role,
    can_access_team,
    can_assign_team_leader,
    can_manage_team,
    can_revoke_task,
    can_update_task_status,
    can_view_task,
)
from core.database import DuckDBAnalytics


@pytest.fixture
def access_store(tmp_path):
    return AccessControlStore(tmp_path / "access.sqlite3")


def create_head(access_store):
    return access_store.bootstrap_org_head(
        "org.head", "Organization Head", "Head-Password-2026!",
    )


def create_scoped_accounts(access_store):
    head = create_head(access_store)
    manager = access_store.create_user(
        head.username, "manager.eng", "Engineering Manager", "Manager-Password-2026!",
        Role.MANAGER, ["Engineering"],
    )
    leader = access_store.create_user(
        head.username, "leader.eng", "Engineering Lead", "Leader-Password-2026!",
        Role.TEAM_LEADER, ["engineering"],
    )
    other_manager = access_store.create_user(
        head.username, "manager.prod", "Product Manager", "Product-Password-2026!",
        Role.MANAGER, ["product"],
    )
    return head, manager, leader, other_manager


def test_first_account_is_an_org_head_and_bootstrap_is_one_time(access_store):
    principal = create_head(access_store)
    assert principal.role == Role.ORG_HEAD
    assert principal.teams == ()
    assert access_store.user_count() == 1
    with pytest.raises(PermissionError, match="already been completed"):
        access_store.bootstrap_org_head("second.head", "Second Head", "Another-Password-2026!")


def test_password_is_salted_and_only_hash_is_stored(access_store):
    create_head(access_store)
    with sqlite3.connect(access_store.path) as connection:
        salt, stored_hash = connection.execute(
            "SELECT password_salt, password_hash FROM access_users WHERE username = ?",
            ("org.head",),
        ).fetchone()
    assert salt
    assert stored_hash != "Head-Password-2026!"
    assert access_store.authenticate("org.head", "Head-Password-2026!").role == Role.ORG_HEAD
    assert access_store.authenticate("org.head", "incorrect-password") is None
    assert access_store.authenticate("unknown.user", "incorrect-password") is None


def test_only_org_head_can_create_users_or_change_grants(access_store):
    _, manager, _, _ = create_scoped_accounts(access_store)
    with pytest.raises(PermissionError, match="organization head"):
        access_store.create_user(
            manager.username, "other.lead", "Other Lead", "Another-Password-2026!",
            Role.TEAM_LEADER, ["engineering"],
        )
    with pytest.raises(PermissionError, match="organization head"):
        access_store.list_users(manager.username)


def test_managers_and_team_leaders_receive_only_granted_teams(access_store):
    head, manager, leader, other_manager = create_scoped_accounts(access_store)
    assert can_access_team(head, "research")
    assert can_manage_team(manager, "ENGINEERING")
    assert not can_access_team(manager, "product")
    assert can_access_team(leader, "engineering")
    assert not can_manage_team(leader, "engineering")
    assert not can_access_team(other_manager, "engineering")


def test_team_leader_assignment_requires_same_granted_team(access_store):
    _, manager, leader, other_manager = create_scoped_accounts(access_store)
    assert can_assign_team_leader(manager, "engineering", leader)
    assert not can_assign_team_leader(manager, "product", leader)
    assert not can_assign_team_leader(other_manager, "engineering", leader)


def test_task_visibility_status_and_revocation_follow_role_scope(access_store):
    head, manager, leader, other_manager = create_scoped_accounts(access_store)
    task = {"department": "engineering", "team_leader": "leader.eng"}
    foreign_task = {"department": "product", "team_leader": "leader.eng"}

    assert can_view_task(head, foreign_task)
    assert can_view_task(manager, task)
    assert not can_view_task(manager, foreign_task)
    assert can_view_task(leader, task)
    assert not can_view_task(leader, foreign_task)
    assert not can_view_task(other_manager, task)

    assert can_update_task_status(manager, task)
    assert can_update_task_status(leader, task)
    assert not can_update_task_status(other_manager, task)
    assert can_revoke_task(head, foreign_task)
    assert can_revoke_task(manager, task)
    assert not can_revoke_task(leader, task)
    assert not can_revoke_task(other_manager, task)


def test_manager_can_only_assign_registered_leaders_in_scope(access_store):
    _, manager, leader, _ = create_scoped_accounts(access_store)
    assert [user.username for user in access_store.list_team_leaders(manager, "engineering")] == [leader.username]
    with pytest.raises(PermissionError, match="not authorized"):
        access_store.list_team_leaders(manager, "product")


def test_last_active_org_head_cannot_be_demoted_or_disabled(access_store):
    head = create_head(access_store)
    with pytest.raises(ValueError, match="last active organization head"):
        access_store.update_user_access(head.username, head.username, Role.MANAGER, ["engineering"], True)
    with pytest.raises(ValueError, match="last active organization head"):
        access_store.update_user_access(head.username, head.username, Role.ORG_HEAD, [], False)
    assert access_store.get_user(head.username).role == Role.ORG_HEAD


def test_account_deactivation_and_grant_changes_invalidate_old_session(access_store):
    head, manager, _, _ = create_scoped_accounts(access_store)
    old_version = manager.auth_version
    updated = access_store.update_user_access(
        head.username, manager.username, Role.MANAGER, ["engineering", "research"], True,
    )
    assert updated.auth_version != old_version
    assert access_store.get_user(manager.username).teams == ("engineering", "research")

    access_store.update_user_access(head.username, manager.username, Role.MANAGER, ["engineering"], False)
    assert access_store.get_user(manager.username) is None
    assert access_store.authenticate(manager.username, "Manager-Password-2026!") is None


def test_api_session_is_hashed_revocable_and_invalidated_by_grant_change(access_store):
    head, manager, _, _ = create_scoped_accounts(access_store)
    raw_token, expires_at = access_store.issue_session(manager.username, ttl_seconds=600)
    assert expires_at.tzinfo is not None
    assert access_store.authenticate_session(raw_token) == manager

    with sqlite3.connect(access_store.path) as connection:
        (stored_hash,) = connection.execute(
            "SELECT token_hash FROM access_sessions WHERE username = ?", (manager.username,)
        ).fetchone()
    assert stored_hash != raw_token

    access_store.update_user_access(head.username, manager.username, Role.MANAGER, ["engineering"], True)
    assert access_store.authenticate_session(raw_token) is None

    manager = access_store.get_user(manager.username)
    renewed_token, _ = access_store.issue_session(manager.username, ttl_seconds=600)
    assert access_store.authenticate_session(renewed_token) == manager
    access_store.revoke_session(renewed_token)
    assert access_store.authenticate_session(renewed_token) is None


def test_api_session_rejects_expired_and_malformed_tokens(access_store):
    head = create_head(access_store)
    raw_token, _ = access_store.issue_session(head.username, ttl_seconds=600)
    with sqlite3.connect(access_store.path) as connection:
        connection.execute(
            "UPDATE access_sessions SET expires_at = ? WHERE username = ?",
            ("2000-01-01T00:00:00+00:00", head.username),
        )
    assert access_store.authenticate_session(raw_token) is None
    assert access_store.authenticate_session("short") is None


def test_non_head_accounts_require_team_scopes_and_head_does_not_accept_them(access_store):
    head = create_head(access_store)
    with pytest.raises(ValueError, match="at least one team"):
        access_store.create_user(
            head.username, "manager.none", "No Scope", "Scoped-Password-2026!", Role.MANAGER, [],
        )
    with pytest.raises(ValueError, match="organization-wide access"):
        access_store.create_user(
            head.username, "other.head", "Other Head", "Scoped-Password-2026!", Role.ORG_HEAD, ["engineering"],
        )


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_weak_passwords_are_rejected_during_account_creation(access_store, password):
    head = create_head(access_store)
    with pytest.raises(ValueError, match="Password must be"):
        access_store.create_user(
            head.username, "weak.pass", "Weak Password", password, Role.MANAGER, ["engineering"],
        )


def test_task_records_keep_creator_and_filter_by_assigned_team_leader():
    db = DuckDBAnalytics(in_memory=True)
    try:
        db.create_delegated_task(
            task_id="TASK-RBAC-1",
            task_name="Engineering review",
            department="engineering",
            team_leader="leader.eng",
            model="gpt-4o",
            allocated_input_tokens=1000,
            allocated_output_tokens=500,
            allocated_budget_usd=1.25,
            manager_name="Engineering Manager",
            manager_username="manager.eng",
        )
        visible = db.get_delegated_tasks_df(team_leader="leader.eng")
        assert visible["task_id"].tolist() == ["TASK-RBAC-1"]
        assert db.get_delegated_tasks_df(team_leader="leader.other").empty
        assert db.get_delegated_task("TASK-RBAC-1")["manager_username"] == "manager.eng"
        with pytest.raises(duckdb.ConstraintException):
            db.create_delegated_task(
                task_id="TASK-RBAC-1",
                task_name="Attempted replacement",
                department="engineering",
                team_leader="leader.eng",
                model="gpt-4o",
                allocated_input_tokens=1,
                allocated_output_tokens=1,
                allocated_budget_usd=0.01,
            )
        assert db.get_delegated_task("TASK-RBAC-1")["task_name"] == "Engineering review"
    finally:
        db.close()


def test_task_table_migrates_legacy_rows_with_restricted_legacy_owner(tmp_path):
    path = tmp_path / "existing.duckdb"
    legacy = duckdb.connect(str(path))
    legacy.execute(
        """
        CREATE TABLE delegated_tasks (
            task_id VARCHAR PRIMARY KEY,
            task_name VARCHAR NOT NULL,
            department VARCHAR NOT NULL,
            team_leader VARCHAR NOT NULL,
            manager_name VARCHAR NOT NULL,
            model VARCHAR NOT NULL,
            allocated_input_tokens BIGINT NOT NULL,
            allocated_output_tokens BIGINT NOT NULL,
            allocated_budget_usd DECIMAL(18, 6) NOT NULL,
            consumed_tokens BIGINT NOT NULL DEFAULT 0,
            consumed_spend_usd DECIMAL(18, 6) NOT NULL DEFAULT 0.0,
            priority VARCHAR NOT NULL DEFAULT 'Medium',
            status VARCHAR NOT NULL DEFAULT 'Assigned',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO delegated_tasks (
            task_id, task_name, department, team_leader, manager_name, model,
            allocated_input_tokens, allocated_output_tokens, allocated_budget_usd
        ) VALUES ('legacy-task', 'Existing Task', 'engineering', 'leader.eng', 'Old Manager', 'gpt-4o', 1, 1, 0.01)
        """
    )
    legacy.close()
    migrated = DuckDBAnalytics(path)
    try:
        columns = {
            row[1]
            for row in migrated.conn.execute("PRAGMA table_info('delegated_tasks')").fetchall()
        }
        assert "manager_username" in columns
        assert migrated.get_delegated_task("legacy-task")["manager_username"] == "legacy"
    finally:
        migrated.close()
