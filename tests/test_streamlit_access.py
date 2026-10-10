"""Streamlit smoke tests for first-run gating and role-specific navigation."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.access_control import AccessControlStore, Role

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_first_run_shows_bootstrap_form_without_rendering_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENLENS_ACCESS_DB", str(tmp_path / "access.sqlite3"))
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "b" * 11)

    app = AppTest.from_file(APP_PATH, default_timeout=30).run(timeout=30)

    assert [title.value for title in app.title] == ["TokenLens access"]
    assert any(button.label == "Create organization head" for button in app.button)
    assert not any("Know where every token goes." in item.value for item in app.markdown)
    assert not app.exception


def test_manager_login_omits_head_settings_navigation(tmp_path, monkeypatch):
    access_path = tmp_path / "access.sqlite3"
    store = AccessControlStore(access_path)
    head = store.bootstrap_org_head("org.head", "Organization Head", "Head-Password-2026!")
    store.create_user(
        head.username,
        "manager.eng",
        "Engineering Manager",
        "Manager-Password-2026!",
        Role.MANAGER,
        ["engineering"],
    )
    monkeypatch.setenv("TOKENLENS_ACCESS_DB", str(access_path))
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "b" * 11)

    app = AppTest.from_file(APP_PATH, default_timeout=30).run(timeout=30)
    app.text_input[0].set_value("manager.eng")
    app.text_input[1].set_value("Manager-Password-2026!")
    app.button[0].click()
    app.run(timeout=30)

    assert app.session_state.get("auth_username") == "manager.eng"
    assert "Settings" not in app.get("segmented_control")[0].options
    header_buttons = [button.label for button in app.button]
    assert header_buttons.index("Validation Report") + 1 == header_buttons.index("Sign out")
    app.button[header_buttons.index("Validation Report")].click()
    app.run(timeout=30)
    assert any("restricted to the organization head" in item.value for item in app.warning)
    assert not any("LLM FinOps Validation Report" in item.value for item in app.markdown)
    assert not app.exception


def test_head_can_open_and_close_validation_report_popup(tmp_path, monkeypatch):
    access_path = tmp_path / "access.sqlite3"
    AccessControlStore(access_path).bootstrap_org_head(
        "org.head", "Organization Head", "Head-Password-2026!"
    )
    monkeypatch.setenv("TOKENLENS_ACCESS_DB", str(access_path))
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "b" * 11)

    app = AppTest.from_file(APP_PATH, default_timeout=30).run(timeout=30)
    app.text_input[0].set_value("org.head")
    app.text_input[1].set_value("Head-Password-2026!")
    app.button[0].click()
    app.run(timeout=30)

    header_buttons = [button.label for button in app.button]
    report_index = header_buttons.index("Validation Report")
    assert report_index + 1 == header_buttons.index("Sign out")
    app.button[report_index].click()
    app.run(timeout=30)

    assert any("Overall status: PASS WITH WARNINGS" in item.value for item in app.warning)
    assert any("Audit Timestamp" in item.value for item in app.markdown)
    assert len(app.get("download_button")) == 1
    assert any(button.label == "Close" for button in app.button)
    close_index = [button.label for button in app.button].index("Close")
    app.button[close_index].click()
    app.run(timeout=30)
    assert any("Know where every token goes." in item.value for item in app.markdown)
    assert "Validation Report" in [button.label for button in app.button]
    assert not app.exception


def test_bootstrap_token_can_login_org_head(tmp_path, monkeypatch):
    access_path = tmp_path / "access.sqlite3"
    AccessControlStore(access_path).bootstrap_org_head(
        "org.head", "Organization Head", "Head-Password-2026!"
    )
    monkeypatch.setenv("TOKENLENS_ACCESS_DB", str(access_path))
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "ZSyVW6mqvgw")

    app = AppTest.from_file(APP_PATH, default_timeout=30).run(timeout=30)
    app.text_input[0].set_value("org.head")
    app.text_input[1].set_value("ZSyVW6mqvgw")
    app.button[0].click()
    app.run(timeout=30)

    assert app.session_state.get("auth_username") == "org.head"
    assert not app.exception
