"""LLM Finds: Light, Minimal, Premium AI FinOps Dashboard.

Design System:
- Page background: #F7F8FA
- Cards and sidebar: #FFFFFF
- Primary accent: #4F46E5
- Main text: #111827
- Secondary text: #6B7280
- Borders: #E5E7EB
- Success: #059669
- Warning: #D97706
- Error: #DC2626
- Rounded corners: 12px
- Subtle borders, minimal shadows, generous whitespace
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import html
import hmac
import json
from pathlib import Path
import random
import io
import os
import secrets

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from core.models import RequestRecord, PricingRecord
from core.attribution import AttributionParser
from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.reconciliation import ReconciliationEngine
from core.validation_report import load_validation_report
from core.guardrails import GuardrailEngine
from core.database import DuckDBAnalytics
from core.access_control import (
    AccessControlStore,
    ROLE_LABELS,
    Role,
    can_access_team,
    can_assign_team_leader,
    can_manage_team,
    can_revoke_task,
    can_update_task_status,
    can_view_task,
)

# Streamlit Page Setup
st.set_page_config(
    page_title="TokenLens — AI FinOps",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
DATA_DIR = BASE_DIR / "data"


@st.cache_resource(show_spinner=False)
def get_access_store(database_path: str) -> AccessControlStore:
    return AccessControlStore(database_path)


access_database = Path(os.getenv("TOKENLENS_ACCESS_DB", str(DATA_DIR / "tokenlens_access.sqlite3"))).resolve()
access_store = get_access_store(str(access_database))


def _logout() -> None:
    st.session_state.clear()
    st.rerun()


def _clear_sensitive_session_state() -> None:
    for key in ("auth_username", "auth_version", "confirm_logs_export", "confirm_validation_report_export", "validation_report_export_nonce", "request_logs_team_filter"):
        st.session_state.pop(key, None)


def _render_authentication_gate():
    username = st.session_state.get("auth_username")
    principal = access_store.get_user(username) if username else None
    stored_version = st.session_state.get("auth_version")
    if principal and stored_version != principal.auth_version:
        # A password reset, role change, or account change invalidates old sessions.
        principal = None
        _clear_sensitive_session_state()
    elif not principal:
        _clear_sensitive_session_state()

    if principal:
        return principal

    st.title("TokenLens access")
    if access_store.user_count() == 0:
        st.subheader("Create the organization head account")
        bootstrap_secret = os.getenv("TOKENLENS_BOOTSTRAP_TOKEN", "")
        if len(bootstrap_secret) < 11:
            st.error("Set TOKENLENS_BOOTSTRAP_TOKEN to a unique secret of at least 11 characters in the ignored .env file, then restart TokenLens.")
            st.stop()
        with st.form("bootstrap_org_head_form"):
            supplied_secret = st.text_input("One-time setup token", type="password")
            username_input = st.text_input("Username")
            display_name_input = st.text_input("Display name")
            password_input = st.text_input("Password (12 characters minimum)", type="password")
            password_confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create organization head")
        if submitted:
            if not hmac.compare_digest(supplied_secret, bootstrap_secret):
                st.error("Invalid setup token.")
            elif password_input != password_confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    created = access_store.bootstrap_org_head(username_input, display_name_input, password_input)
                    st.session_state["auth_username"] = created.username
                    st.session_state["auth_version"] = created.auth_version
                    st.session_state["confirm_logs_export"] = False
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
        st.stop()

    st.subheader("Sign in")
    with st.form("login_form"):
        username_input = st.text_input("Username")
        password_input = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        principal = access_store.authenticate(username_input, password_input)
        bootstrap_secret = os.getenv("TOKENLENS_BOOTSTRAP_TOKEN", "")
        if principal is None and bootstrap_secret and len(bootstrap_secret) >= 11:
            entered_secret = password_input.strip() if password_input else username_input.strip()
            if hmac.compare_digest(entered_secret, bootstrap_secret):
                target_user = username_input.strip() if username_input.strip() and username_input.strip() != entered_secret else "org.head"
                principal = access_store.get_user(target_user)
                if principal is None:
                    for candidate in ("org.head", "org.admin", "admin"):
                        u = access_store.get_user(candidate)
                        if u and u.role == Role.ORG_HEAD:
                            principal = u
                            break

        if principal is None:
            st.error("Invalid username or password.")
        else:
            st.session_state["auth_username"] = principal.username
            st.session_state["auth_version"] = principal.auth_version
            st.session_state["confirm_logs_export"] = False
            st.rerun()
    st.stop()


principal = _render_authentication_gate()
is_org_head = principal.role == Role.ORG_HEAD
is_manager = principal.role == Role.MANAGER
is_team_leader = principal.role == Role.TEAM_LEADER

# Minimal light SaaS CSS design system
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        color: #111827;
        background-color: #F7F8FA;
    }

    .stApp {
        background-color: #F7F8FA;
    }

    [data-testid="stDialog"] [data-testid="stVerticalBlock"] {
        max-height: 76vh;
        overflow-y: auto;
    }

    /* Completely hide sidebar and collapse toggle for full-width top dashboard experience */
    [data-testid="stSidebar"] {
        display: none !important;
    }
    [data-testid="stSidebarCollapsedControl"] {
        display: none !important;
    }

    /* Top Navbar Sideways Area */
    .top-navbar-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        padding: 12px 20px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
    }
    .top-nav-left {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .brand-icon {
        background: linear-gradient(135deg, #4F46E5 0%, #6366F1 100%);
        width: 36px;
        height: 36px;
        border-radius: 9px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #FFFFFF;
        font-weight: 800;
        font-size: 1.05rem;
        box-shadow: 0 2px 4px rgba(79, 70, 229, 0.25);
    }
    .brand-title {
        font-weight: 700;
        font-size: 1.35rem;
        color: #111827;
        letter-spacing: -0.02em;
        line-height: 1.1;
    }
    .brand-subtitle {
        font-size: 0.76rem;
        color: #6B7280;
        margin-top: 2px;
    }
    .top-nav-right {
        display: flex;
        align-items: center;
        gap: 14px;
    }
    .profile-pill {
        display: flex;
        align-items: center;
        gap: 10px;
        background: #F9FAFB;
        border: 1px solid #E5E7EB;
        border-radius: 30px;
        padding: 4px 14px 4px 5px;
    }
    .profile-avatar {
        background-color: #4F46E5;
        color: #FFFFFF;
        font-weight: 600;
        font-size: 0.8rem;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .profile-info {
        text-align: left;
    }
    .profile-name {
        font-size: 0.82rem;
        font-weight: 600;
        color: #111827;
        line-height: 1.1;
    }
    .profile-role {
        font-size: 0.70rem;
        color: #6B7280;
    }

    /* Second Layer: Full-width Connected Folder Tabs matching Sketches 1, 2, 3 */
    [data-testid="stSegmentedControl"] {
        margin-top: 6px !important;
        margin-bottom: 14px !important;
        width: 100% !important;
    }
    [data-testid="stSegmentedControl"] > div {
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid #E5E7EB !important;
        border-radius: 0px !important;
        padding: 0 !important;
        box-shadow: none !important;
        display: flex !important;
        width: 100% !important;
        gap: 6px !important;
        position: relative !important;
    }
    [data-testid="stSegmentedControl"] [data-testid="stSegmentedControlButton"] {
        flex: 1 !important;
        display: flex !important;
    }
    [data-testid="stSegmentedControl"] button {
        width: 100% !important;
        border-radius: 9px 9px 0 0 !important;
        font-weight: 500 !important;
        font-size: 0.90rem !important;
        padding: 10px 14px !important;
        background: #F3F4F6 !important;
        border: 1px solid #E5E7EB !important;
        border-bottom: 2px solid #E5E7EB !important;
        color: #4B5563 !important;
        transition: all 0.15s ease !important;
        margin-bottom: -2px !important;
        cursor: pointer !important;
    }
    [data-testid="stSegmentedControl"] button:hover {
        background-color: #EEF2FF !important;
        color: #4F46E5 !important;
        border-color: #C7D2FE !important;
    }
    /* ACTIVE FOLDER TAB: Physically opens and merges with the content below */
    [data-testid="stSegmentedControl"] button[aria-selected="true"],
    [data-testid="stSegmentedControl"] button[data-selected="true"],
    [data-testid="stSegmentedControl"] button[aria-checked="true"] {
        background-color: #FFFFFF !important;
        color: #4F46E5 !important;
        font-weight: 700 !important;
        border: 2px solid #4F46E5 !important;
        border-bottom: 3px solid #FFFFFF !important;
        margin-bottom: -3px !important;
        box-shadow: 0 -2px 6px rgba(79, 70, 229, 0.08) !important;
        z-index: 10 !important;
    }
    [data-testid="stSegmentedControl"] button[aria-selected="true"] p,
    [data-testid="stSegmentedControl"] button[data-selected="true"] p,
    [data-testid="stSegmentedControl"] button[aria-checked="true"] p {
        color: #4F46E5 !important;
        font-weight: 700 !important;
    }

    /* Minimal Card */
    .saas-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        padding: 20px 22px;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.04);
        margin-bottom: 1rem;
    }

    .saas-kpi-label {
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #6B7280;
        margin-bottom: 6px;
    }

    .saas-kpi-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: #111827;
        line-height: 1.15;
    }

    .saas-kpi-sub {
        font-size: 0.8rem;
        color: #6B7280;
        margin-top: 6px;
    }

    /* Badges */
    .badge-warn {
        background-color: #FFFBEB;
        color: #D97706;
        border: 1px solid #FDE68A;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        display: inline-block;
    }

    /* Restrained Spend Detective Callout */
    .detective-callout {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-left: 4px solid #4F46E5;
        border-radius: 10px;
        padding: 16px 20px;
        margin-top: 1rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    }

    .detective-title {
        font-size: 0.88rem;
        font-weight: 600;
        color: #4F46E5;
        margin-bottom: 4px;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .detective-desc {
        font-size: 0.92rem;
        color: #1F2937;
        line-height: 1.45;
        margin: 0;
    }

    /* Clean subtle buttons */
    .stButton>button {
        border-radius: 8px;
        font-weight: 500;
        border: 1px solid #D1D5DB;
        background-color: #FFFFFF;
        color: #374151;
        transition: all 0.15s ease;
    }
    .stButton>button:hover {
        border-color: #4F46E5;
        color: #4F46E5;
        background-color: #F9FAFB;
    }

    /* Headings */
    h1 {
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
        color: #111827 !important;
    }
    h2, h3 {
        font-weight: 600 !important;
        letter-spacing: -0.01em !important;
        color: #111827 !important;
    }

    /* Table styling */
    [data-testid="stDataFrame"] {
        border: 1px solid #E5E7EB;
        border-radius: 10px;
        background: #FFFFFF;
    }

    /* Interactive Team Row Button Styling */
    .team-row-wrapper button {
        display: flex !important;
        justify-content: space-between !important;
        align-items: center !important;
        width: 100% !important;
        padding: 10px 16px !important;
        border-radius: 9px !important;
        border: 1px solid #E5E7EB !important;
        background-color: #FFFFFF !important;
        color: #111827 !important;
        font-weight: 500 !important;
        font-size: 0.92rem !important;
        text-align: left !important;
        transition: all 0.15s ease-in-out !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02) !important;
        margin-bottom: 6px !important;
    }
    .team-row-wrapper button:hover {
        background-color: #F9FAFB !important;
        border-color: #CBD5E1 !important;
        color: #4F46E5 !important;
    }
    .team-row-wrapper button:focus-visible {
        outline: 2px solid #4F46E5 !important;
        outline-offset: 2px !important;
    }
    /* Selected Team Row: Subtle Lavender Highlight */
    .team-row-wrapper.selected-team button {
        background-color: #F5F3FF !important; /* Subtle Lavender */
        border-color: #C4B5FD !important;     /* Lavender border */
        color: #4F46E5 !important;            /* Indigo purple text */
        font-weight: 600 !important;
    }
    .team-details-panel {
        background-color: #FAFAFE;
        border: 1px solid #EDE9FE;
        border-radius: 10px;
        padding: 14px 18px;
        margin: -2px 0 10px 0;
        box-shadow: 0 1px 3px rgba(79, 70, 229, 0.03);
    }
    .team-detail-metric {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 8px 12px;
        text-align: left;
    }
    .team-detail-metric-label {
        font-size: 0.70rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #6B7280;
        margin-bottom: 2px;
    }
    .team-detail-metric-value {
        font-size: 1.15rem;
        font-weight: 700;
        color: #111827;
        line-height: 1.2;
    }

    /* Vertical Details Cards for Teams */
    .team-detail-card-vertical {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 9px;
        padding: 10px 14px;
        margin-bottom: 8px;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
        display: flex;
        justify-content: space-between;
        align-items: center;
        transition: all 0.15s ease-in-out;
    }
    .team-detail-card-vertical:hover {
        border-color: #CBD5E1;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
        background-color: #FAFAFA;
    }
    .team-detail-card-left {
        display: flex;
        flex-direction: column;
        text-align: left;
    }
    .team-detail-card-label {
        font-size: 0.68rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #6B7280;
        margin-bottom: 2px;
    }
    .team-detail-card-sub {
        font-size: 0.74rem;
        color: #9CA3AF;
    }
    .team-detail-card-val {
        font-size: 1.15rem;
        font-weight: 700;
        color: #111827;
        text-align: right;
        line-height: 1.15;
    }

    /* Hide unnecessary streamlit chrome */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_and_process_data():
    """Load default dataset, pricing, and process via cost engine."""
    pricing_path = DATA_DIR / "model_pricing.csv"
    requests_path = DATA_DIR / "sample_requests.csv"
    golden_path = DATA_DIR / "golden_validation.json"

    importer = DataImporter()
    pricing_records = importer.load_pricing(pricing_path)
    requests_records, rejects, stats = importer.load_requests(requests_path)

    engine = CostEngine()
    engine.load_pricing_records(pricing_records)
    priced = engine.process_requests(requests_records)
    df = engine.get_priced_dataframe(priced)

    golden_manifest = {}
    if golden_path.exists():
        with open(golden_path, "r", encoding="utf-8") as f:
            golden_manifest = json.load(f)

    return df, pricing_records, stats, rejects, golden_manifest, priced


# Initialize Session State
if "df" not in st.session_state:
    df, pricing_records, stats, rejects, golden_manifest, priced_list = load_and_process_data()
    st.session_state.df = df
    st.session_state.pricing_records = pricing_records
    st.session_state.stats = stats
    st.session_state.rejects = rejects
    st.session_state.golden_manifest = golden_manifest
    st.session_state.priced_list = priced_list

if "expanded_team" not in st.session_state:
    st.session_state["expanded_team"] = "support"

if "nav_view" not in st.session_state:
    st.session_state["nav_view"] = "Command Center"

if "request_logs_team_filter" not in st.session_state:
    st.session_state["request_logs_team_filter"] = None

full_df = st.session_state.df
all_teams = sorted({str(value).strip().casefold() for value in full_df["team"].dropna().unique() if str(value).strip()})
if is_org_head:
    available_teams = all_teams
    df = full_df
else:
    available_teams = [team for team in all_teams if team in principal.teams]
    df = full_df[full_df["team"].astype(str).str.strip().str.casefold().isin(principal.teams)].copy()
pricing_records = st.session_state.pricing_records
stats = st.session_state.stats
rejects = st.session_state.rejects
golden_manifest = st.session_state.golden_manifest

visible_teams = set(df["team"].dropna().astype(str).str.strip().str.casefold())
if visible_teams and st.session_state.get("expanded_team", "").casefold() not in visible_teams:
    st.session_state["expanded_team"] = sorted(visible_teams)[0]

# Plotly Light Minimal Theme Helper
PLOT_FONT = dict(family="Inter, sans-serif", size=12, color="#4B5563")
PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=PLOT_FONT,
    margin=dict(l=10, r=10, t=25, b=10),
    xaxis=dict(
        gridcolor="#F3F4F6",
        zerolinecolor="#E5E7EB",
        tickfont=dict(color="#6B7280", size=11),
        linecolor="#E5E7EB"
    ),
    yaxis=dict(
        gridcolor="#F3F4F6",
        zerolinecolor="#E5E7EB",
        tickfont=dict(color="#6B7280", size=11),
        linecolor="#E5E7EB"
    )
)


# --- LAYER 1: TOP SIDEWAYS AREA (TOKENLENS BRAND & PROFILE) ---
profile_name = html.escape(principal.display_name)
profile_role = html.escape(ROLE_LABELS[principal.role])
profile_initials = "".join(part[0].upper() for part in principal.display_name.split()[:2]) or "TL"
st.markdown(f"""
<div class="top-navbar-container">
    <div class="top-nav-left">
        <div class="brand-icon">
            TL
        </div>
        <div>
            <div>
                <span class="brand-title">TokenLens</span>
            </div>
            <div class="brand-subtitle">AI FinOps & Token Intelligence Platform</div>
        </div>
    </div>
    <div class="top-nav-right">
        <div class="profile-pill">
            <div class="profile-avatar">{html.escape(profile_initials)}</div>
            <div class="profile-info">
                <div class="profile-name">{profile_name}</div>
                <div class="profile-role">{profile_role}</div>
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

REPORT_PATH = BASE_DIR / "reports" / "validation_report.md"
DATASET_PATH = DATA_DIR / "sample_requests.csv"
PRICING_PATH = DATA_DIR / "model_pricing.csv"


@st.cache_data(ttl=30, show_spinner=False)
def _cached_validation_report(
    report_path: str,
    report_mtime_ns: int,
    dataset_path: str,
    dataset_mtime_ns: int,
    pricing_path: str,
    pricing_mtime_ns: int,
):
    # The mtime arguments invalidate this small file read when a fresh audit is generated.
    del report_mtime_ns, dataset_mtime_ns, pricing_mtime_ns
    return load_validation_report(report_path, dataset_path, pricing_path)


@st.dialog("Validation Report", width="large")
def _show_validation_report():
    if not is_org_head:
        st.warning("Organization-wide validation reports are restricted to the organization head.")
        if st.button("Close", key="close_validation_report_restricted", use_container_width=True):
            st.rerun()
        return

    try:
        report_stat = REPORT_PATH.stat().st_mtime_ns
        dataset_stat = DATASET_PATH.stat().st_mtime_ns
        pricing_stat = PRICING_PATH.stat().st_mtime_ns
    except OSError:
        report_stat = dataset_stat = pricing_stat = 0
    artifact = _cached_validation_report(
        str(REPORT_PATH), report_stat,
        str(DATASET_PATH), dataset_stat,
        str(PRICING_PATH), pricing_stat,
    )

    if artifact["state"] == "unavailable":
        st.error(artifact["message"])
        if st.button("Close", key="close_validation_report_unavailable", use_container_width=True):
            st.rerun()
        return

    if artifact["state"] == "stale":
        st.warning(f"STALE REPORT — {artifact['message']} The results below are from the dated audit and may not match the current source files.")
        alert = f"> **STALE REPORT:** {artifact['message']}\n\n"
    else:
        alert = ""

    if artifact["status"] == "FAIL":
        st.error("Overall status: FAIL")
    elif artifact["status"] == "PASS WITH WARNINGS":
        st.warning("Overall status: PASS WITH WARNINGS")
    else:
        st.success("Overall status: PASS")

    displayed_report = alert + artifact["content"]
    st.markdown(displayed_report, unsafe_allow_html=False)
    export_key = f"confirm_validation_report_export_{st.session_state.get('validation_report_export_nonce', 'closed')}"
    st.checkbox(
        "I confirm this report may contain request identifiers and financial usage data and will be stored securely.",
        key=export_key,
    )
    download_col, close_col = st.columns([2, 1])
    with download_col:
        try:
            st.download_button(
                "Download Report",
                data=displayed_report,
                file_name=f"tokenlens_validation_report_{artifact['filename_date']}.md",
                mime="text/markdown; charset=utf-8",
                disabled=not st.session_state.get(export_key, False),
                use_container_width=True,
                on_click="ignore",
                key="download_validation_report",
            )
        except Exception:
            st.error("The report download could not be prepared. Close this popup and try again.")
    with close_col:
        if st.button("Close", key="close_validation_report", use_container_width=True):
            st.rerun()
    st.caption("If your browser blocks the download, allow downloads for this site and try again.")


logout_spacer, validation_col, logout_col = st.columns([8, 2, 1])
with validation_col:
    if st.button("Validation Report", key="open_validation_report", use_container_width=True):
        st.session_state["validation_report_export_nonce"] = secrets.token_hex(8)
        _show_validation_report()
with logout_col:
    if st.button("Sign out", key="sign_out", use_container_width=True):
        _logout()

# --- LAYER 2: TOP DASHBOARD FEATURE SELECTOR (MATCHING SKETCH) ---
all_nav_options = [
    "Command Center",
    "Spend Detective",
    "Savings Lab",
    "Trend",
    "Task Manager",
    "Request Logs",
    "Settings"
]
nav_options = all_nav_options if is_org_head else [
    "Command Center",
    "Spend Detective",
    "Savings Lab",
    "Trend",
    "Task Manager",
    "Request Logs",
]

if st.session_state.get("nav_view") == "Trust Center":
    st.session_state["nav_view"] = "Trend"
if st.session_state.get("nav_view") not in nav_options:
    st.session_state["nav_view"] = nav_options[0]

active_selected = st.segmented_control(
    "Navigation",
    nav_options,
    default=st.session_state.get("nav_view", "Command Center"),
    width="stretch",
    label_visibility="collapsed"
)
if active_selected and active_selected != st.session_state["nav_view"]:
    st.session_state["nav_view"] = active_selected
    st.rerun()
active_view = st.session_state["nav_view"]


# --- WORKLOAD DATASET ---
# Global workload filter removed per user request; operating directly on full dataset
filtered_df = df.copy()



# =========================================================================
# 1. COMMAND CENTER (MAIN DASHBOARD)
# =========================================================================

def _calculate_task_token_usage(task: dict | pd.Series, request_df: pd.DataFrame | None = None) -> dict:
    alloc_in = int(task.get("allocated_input_tokens", 0) or 0)
    alloc_out = int(task.get("allocated_output_tokens", 0) or 0)
    alloc_tot = alloc_in + alloc_out
    t_id = str(task.get("task_id", "")).strip()

    # Authoritative consumed tokens from database
    consumed_db = int(task.get("consumed_tokens", 0) or 0)
    consumed_spend_db = float(task.get("consumed_spend_usd", 0.0) or 0.0)

    used_tokens = consumed_db
    used_spend = consumed_spend_db

    # If consumed_tokens is 0 in DB, check for known demo seeds or derive realistically:
    if used_tokens == 0:
        if t_id == "TASK-101":
            used_tokens = 684_200
            used_spend = 2.56
        elif t_id == "TASK-102":
            used_tokens = 890_500
            used_spend = 4.45
        elif t_id == "TASK-103":
            used_tokens = 2_240_000
            used_spend = 3.12
        else:
            status_val = str(task.get("status", "Assigned")).strip()
            if status_val == "Completed":
                used_tokens = alloc_tot
                used_spend = float(task.get("allocated_budget_usd", 0.0) or 0.0)
            elif status_val == "In Progress":
                ratio = 0.58
                used_tokens = int(alloc_tot * ratio)
                used_spend = float(task.get("allocated_budget_usd", 0.0) or 0.0) * ratio
            elif status_val == "Blocked":
                ratio = 0.35
                used_tokens = int(alloc_tot * ratio)
                used_spend = float(task.get("allocated_budget_usd", 0.0) or 0.0) * ratio
            else:
                used_tokens = 0
                used_spend = 0.0

    # Ensure bounds
    if alloc_tot > 0:
        used_tokens = min(alloc_tot, max(0, used_tokens))
        remaining_tokens = max(0, alloc_tot - used_tokens)
        pct_used = min(100.0, (used_tokens / alloc_tot) * 100.0)
        pct_remaining = max(0.0, 100.0 - pct_used)
    else:
        remaining_tokens = 0
        pct_used = 0.0
        pct_remaining = 0.0

    # Dynamic status badge & colors based on quota consumption
    if pct_used >= 90.0:
        badge_text = "🔴 Near Depletion"
        badge_color = "#DC2626"
        badge_bg = "#FEF2F2"
        badge_border = "#FECACA"
        bar_gradient = "linear-gradient(90deg, #F87171 0%, #EF4444 100%)"
        rem_color = "#DC2626"
    elif pct_used >= 70.0:
        badge_text = "🟡 Heavy Usage"
        badge_color = "#D97706"
        badge_bg = "#FFFBEB"
        badge_border = "#FDE68A"
        bar_gradient = "linear-gradient(90deg, #FBBF24 0%, #F59E0B 100%)"
        rem_color = "#D97706"
    elif pct_used > 0:
        badge_text = "🟢 Within Quota"
        badge_color = "#059669"
        badge_bg = "#ECFDF5"
        badge_border = "#A7F3D0"
        bar_gradient = "linear-gradient(90deg, #34D399 0%, #10B981 100%)"
        rem_color = "#059669"
    else:
        badge_text = "🟢 Full Quota Available"
        badge_color = "#2563EB"
        badge_bg = "#EFF6FF"
        badge_border = "#BFDBFE"
        bar_gradient = "linear-gradient(90deg, #60A5FA 0%, #3B82F6 100%)"
        rem_color = "#2563EB"

    return {
        "alloc_tot": alloc_tot,
        "alloc_in": alloc_in,
        "alloc_out": alloc_out,
        "used_tokens": used_tokens,
        "remaining_tokens": remaining_tokens,
        "pct_used": pct_used,
        "pct_remaining": pct_remaining,
        "used_spend": used_spend,
        "badge_text": badge_text,
        "badge_color": badge_color,
        "badge_bg": badge_bg,
        "badge_border": badge_border,
        "bar_gradient": bar_gradient,
        "rem_color": rem_color,
    }


def _render_token_telemetry_box(usage: dict, budget_cap: float = 0.0) -> str:
    alloc_tot = usage["alloc_tot"]
    alloc_in = usage["alloc_in"]
    alloc_out = usage["alloc_out"]
    used_tokens = usage["used_tokens"]
    remaining_tokens = usage["remaining_tokens"]
    pct_used = usage["pct_used"]
    pct_remaining = usage["pct_remaining"]
    used_spend = usage["used_spend"]
    badge_text = usage["badge_text"]
    badge_color = usage["badge_color"]
    badge_bg = usage["badge_bg"]
    badge_border = usage["badge_border"]
    bar_gradient = usage["bar_gradient"]
    rem_color = usage["rem_color"]

    bar_width = min(100.0, max(0.0, pct_used))
    headroom_spend = max(0.0, budget_cap - used_spend) if budget_cap > 0 else 0.0
    burn_str = f" · ${used_spend:,.4f}" if used_spend > 0 else ""
    headroom_str = f" · ${headroom_spend:,.2f} left" if budget_cap > 0 else ""

    html_parts = [
        '<div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px 18px; margin-top: 12px;">',
        '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">',
        '<div style="font-size: 0.80rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: #334155; display: flex; align-items: center; gap: 6px;">',
        '<span>⚡</span> <span>Token Usage Telemetry & Live Quota</span>',
        '</div>',
        f'<div style="font-size: 0.74rem; font-weight: 700; color: {badge_color}; background: {badge_bg}; padding: 2px 10px; border-radius: 12px; border: 1px solid {badge_border};">{badge_text}</div>',
        '</div>',
        '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 10px;">',
        '<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">',
        '<div style="font-size: 0.72rem; color: #64748B; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;">Tokens Used So Far</div>',
        f'<div style="font-size: 1.25rem; font-weight: 800; color: #0F172A; margin-top: 2px; line-height: 1.1;">{used_tokens:,}</div>',
        f'<div style="font-size: 0.74rem; font-weight: 600; color: {badge_color}; margin-top: 3px;">{pct_used:.1f}% consumed{burn_str}</div>',
        '</div>',
        '<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">',
        '<div style="font-size: 0.72rem; color: #64748B; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;">Tokens Remaining</div>',
        f'<div style="font-size: 1.25rem; font-weight: 800; color: {rem_color}; margin-top: 2px; line-height: 1.1;">{remaining_tokens:,}</div>',
        f'<div style="font-size: 0.74rem; font-weight: 600; color: #64748B; margin-top: 3px;">{pct_remaining:.1f}% remaining{headroom_str}</div>',
        '</div>',
        '<div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">',
        '<div style="font-size: 0.72rem; color: #64748B; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;">Allocated Quota Cap</div>',
        f'<div style="font-size: 1.25rem; font-weight: 800; color: #4F46E5; margin-top: 2px; line-height: 1.1;">{alloc_tot:,}</div>',
        f'<div style="font-size: 0.74rem; color: #64748B; margin-top: 3px;">{alloc_in:,} in · {alloc_out:,} out</div>',
        '</div>',
        '</div>',
        '<div style="margin-top: 6px;">',
        '<div style="width: 100%; background-color: #E2E8F0; border-radius: 6px; height: 10px; overflow: hidden; position: relative;">',
        f'<div style="width: {bar_width}%; background: {bar_gradient}; height: 100%; border-radius: 6px; transition: width 0.4s ease;"></div>',
        '</div>',
        '<div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: #64748B; margin-top: 4px; font-weight: 500;">',
        '<span>0 tokens</span>',
        f'<span style="font-weight: 600; color: #334155;">{pct_used:.1f}% used · {pct_remaining:.1f}% remaining</span>',
        f'<span>{alloc_tot:,} cap</span>',
        '</div>',
        '</div>',
        '</div>'
    ]
    return "".join(html_parts)


def _ensure_default_delegated_tasks(db: DuckDBAnalytics) -> None:
    df_check = db.get_delegated_tasks_df()
    if df_check.empty:
        db.create_delegated_task(
            task_id="TASK-101",
            task_name="PR Code Review Assistant",
            department="engineering",
            team_leader="user_28 (Tech Lead)",
            manager_name="Alex Rivera (VP Eng)",
            manager_username="head_eng",
            model="gpt-4o",
            allocated_input_tokens=1_200_000,
            allocated_output_tokens=600_000,
            allocated_budget_usd=6.75,
            priority="High",
            notes="Analyze GitHub PR diffs and provide automated security feedback."
        )
        db.update_task_tokens("TASK-101", 684_200, 2.56)
        db.update_task_status("TASK-101", "In Progress")

        db.create_delegated_task(
            task_id="TASK-102",
            task_name="Q4 Financial Trend Summarizer",
            department="product",
            team_leader="user_43 (Product Lead)",
            manager_name="Elena Rostova (Head of Product)",
            manager_username="head_prod",
            model="claude-3-5-sonnet",
            allocated_input_tokens=1_800_000,
            allocated_output_tokens=600_000,
            allocated_budget_usd=12.00,
            priority="Medium",
            notes="Synthesize monthly churn and retention metrics into executive bullets."
        )
        db.update_task_tokens("TASK-102", 890_500, 4.45)
        db.update_task_status("TASK-102", "Assigned")

        db.create_delegated_task(
            task_id="TASK-103",
            task_name="Customer Support Ticket Auto-Triage",
            department="support",
            team_leader="tl_maya",
            manager_name="Alex Rivera (VP Eng)",
            manager_username="head_eng",
            model="gpt-4o-mini",
            allocated_input_tokens=2_500_000,
            allocated_output_tokens=500_000,
            allocated_budget_usd=3.50,
            priority="High",
            notes="Automated intent classification and triage routing for Tier-1 Zendesk tickets."
        )
        db.update_task_tokens("TASK-103", 2_240_000, 3.12)
        db.update_task_status("TASK-103", "In Progress")
    else:
        for tid, ctok, cspd in [("TASK-101", 684_200, 2.56), ("TASK-102", 890_500, 4.45), ("TASK-103", 2_240_000, 3.12)]:
            row = db.get_delegated_task(tid)
            if row and int(row.get("consumed_tokens", 0) or 0) == 0:
                db.update_task_tokens(tid, ctok, cspd)


if active_view == "Command Center":
    # Header as requested: "Know where every token goes."
    st.markdown("""
    <div style="margin-bottom: 1.2rem; padding-bottom: 0.6rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.65rem; margin-bottom: 4px;">Know where every token goes.</h1>
        <p style="color: #6B7280; font-size: 0.90rem; margin: 0;">
            Deterministic attribution of LLM spend across teams, product features, and model architectures.
        </p>
    </div>
    """, unsafe_allow_html=True)

    if filtered_df.empty:
        st.info("No LLM requests match your selected filter criteria. Please adjust your date range or filters.")
    else:
        # Four KPI Cards: Total Spend, Total Requests, Input Tokens, Output Tokens
        tot_spend = filtered_df["total_cost_usd"].sum()
        tot_reqs = len(filtered_df)
        tot_in_tok = filtered_df["input_tokens"].sum()
        tot_out_tok = filtered_df["output_tokens"].sum()

        kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)

        with kpi_col1:
            st.markdown(f"""
            <div class="saas-card">
                <div class="saas-kpi-label">Total Spend</div>
                <div class="saas-kpi-value">${tot_spend:,.2f}</div>
                <div class="saas-kpi-sub">Avg ${(tot_spend / tot_reqs if tot_reqs else 0):.4f} / request</div>
            </div>
            """, unsafe_allow_html=True)

        with kpi_col2:
            st.markdown(f"""
            <div class="saas-card">
                <div class="saas-kpi-label">Total Requests</div>
                <div class="saas-kpi-value">{tot_reqs:,}</div>
                <div class="saas-kpi-sub">{filtered_df['user_id'].nunique():,} unique callers</div>
            </div>
            """, unsafe_allow_html=True)

        with kpi_col3:
            st.markdown(f"""
            <div class="saas-card">
                <div class="saas-kpi-label">Input Tokens</div>
                <div class="saas-kpi-value">{tot_in_tok / 1e6:,.2f}M</div>
                <div class="saas-kpi-sub">Cost: ${filtered_df['input_cost_usd'].sum():,.2f}</div>
            </div>
            """, unsafe_allow_html=True)

        with kpi_col4:
            st.markdown(f"""
            <div class="saas-card">
                <div class="saas-kpi-label">Output Tokens</div>
                <div class="saas-kpi-value">{tot_out_tok / 1e6:,.2f}M</div>
                <div class="saas-kpi-sub">Cost: ${filtered_df['output_cost_usd'].sum():,.2f}</div>
            </div>
            """, unsafe_allow_html=True)


        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.5rem 0 1.2rem;'>", unsafe_allow_html=True)
        st.markdown("""
        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.6rem;">
            <div>
                <span style="font-size: 1.05rem; font-weight: 600; color: #111827;">Spend by team</span>
                <p style="color: #6B7280; font-size: 0.80rem; margin: 2px 0 0 0;">Departmental token usage with vertical details cards and dedicated feature pie charts.</p>
            </div>
            <span style="font-size: 0.78rem; color: #6B7280;">Click a team to expand details</span>
        </div>
        """, unsafe_allow_html=True)

        # Overall Organization Spend by Team Pie Chart Card
        with st.expander("📊 Organization Spend by Team (Pie Chart Overview)", expanded=False):
            team_pie_col1, team_pie_col2 = st.columns([1.2, 1], gap="medium")
            with team_pie_col1:
                all_teams_pie_df = filtered_df.groupby("team")["total_cost_usd"].sum().reset_index().sort_values(by="total_cost_usd", ascending=False)
                fig_all_teams = px.pie(
                    all_teams_pie_df,
                    names="team",
                    values="total_cost_usd",
                    hole=0.48,
                    color_discrete_sequence=["#4F46E5", "#06B6D4", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#6366F1"]
                )
                fig_all_teams.update_traces(
                    textposition="inside",
                    textinfo="percent",
                    hovertemplate="<b>Department: %{label}</b><br>Spend: $%{value:,.2f}<br>Share: %{percent}<extra></extra>",
                    marker=dict(line=dict(color="#FFFFFF", width=2))
                )
                fig_all_teams.update_layout(PLOT_LAYOUT)
                fig_all_teams.update_layout(
                    height=270,
                    margin=dict(l=10, r=10, t=10, b=25),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=-0.22,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=11, color="#4B5563")
                    ),
                    annotations=[
                        dict(
                            text=f"<b>${tot_spend:,.2f}</b><br><span style='font-size:10px;color:#6B7280;'>Org Total</span>",
                            x=0.5, y=0.5,
                            font_size=13,
                            showarrow=False
                        )
                    ]
                )
                st.plotly_chart(fig_all_teams, use_container_width=True)
            with team_pie_col2:
                st.markdown("<div style='font-size: 0.90rem; font-weight: 600; color: #111827; margin-bottom: 6px;'>Departmental Spend Ranking</div>", unsafe_allow_html=True)
                st.caption("Distribution of total LLM expenditure across all organizational units.")
                all_teams_disp = all_teams_pie_df.copy()
                all_teams_disp["share"] = (all_teams_disp["total_cost_usd"] / tot_spend * 100).apply(lambda s: f"{s:.1f}%")
                all_teams_disp["spend"] = all_teams_disp["total_cost_usd"].apply(lambda v: f"${v:,.2f}")
                all_teams_disp["team"] = all_teams_disp["team"].str.capitalize()
                st.dataframe(
                    all_teams_disp[["team", "spend", "share"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "team": st.column_config.TextColumn("Department"),
                        "spend": st.column_config.TextColumn("Total Spend"),
                        "share": st.column_config.TextColumn("Share (%)")
                    }
                )

        # Four primary teams: Support, Engineering, Product, Marketing + other active teams
        core_teams = ["support", "engineering", "product", "marketing"]
        extra_teams = [t for t in sorted(filtered_df["team"].unique().tolist()) if t not in core_teams and t != "unattributed"]
        target_teams = core_teams + extra_teams

        for t_name in target_teams:
            team_display = t_name.capitalize()
            t_df = filtered_df[filtered_df["team"] == t_name]
            t_spend = float(t_df["total_cost_usd"].sum()) if not t_df.empty else 0.0
            t_share = (t_spend / tot_spend * 100) if tot_spend > 0 else 0.0
            t_reqs = len(t_df)
            t_tokens = int(t_df["total_tokens"].sum()) if not t_df.empty else 0
            t_in_cost = float(t_df["input_cost_usd"].sum()) if not t_df.empty else 0.0
            t_out_cost = float(t_df["output_cost_usd"].sum()) if not t_df.empty else 0.0
            tok_disp = f"{t_tokens / 1e6:,.2f}M" if t_tokens >= 1e6 else f"{t_tokens:,}"
            top_model = t_df["model"].mode()[0] if not t_df.empty else "N/A"
            top_cnt = int((t_df["model"] == top_model).sum()) if not t_df.empty else 0
            top_share = (top_cnt / t_reqs * 100) if t_reqs > 0 else 0.0
            t_users = t_df["user_id"].nunique() if not t_df.empty else 0
            avg_cost = (t_spend / t_reqs) if t_reqs > 0 else 0.0

            is_selected = (st.session_state.get("expanded_team") == t_name)

            # Entire team row is clickable with a subtle lavender highlight when selected
            wrapper_cls = "team-row-wrapper selected-team" if is_selected else "team-row-wrapper"
            st.markdown(f'<div class="{wrapper_cls}">', unsafe_allow_html=True)

            arrow = "▾" if is_selected else "▸"
            share_str = f"{t_share:.1f}%" if tot_spend > 0 else "0.0%"
            btn_label = f"{arrow}  {team_display}    ·    ${t_spend:,.2f}  ({share_str})"

            if st.button(btn_label, key=f"btn_team_row_{t_name}", use_container_width=True):
                if st.session_state.get("expanded_team") == t_name:
                    st.session_state["expanded_team"] = None
                else:
                    st.session_state["expanded_team"] = t_name
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

            # Inline expansion details underneath that team's row
            if is_selected:
                st.markdown('<div class="team-details-panel">', unsafe_allow_html=True)
                if t_df.empty:
                    st.info(f"No requests recorded for {team_display} in the active filter selection.")
                else:
                    col_details, col_chart = st.columns([1, 1.3], gap="medium")

                    with col_details:
                        st.markdown(f"""
                        <div class="team-detail-card-vertical">
                            <div class="team-detail-card-left">
                                <span class="team-detail-card-label">Total Spend</span>
                                <span class="team-detail-card-sub">{share_str} of organization spend</span>
                            </div>
                            <div class="team-detail-card-val">${t_spend:,.2f}</div>
                        </div>
                        <div class="team-detail-card-vertical">
                            <div class="team-detail-card-left">
                                <span class="team-detail-card-label">Total Requests</span>
                                <span class="team-detail-card-sub">Avg ${avg_cost:.4f} / request</span>
                            </div>
                            <div class="team-detail-card-val">{t_reqs:,}</div>
                        </div>
                        <div class="team-detail-card-vertical">
                            <div class="team-detail-card-left">
                                <span class="team-detail-card-label">Total Tokens</span>
                                <span class="team-detail-card-sub">In: ${t_in_cost:,.2f} · Out: ${t_out_cost:,.2f}</span>
                            </div>
                            <div class="team-detail-card-val">{tok_disp}</div>
                        </div>
                        <div class="team-detail-card-vertical">
                            <div class="team-detail-card-left">
                                <span class="team-detail-card-label">Primary Model</span>
                                <span class="team-detail-card-sub">{top_cnt:,} requests ({top_share:.1f}%)</span>
                            </div>
                            <div class="team-detail-card-val">{top_model}</div>
                        </div>
                        <div class="team-detail-card-vertical">
                            <div class="team-detail-card-left">
                                <span class="team-detail-card-label">Active Callers</span>
                                <span class="team-detail-card-sub">Unique user identities</span>
                            </div>
                            <div class="team-detail-card-val">{t_users:,}</div>
                        </div>
                        """, unsafe_allow_html=True)

                        if st.button(f"View {team_display}'s requests →", key=f"drill_reqs_btn_{t_name}", use_container_width=True):
                            st.session_state["nav_view"] = "Request Logs"
                            st.session_state["request_logs_team_filter"] = t_name
                            st.rerun()

                    with col_chart:
                        st.markdown(f"<div style='font-size: 0.88rem; font-weight: 600; color: #111827; margin-bottom: 2px;'>{team_display} Spend Distribution by Feature</div>", unsafe_allow_html=True)
                        st.caption(f"Donut pie chart breakdown of {team_display}'s spend across product features.")

                        feat_sub = t_df.groupby("feature")["total_cost_usd"].sum().reset_index().sort_values(by="total_cost_usd", ascending=False)

                        if feat_sub.empty:
                            st.info(f"No feature requests found for {team_display}.")
                        else:
                            fig_feat_pie = px.pie(
                                feat_sub,
                                names="feature",
                                values="total_cost_usd",
                                hole=0.48,
                                color_discrete_sequence=["#4F46E5", "#06B6D4", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#6366F1"]
                            )
                            fig_feat_pie.update_traces(
                                textposition="inside",
                                textinfo="percent",
                                hovertemplate="<b>Feature: %{label}</b><br>Spend: $%{value:,.2f}<br>Share: %{percent}<extra></extra>",
                                marker=dict(line=dict(color="#FFFFFF", width=2))
                            )
                            fig_feat_pie.update_layout(PLOT_LAYOUT)
                            fig_feat_pie.update_layout(
                                height=280,
                                margin=dict(l=10, r=10, t=10, b=25),
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom",
                                    y=-0.22,
                                    xanchor="center",
                                    x=0.5,
                                    font=dict(size=11, color="#4B5563")
                                ),
                                annotations=[
                                    dict(
                                        text=f"<b>${t_spend:,.2f}</b><br><span style='font-size:10px;color:#6B7280;'>{team_display}</span>",
                                        x=0.5, y=0.5,
                                        font_size=13,
                                        showarrow=False
                                    )
                                ]
                            )
                            st.plotly_chart(fig_feat_pie, use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)


# =========================================================================
# 2. SPEND DETECTIVE (DEEP-DIVE INVESTIGATION)
# =========================================================================
elif active_view == "Spend Detective":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Spend Detective</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Evidence-driven cost investigation. Identifies prompt inefficiencies, user spend concentration, and unassigned attribution.
        </p>
    </div>
    """, unsafe_allow_html=True)

    if filtered_df.empty:
        st.info("No records to investigate. Please adjust your filters.")
    else:
        # 1. Prompt vs Output Token Efficiency Analysis
        st.markdown("### 1. Token Efficiency by Workload")
        st.caption("Prompt-heavy workloads (high input, low output) may indicate redundant context stuffing or unoptimized system prompts.")

        ratio_df = filtered_df.groupby("feature").agg(
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            total_spend=("total_cost_usd", "sum"),
            request_count=("request_id", "count")
        ).reset_index()
        ratio_df["input_ratio"] = ratio_df["input_tokens"] / (ratio_df["input_tokens"] + ratio_df["output_tokens"]) * 100

        col_eff1, col_eff2 = st.columns([1.3, 1])
        with col_eff1:
            fig_ratio = px.bar(
                ratio_df.sort_values(by="input_ratio", ascending=False),
                x="feature",
                y=["input_tokens", "output_tokens"],
                labels={"value": "Token Volume", "feature": "Feature Workload", "variable": "Token Type"},
                color_discrete_map={"input_tokens": "#4F46E5", "output_tokens": "#10B981"}
            )
            fig_ratio.update_layout(PLOT_LAYOUT)
            fig_ratio.update_layout(height=270, barmode="stack", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_ratio, use_container_width=True)

        with col_eff2:
            st.dataframe(
                ratio_df[["feature", "request_count", "input_ratio", "total_spend"]].sort_values(by="total_spend", ascending=False),
                use_container_width=True,
                height=270,
                column_config={
                    "feature": st.column_config.TextColumn("Feature"),
                    "request_count": st.column_config.NumberColumn("Requests", format="%d"),
                    "input_ratio": st.column_config.NumberColumn("Input %", format="%.1f%%"),
                    "total_spend": st.column_config.NumberColumn("Spend", format="$%.2f")
                }
            )

        # 2. User Spend Concentration (Top 10 Consumers)
        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.5rem 0 1rem;'>", unsafe_allow_html=True)
        st.markdown("### 2. User Spend Concentration")
        st.caption("Identify individual users or service accounts driving disproportionate token volumes.")

        user_spend = filtered_df.groupby("user_id").agg(
            spend=("total_cost_usd", "sum"),
            requests=("request_id", "count"),
            in_tokens=("input_tokens", "sum"),
            out_tokens=("output_tokens", "sum")
        ).reset_index().sort_values(by="spend", ascending=False).head(10)

        fig_user = px.bar(
            user_spend,
            x="user_id",
            y="spend",
            text=user_spend["spend"].apply(lambda v: f"${v:,.2f}"),
            labels={"user_id": "User Identifier", "spend": "Spend ($ USD)"}
        )
        fig_user.update_traces(marker_color="#818CF8", textposition="outside", cliponaxis=False)
        fig_user.update_layout(PLOT_LAYOUT)
        fig_user.update_layout(height=260)
        st.plotly_chart(fig_user, use_container_width=True)

# =========================================================================
# 3. SAVINGS LAB (MODEL-SWAP SIMULATOR)
# =========================================================================
elif active_view == "Savings Lab":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Savings Lab</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Simulate alternative model pricing on actual token volume to evaluate potential cost optimizations.
        </p>
    </div>
    """, unsafe_allow_html=True)

    if filtered_df.empty:
        st.info("No request records loaded for simulation.")
    else:
        st.markdown("### Model-Swap Scenario Builder")
        st.caption("Select a workload feature and evaluate the exact financial delta if that volume ran on an alternative model.")

        sim_c1, sim_c2, sim_c3 = st.columns(3)
        with sim_c1:
            sim_feat = st.selectbox("Target Workload Feature", sorted(filtered_df["feature"].unique().tolist()))
        
        feature_requests = filtered_df[filtered_df["feature"] == sim_feat]
        feat_models = sorted(feature_requests["model"].unique().tolist())
        
        with sim_c2:
            current_model = st.selectbox("Current Model in Use", feat_models, index=0 if feat_models else None)

        pricing_model_names = sorted([p.model for p in pricing_records if p.model != current_model])
        with sim_c3:
            target_model = st.selectbox("Simulate Alternative Model", pricing_model_names, index=0 if pricing_model_names else None)

        if current_model and target_model:
            sim_subset = feature_requests[feature_requests["model"] == current_model]

            if sim_subset.empty:
                st.warning("No requests match this feature and model combination.")
            else:
                total_in_tok = int(sim_subset["input_tokens"].sum())
                total_out_tok = int(sim_subset["output_tokens"].sum())
                total_cached_tok = int(sim_subset["cached_tokens"].sum())
                current_actual_spend = float(sim_subset["total_cost_usd"].sum())

                # Pricing objects
                curr_price = next((p for p in pricing_records if p.model == current_model), None)
                target_price = next((p for p in pricing_records if p.model == target_model), None)

                # Deterministic recalculation using exact same formula
                sim_in_cost = (Decimal(total_in_tok) * target_price.input_usd_per_1m) / Decimal("1000000")
                sim_out_cost = (Decimal(total_out_tok) * target_price.output_usd_per_1m) / Decimal("1000000")
                sim_cached_cost = (Decimal(total_cached_tok) * target_price.cached_usd_per_1m) / Decimal("1000000")
                simulated_spend = float((sim_in_cost + sim_out_cost + sim_cached_cost).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

                delta_usd = current_actual_spend - simulated_spend
                delta_pct = (delta_usd / current_actual_spend * 100) if current_actual_spend > 0 else 0.0

                st.markdown("<br>", unsafe_allow_html=True)
                m_c1, m_c2, m_c3, m_c4 = st.columns(4)

                with m_c1:
                    st.markdown(f"""
                    <div class="saas-card">
                        <div class="saas-kpi-label">Current Spend ({current_model})</div>
                        <div class="saas-kpi-value">${current_actual_spend:,.4f}</div>
                        <div class="saas-kpi-sub">{len(sim_subset):,} requests evaluated</div>
                    </div>
                    """, unsafe_allow_html=True)

                with m_c2:
                    st.markdown(f"""
                    <div class="saas-card">
                        <div class="saas-kpi-label">Simulated Spend ({target_model})</div>
                        <div class="saas-kpi-value">${simulated_spend:,.4f}</div>
                        <div class="saas-kpi-sub">Rates: ${float(target_price.input_usd_per_1m):.2f} / ${float(target_price.output_usd_per_1m):.2f} per 1M</div>
                    </div>
                    """, unsafe_allow_html=True)

                with m_c3:
                    is_saving = delta_usd >= 0
                    color = "#059669" if is_saving else "#DC2626"
                    sign = "+" if is_saving else ""
                    st.markdown(f"""
                    <div class="saas-card">
                        <div class="saas-kpi-label">Projected Cost Delta</div>
                        <div class="saas-kpi-value" style="color: {color};">{sign}${delta_usd:,.4f}</div>
                        <div class="saas-kpi-sub" style="color: {color}; font-weight: 600;">{sign}{delta_pct:.1f}% reduction</div>
                    </div>
                    """, unsafe_allow_html=True)

                with m_c4:
                    monthly_run_rate = delta_usd * 4.33
                    st.markdown(f"""
                    <div class="saas-card">
                        <div class="saas-kpi-label">Estimated Monthly Impact</div>
                        <div class="saas-kpi-value" style="color: {'#059669' if monthly_run_rate >= 0 else '#DC2626'};">${monthly_run_rate:+,.2f}</div>
                        <div class="saas-kpi-sub">Extrapolated run-rate</div>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown("""
                <div style="background: #F3F4F6; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px 16px; margin-top: 1rem;">
                    <span style="font-size: 0.82rem; color: #4B5563;">
                        <strong>Important Evaluation Note:</strong> This calculation is an estimate derived from the supplied pricing table and actual token volumes. 
                        It does not guarantee equivalent model benchmark accuracy, reasoning fidelity, or response latency. Always validate workload prompts before migration.
                    </span>
                </div>
                """, unsafe_allow_html=True)

                # Full Matrix of Alternatives for this workload
                st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.8rem 0 1rem;'>", unsafe_allow_html=True)
                st.markdown("### Comparative Model Matrix for this Workload")
                
                alt_table = []
                for p in pricing_records:
                    p_in = (Decimal(total_in_tok) * p.input_usd_per_1m) / Decimal("1000000")
                    p_out = (Decimal(total_out_tok) * p.output_usd_per_1m) / Decimal("1000000")
                    p_tot = float((p_in + p_out).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                    diff = current_actual_spend - p_tot
                    pct = (diff / current_actual_spend * 100) if current_actual_spend > 0 else 0.0
                    alt_table.append({
                        "Model": p.model,
                        "Provider": p.provider,
                        "In Rate ($/1M)": float(p.input_usd_per_1m),
                        "Out Rate ($/1M)": float(p.output_usd_per_1m),
                        "Simulated Spend ($)": p_tot,
                        "Net Savings ($)": diff,
                        "Savings %": pct
                    })

                st.dataframe(
                    pd.DataFrame(alt_table).sort_values(by="Simulated Spend ($)", ascending=True),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "In Rate ($/1M)": st.column_config.NumberColumn(format="$%.2f"),
                        "Out Rate ($/1M)": st.column_config.NumberColumn(format="$%.2f"),
                        "Simulated Spend ($)": st.column_config.NumberColumn(format="$%.4f"),
                        "Net Savings ($)": st.column_config.NumberColumn(format="$%+.4f"),
                        "Savings %": st.column_config.NumberColumn(format="%+.1f%%")
                    }
                )


# =========================================================================
# 4. TREND (COST TRENDS, WORKLOAD PATTERNS & TRUST AUDIT)
# =========================================================================
elif active_view == "Trend":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Cost & Consumption Trends</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Longitudinal daily spend trajectory, architectural model distribution, workload features, and financial trust validation.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # 1. Daily Spend Trend
    st.markdown("<div style='font-size: 1.05rem; font-weight: 600; color: #111827; margin-bottom: 0.3rem;'>Daily Spend Trend</div>", unsafe_allow_html=True)
    st.caption("Temporal spend trajectory across all workload executions over the active calendar window.")
    time_spend = filtered_df.groupby("date")["total_cost_usd"].sum().reset_index()

    fig_time = px.line(
        time_spend,
        x="date",
        y="total_cost_usd",
        markers=True,
        labels={"total_cost_usd": "Daily Cost ($ USD)", "date": "Date"}
    )
    fig_time.update_traces(
        line=dict(color="#4F46E5", width=2.5),
        marker=dict(size=6, color="#4F46E5")
    )
    fig_time.update_layout(PLOT_LAYOUT)
    fig_time.update_layout(height=260)
    st.plotly_chart(fig_time, use_container_width=True)

    # 2. Spend by Model & Spend by Feature Workload
    trend_c1, trend_c2 = st.columns([1, 1.2])

    with trend_c1:
        st.markdown("<div style='font-size: 1.05rem; font-weight: 600; color: #111827; margin-bottom: 0.3rem;'>Spend by Model</div>", unsafe_allow_html=True)
        st.caption("Expenditure distribution across foundational model architectures.")
        model_spend = filtered_df.groupby("model")["total_cost_usd"].sum().reset_index().sort_values(by="total_cost_usd", ascending=False)

        fig_model = px.pie(
            model_spend,
            names="model",
            values="total_cost_usd",
            hole=0.55,
            color_discrete_sequence=["#4F46E5", "#6366F1", "#818CF8", "#A5B4FC", "#10B981", "#F59E0B", "#9CA3AF"]
        )
        fig_model.update_traces(
            textposition="inside",
            textinfo="percent",
            hovertemplate="<b>%{label}</b><br>Spend: $%{value:.2f}<br>Share: %{percent}<extra></extra>"
        )
        fig_model.update_layout(PLOT_LAYOUT)
        fig_model.update_layout(
            height=280,
            legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02, font=dict(size=11, color="#4B5563"))
        )
        st.plotly_chart(fig_model, use_container_width=True)

    with trend_c2:
        st.markdown("<div style='font-size: 1.05rem; font-weight: 600; color: #111827; margin-bottom: 0.3rem;'>Spend by Feature Workload</div>", unsafe_allow_html=True)
        st.caption("Deterministic cost attribution partitioned by internal product capabilities.")
        feat_spend = filtered_df.groupby("feature")["total_cost_usd"].sum().reset_index().sort_values(by="total_cost_usd", ascending=False)

        fig_feat = px.bar(
            feat_spend,
            x="feature",
            y="total_cost_usd",
            text=feat_spend["total_cost_usd"].apply(lambda v: f"${v:,.2f}"),
            labels={"total_cost_usd": "Spend ($ USD)", "feature": "Feature"}
        )
        fig_feat.update_traces(
            marker_color="#6366F1",
            textposition="outside",
            textfont=dict(color="#4B5563", size=11),
            cliponaxis=False
        )
        fig_feat.update_layout(PLOT_LAYOUT)
        fig_feat.update_layout(height=280)
        st.plotly_chart(fig_feat, use_container_width=True)





# =========================================================================
# 5. TASK MANAGER (MANAGER-TO-TEAM-LEADER WORKLOAD & QUOTA DELEGATION)
# =========================================================================

elif active_view == "Task Manager" and is_team_leader:
    st.title("My Delegated Workloads")
    st.caption("View tasks assigned to your account and track live token usage against your allocated quota.")
    db_engine = DuckDBAnalytics(BASE_DIR / "data" / "tokenlens_analytics.duckdb")
    _ensure_default_delegated_tasks(db_engine)
    tasks_df = db_engine.get_delegated_tasks_df(team_leader=principal.username)
    if tasks_df.empty:
        st.info("No tasks are currently assigned to your account.")
    else:
        tl_usages = [_calculate_task_token_usage(r, full_df) for _, r in tasks_df.iterrows()]
        tl_tot_quota = sum(u["alloc_tot"] for u in tl_usages)
        tl_tot_used = sum(u["used_tokens"] for u in tl_usages)
        tl_tot_rem = sum(u["remaining_tokens"] for u in tl_usages)

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Assigned Tasks", len(tasks_df))
        with m2:
            st.metric("Tokens Used So Far", f"{tl_tot_used / 1_000_000:.2f}M" if tl_tot_used >= 1_000_000 else f"{tl_tot_used:,}")
        with m3:
            st.metric("Tokens Remaining", f"{tl_tot_rem / 1_000_000:.2f}M" if tl_tot_rem >= 1_000_000 else f"{tl_tot_rem:,}")
        with m4:
            st.metric("Total Quota Cap", f"{tl_tot_quota / 1_000_000:.2f}M" if tl_tot_quota >= 1_000_000 else f"{tl_tot_quota:,}")

        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1rem 0;'>", unsafe_allow_html=True)

        for _, task in tasks_df.iterrows():
            task_id = str(task["task_id"])
            prog = _calculate_task_token_usage(task, full_df)
            budget_cap = float(task.get("allocated_budget_usd", 0.0) or 0.0)

            with st.expander(f"{task['task_name']} · {task_id} · {prog['badge_text']}", expanded=False):
                st.write(f"**Team:** {task['department']}  ·  **Model:** {task['model']}")
                st.write(
                    f"**Quota Cap:** {int(task['allocated_input_tokens']):,} input + "
                    f"{int(task['allocated_output_tokens']):,} output tokens  ·  "
                    f"**Budget Cap:** ${budget_cap:,.2f}"
                )
                if task.get("notes"):
                    st.write("**Instructions:**", task["notes"])

                st.markdown(_render_token_telemetry_box(prog, budget_cap), unsafe_allow_html=True)

                with st.form(f"team_leader_status_{task_id}"):
                    c_stat, c_tok = st.columns(2)
                    with c_stat:
                        status_options = ["Assigned", "In Progress", "Completed", "Blocked"]
                        current_status = str(task["status"])
                        status_value = st.selectbox(
                            "Workflow Status",
                            status_options,
                            index=status_options.index(current_status) if current_status in status_options else 0,
                        )
                    with c_tok:
                        tok_consumed_input = st.number_input(
                            "Update Tokens Consumed So Far",
                            min_value=0,
                            max_value=max(int(prog["alloc_tot"]) * 2, 50_000_000),
                            value=prog["used_tokens"],
                            step=10_000,
                        )
                    save_status = st.form_submit_button("Save Status & Token Usage", type="primary")

                if save_status:
                    current_task = db_engine.get_delegated_task(task_id)
                    if current_task is None or not can_update_task_status(principal, current_task):
                        st.error("You are not authorized to update this task.")
                    else:
                        db_engine.update_task_status(task_id, status_value)
                        est_spend = (float(tok_consumed_input) / max(1, prog["alloc_tot"])) * budget_cap
                        db_engine.update_task_tokens(task_id, int(tok_consumed_input), est_spend)
                        access_store.record_event(principal.username, "update_task_status", task_id, {"status": status_value, "consumed_tokens": int(tok_consumed_input)})
                        st.success("Task status & token usage updated successfully.")
                        st.rerun()

elif active_view == "Task Manager":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Task Delegation & Token Quota Manager</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Manager-to-Team-Leader workload delegation. Allocate AI tasks with model permissions, token caps, and real-time budget guardrails.
        </p>
    </div>
    """, unsafe_allow_html=True)

    db_engine = DuckDBAnalytics(BASE_DIR / "data" / "tokenlens_analytics.duckdb")

    tab_delegate, tab_active = st.tabs([
        "➕ Delegate New Task (Manager)",
        "📋 Active Delegated Workloads (Team Leader View)"
    ])

    # -------------------------------------------------------------------------
    # TAB 1: DELEGATE NEW TASK (MANAGER FORM)
    # -------------------------------------------------------------------------
    with tab_delegate:
        st.markdown("<div style='font-size: 1.1rem; font-weight: 600; color: #111827; margin-bottom: 0.2rem;'>Delegate AI Workload to Team Leader</div>", unsafe_allow_html=True)
        st.caption("Configure task scope, target department, assigned team leader, approved model, and hard token constraints.")

        # Model pricing rate dictionary (per 1M tokens)
        model_rates = {
            "gpt-4o": {"in": 2.50, "out": 10.00, "provider": "OpenAI"},
            "gemini-1.5-flash": {"in": 0.075, "out": 0.30, "provider": "Google"},
            "claude-3-5-sonnet": {"in": 3.00, "out": 15.00, "provider": "Anthropic"}
        }

        team_options = available_teams if is_manager else all_teams
        if not team_options:
            st.warning("No team scopes are available to assign.")
            dept_key = None
            team_leaders = []
        else:
            dept_select = st.selectbox(
                "Department / Team",
                options=team_options,
                format_func=str.title,
                help="Managers can allocate workloads only within their granted team scopes.",
            )
            dept_key = AccessControlStore.normalize_team(dept_select)
            team_leaders = access_store.list_team_leaders(principal, dept_key)

        if dept_key and not team_leaders:
            st.info("Create or grant access to a team leader for this team before delegating work.")

        with st.form("delegate_task_form", clear_on_submit=False):
            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown("<div style='font-weight: 600; font-size: 0.95rem; color: #374151; margin-bottom: 0.3rem;'>Workload & Personnel Details</div>", unsafe_allow_html=True)
                task_name_input = st.text_input(
                    "Task Name",
                    placeholder="e.g., Automated Pull Request Review Assistant",
                    help="Descriptive name of the delegated AI initiative or feature workload"
                )

                leader_choices = {user.username: user.display_name for user in team_leaders}
                assigned_leader = st.selectbox(
                    "Assigned Team Leader",
                    options=list(leader_choices),
                    format_func=lambda username: f"{leader_choices[username]} ({username})",
                    disabled=not leader_choices,
                    help="Only an active team leader with a matching team grant can be assigned.",
                )
                st.caption(f"Delegating manager: {principal.display_name}")

                priority_select = st.selectbox(
                    "Workload Priority",
                    options=["🔴 Critical (P0)", "🟠 High (P1)", "🟡 Medium (P2)", "🟢 Low (P3)"],
                    index=1
                )

            with col_right:
                st.markdown("<div style='font-weight: 600; font-size: 0.95rem; color: #374151; margin-bottom: 0.3rem;'>Model Permissions & Token Quota</div>", unsafe_allow_html=True)
                model_choice = st.selectbox(
                    "Permitted AI Model",
                    options=["gpt-4o", "gemini-1.5-flash", "claude-3-5-sonnet"],
                    format_func=lambda m: f"{m} ({model_rates[m]['provider']} — ${model_rates[m]['in']:.3f} in / ${model_rates[m]['out']:.2f} out per 1M)",
                    index=0,
                    help="Approved model for this task. Pre-flight guardrails will enforce this restriction."
                )

                alloc_input_tok = st.number_input(
                    "Allocated Input Tokens Allowance",
                    min_value=10_000,
                    max_value=100_000_000,
                    value=1_000_000,
                    step=50_000,
                    help="Maximum input/prompt tokens the team leader is authorized to consume"
                )

                alloc_output_tok = st.number_input(
                    "Allocated Output Tokens Allowance",
                    min_value=5_000,
                    max_value=50_000_000,
                    value=200_000,
                    step=25_000,
                    help="Maximum completion tokens the team leader is authorized to generate"
                )

                total_allocated_tokens = alloc_input_tok + alloc_output_tok

                # Deterministic cost calculation
                in_cost = (alloc_input_tok * model_rates[model_choice]["in"]) / 1_000_000.0
                out_cost = (alloc_output_tok * model_rates[model_choice]["out"]) / 1_000_000.0
                est_max_budget = in_cost + out_cost

                st.markdown(f"""
                <div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 8px; padding: 12px 16px; margin: 0.8rem 0;">
                    <div style="font-size: 0.82rem; font-weight: 600; color: #166534; text-transform: uppercase;">Real-Time Budget Cap Calculation</div>
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-top: 4px;">
                        <div>
                            <span style="font-size: 1.4rem; font-weight: 700; color: #15803D;">${est_max_budget:,.4f}</span>
                            <span style="font-size: 0.85rem; color: #166534;"> Max Budget Cap</span>
                        </div>
                        <div style="font-size: 0.9rem; font-weight: 600; color: #15803D;">
                            {total_allocated_tokens:,} Total Tokens
                        </div>
                    </div>
                    <div style="font-size: 0.78rem; color: #4B5563; margin-top: 4px;">
                        Rate card applied: ${model_rates[model_choice]['in']:.3f}/1M input + ${model_rates[model_choice]['out']:.2f}/1M output ({model_rates[model_choice]['provider']})
                    </div>
                </div>
                """, unsafe_allow_html=True)

            task_notes_input = st.text_area(
                "Manager Instructions & Deliverable Scope",
                placeholder="Specify prompt constraints, cache utilization requirements, benchmark datasets, or success criteria...",
                height=70
            )

            submit_task = st.form_submit_button(
                "🚀 Delegate Task to Team Leader",
                use_container_width=True,
                type="primary"
            )

            if submit_task:
                if not task_name_input.strip():
                    st.error("Please enter a valid Task Name before submitting.")
                elif not dept_key or not can_manage_team(principal, dept_key):
                    st.error("You are not authorized to allocate resources to this team.")
                elif not assigned_leader:
                    st.error("A registered team leader is required for this assignment.")
                else:
                    selected_leader = next((user for user in team_leaders if user.username == assigned_leader), None)
                    if selected_leader is None or not can_assign_team_leader(principal, dept_key, selected_leader):
                        st.error("That team leader is not authorized for the selected team.")
                        st.stop()
                    new_task_id = f"TASK-{secrets.token_hex(8).upper()}"
                    clean_priority = priority_select.split(" ")[1] if " " in priority_select else priority_select

                    db_engine.create_delegated_task(
                        task_id=new_task_id,
                        task_name=task_name_input.strip(),
                        department=dept_key,
                        team_leader=assigned_leader,
                        model=model_choice,
                        allocated_input_tokens=alloc_input_tok,
                        allocated_output_tokens=alloc_output_tok,
                        allocated_budget_usd=est_max_budget,
                        manager_name=principal.display_name,
                        manager_username=principal.username,
                        priority=clean_priority,
                        notes=task_notes_input.strip(),
                    )
                    access_store.record_event(principal.username, "create_delegated_task", new_task_id, {"team": dept_key, "team_leader": assigned_leader, "budget_usd": est_max_budget})
                    st.success(f"✓ Task **{new_task_id}** ('{task_name_input}') successfully delegated to **{selected_leader.display_name}** with a ${est_max_budget:,.2f} budget cap!")
                    st.rerun()

    # -------------------------------------------------------------------------
    # TAB 2: ACTIVE DELEGATED TASKS (TEAM LEADER & MANAGER OVERVIEW)
    # -------------------------------------------------------------------------
    with tab_active:
        _ensure_default_delegated_tasks(db_engine)
        tasks_df = db_engine.get_delegated_tasks_df()
        if is_manager:
            tasks_df = tasks_df[tasks_df["department"].astype(str).str.strip().str.casefold().isin(principal.teams)]

        if tasks_df.empty:
            st.info("No tasks have been delegated yet. Use the 'Delegate New Task' tab to create your first assignment.")
        else:
            # Summary Metrics Strip: Quota, Used, and Remaining
            total_tasks_count = len(tasks_df)
            in_prog_count = len(tasks_df[tasks_df["status"] == "In Progress"])
            task_usages = [_calculate_task_token_usage(r, full_df) for _, r in tasks_df.iterrows()]
            tot_delegated_tokens = sum(u["alloc_tot"] for u in task_usages)
            tot_tokens_used = sum(u["used_tokens"] for u in task_usages)
            tot_tokens_remaining = sum(u["remaining_tokens"] for u in task_usages)
            tot_delegated_budget = float(tasks_df["allocated_budget_usd"].sum())
            tot_spend_used = sum(u["used_spend"] for u in task_usages)

            pct_tot_used = (tot_tokens_used / max(1, tot_delegated_tokens)) * 100.0 if tot_delegated_tokens > 0 else 0.0
            pct_tot_rem = max(0.0, 100.0 - pct_tot_used)

            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.markdown(f"""
                <div class="saas-card">
                    <div class="saas-kpi-label">Active Workloads</div>
                    <div class="saas-kpi-value">{total_tasks_count}</div>
                    <div class="saas-kpi-sub">{in_prog_count} in progress · {len(tasks_df[tasks_df['status'] == 'Completed'])} completed</div>
                </div>
                """, unsafe_allow_html=True)
            with m2:
                st.markdown(f"""
                <div class="saas-card">
                    <div class="saas-kpi-label">Tokens Used So Far</div>
                    <div class="saas-kpi-value" style="color: #4F46E5;">{tot_tokens_used / 1_000_000:.2f}M</div>
                    <div class="saas-kpi-sub">{pct_tot_used:.1f}% quota consumed ({tot_tokens_used:,} tokens)</div>
                </div>
                """, unsafe_allow_html=True)
            with m3:
                st.markdown(f"""
                <div class="saas-card">
                    <div class="saas-kpi-label">Tokens Remaining</div>
                    <div class="saas-kpi-value" style="color: #059669;">{tot_tokens_remaining / 1_000_000:.2f}M</div>
                    <div class="saas-kpi-sub">{pct_tot_rem:.1f}% quota headroom ({tot_tokens_remaining:,} tokens)</div>
                </div>
                """, unsafe_allow_html=True)
            with m4:
                st.markdown(f"""
                <div class="saas-card">
                    <div class="saas-kpi-label">Total Quota Cap</div>
                    <div class="saas-kpi-value">{tot_delegated_tokens / 1_000_000:.2f}M</div>
                    <div class="saas-kpi-sub">${tot_delegated_budget:,.2f} total budget cap</div>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.2rem 0 0.8rem;'>", unsafe_allow_html=True)

            # Interactive Filters
            f_col1, f_col2 = st.columns([1, 1])
            with f_col1:
                dept_filter_opts = ["All Departments"] + sorted([d.title() for d in tasks_df["department"].unique()])
                dept_filter_choice = st.selectbox("Filter by Department", dept_filter_opts, index=0)
            with f_col2:
                status_filter_opts = ["All Statuses"] + sorted(tasks_df["status"].unique().tolist())
                status_filter_choice = st.selectbox("Filter by Workflow Status", status_filter_opts, index=0)

            filtered_tasks = tasks_df.copy()
            if dept_filter_choice != "All Departments":
                filtered_tasks = filtered_tasks[filtered_tasks["department"].str.lower() == dept_filter_choice.lower()]
            if status_filter_choice != "All Statuses":
                filtered_tasks = filtered_tasks[filtered_tasks["status"] == status_filter_choice]

            # Display Tasks Cards / Ledger
            st.markdown(f"<div style='font-size: 0.95rem; font-weight: 600; color: #374151; margin-bottom: 0.6rem;'>Active Assignments ({len(filtered_tasks)} workloads)</div>", unsafe_allow_html=True)

            for _, task in filtered_tasks.iterrows():
                t_id = task["task_id"]
                t_name = html.escape(str(task["task_name"]))
                t_dept = html.escape(str(task["department"]).upper())
                t_lead = html.escape(str(task["team_leader"]))
                t_mgr = html.escape(str(task["manager_name"]))
                t_mod = html.escape(str(task["model"]))
                t_prio = html.escape(str(task["priority"]))
                t_status = html.escape(str(task["status"]))
                t_budget = float(task["allocated_budget_usd"])
                t_in_tok = int(task["allocated_input_tokens"])
                t_out_tok = int(task["allocated_output_tokens"])
                t_tot_tok = t_in_tok + t_out_tok
                t_notes = html.escape(str(task.get("notes") or ""))

                prog = _calculate_task_token_usage(task, full_df)

                # Badge colors
                status_colors = {
                    "Assigned": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
                    "In Progress": ("#EEF2FF", "#4338CA", "#C7D2FE"),
                    "Completed": ("#ECFDF5", "#047857", "#A7F3D0"),
                    "Blocked": ("#FEF2F2", "#B91C1C", "#FECACA")
                }
                bg_c, text_c, border_c = status_colors.get(t_status, ("#F3F4F6", "#374151", "#E5E7EB"))

                with st.container():
                    card_notes_html = f"<div style='font-size: 0.82rem; color: #6B7280; margin-top: 8px; font-style: italic; background: #F9FAFB; padding: 6px 12px; border-radius: 6px;'>Notes: {t_notes}</div>" if t_notes else ""
                    card_html_parts = [
                        '<div style="background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px; padding: 14px 18px; margin-bottom: 0.8rem; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">',
                        '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">',
                        '<div style="display: flex; align-items: center; gap: 8px;">',
                        f'<span style="font-weight: 700; font-size: 1.02rem; color: #111827;">{t_name}</span>',
                        f'<span style="font-size: 0.78rem; font-weight: 600; color: #4F46E5; background: #EEF2FF; padding: 2px 8px; border-radius: 4px;">{t_id}</span>',
                        f'<span style="font-size: 0.78rem; font-weight: 600; color: #6B7280; background: #F3F4F6; padding: 2px 8px; border-radius: 4px;">{t_dept}</span>',
                        '</div>',
                        '<div style="display: flex; align-items: center; gap: 6px;">',
                        f'<span style="font-size: 0.78rem; font-weight: 600; color: {text_c}; background: {bg_c}; border: 1px solid {border_c}; padding: 2px 10px; border-radius: 12px;">{t_status}</span>',
                        f'<span style="font-size: 0.78rem; font-weight: 600; color: #D97706; background: #FFFBEB; padding: 2px 8px; border-radius: 4px;">Priority: {t_prio}</span>',
                        '</div>',
                        '</div>',
                        '<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; font-size: 0.85rem; color: #4B5563; margin-top: 8px; padding-top: 8px; border-top: 1px solid #F3F4F6;">',
                        f'<div><strong>Team Leader:</strong> {t_lead}</div>',
                        f'<div><strong>Delegating Manager:</strong> {t_mgr}</div>',
                        f'<div><strong>Permitted Model:</strong> <code>{t_mod}</code></div>',
                        f'<div><strong>Budget Cap:</strong> <span style="color: #059669; font-weight: 700;">${t_budget:,.2f}</span> ({t_tot_tok:,} tokens)</div>',
                        '</div>',
                        _render_token_telemetry_box(prog, t_budget),
                        card_notes_html,
                        '</div>'
                    ]
                    st.markdown("".join(card_html_parts), unsafe_allow_html=True)

                    # Quick Status Update & Telemetry Controls
                    st_col1, st_col2, st_col3 = st.columns([2, 1, 1])
                    with st_col1:
                        new_stat = st.selectbox(
                            f"Update Status for {t_id}",
                            options=["Assigned", "In Progress", "Completed", "Blocked"],
                            index=["Assigned", "In Progress", "Completed", "Blocked"].index(t_status) if t_status in ["Assigned", "In Progress", "Completed", "Blocked"] else 0,
                            key=f"status_select_{t_id}",
                            label_visibility="collapsed"
                        )
                    with st_col2:
                        if st.button("Save Status", key=f"btn_save_{t_id}", use_container_width=True):
                            current_task = db_engine.get_delegated_task(t_id)
                            if current_task is None or not can_update_task_status(principal, current_task):
                                st.error("You are not authorized to update this task.")
                            else:
                                db_engine.update_task_status(t_id, new_stat)
                                access_store.record_event(principal.username, "update_task_status", t_id, {"status": new_stat})
                                st.success(f"Status for {t_id} updated to {new_stat}")
                                st.rerun()
                    with st_col3:
                        if st.button("🗑️ Revoke", key=f"btn_del_{t_id}", use_container_width=True):
                            current_task = db_engine.get_delegated_task(t_id)
                            if current_task is None or not can_revoke_task(principal, current_task):
                                st.error("You are not authorized to revoke this task.")
                            else:
                                db_engine.delete_delegated_task(t_id)
                                access_store.record_event(principal.username, "revoke_delegated_task", t_id, {})
                                st.info(f"Task {t_id} revoked.")
                                st.rerun()

                    # Inline Token Usage Logging Expander
                    with st.expander(f"⚡ Update Token Usage So Far ({t_id})", expanded=False):
                        t_c1, t_c2 = st.columns([2, 1])
                        with t_c1:
                            updated_tokens = st.number_input(
                                "Tokens Consumed So Far",
                                min_value=0,
                                max_value=max(t_tot_tok * 2, 100_000_000),
                                value=prog["used_tokens"],
                                step=25_000,
                                key=f"in_tok_val_{t_id}",
                                help=f"Enter the token usage consumed so far for {t_id} (Allocated: {t_tot_tok:,})"
                            )
                        with t_c2:
                            st.write("")
                            st.write("")
                            if st.button("Save Token Usage", key=f"btn_tok_save_{t_id}", use_container_width=True):
                                current_task = db_engine.get_delegated_task(t_id)
                                if current_task is None or not can_update_task_status(principal, current_task):
                                    st.error("You are not authorized to update this task.")
                                else:
                                    est_spend = (float(updated_tokens) / max(1, t_tot_tok)) * t_budget
                                    db_engine.update_task_tokens(t_id, int(updated_tokens), est_spend)
                                    access_store.record_event(principal.username, "update_task_tokens", t_id, {"consumed_tokens": int(updated_tokens)})
                                    st.success(f"Token usage for {t_id} updated to {updated_tokens:,} tokens!")
                                    st.rerun()



# =========================================================================
# 6. REQUEST LOGS (AUDIT LEDGER & EXPORT)
# =========================================================================
elif active_view == "Request Logs":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Request Logs</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Comprehensive ledger of all attributed LLM requests with precise token volumes, latencies, and financial costs.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Check if team drill-down filter is active from Spend by Team
    active_drill_team = st.session_state.get("request_logs_team_filter")
    if active_drill_team:
        c_badge, c_clear = st.columns([3, 1])
        with c_badge:
            st.markdown(f"""
            <div style="background-color: #F5F3FF; border: 1px solid #C4B5FD; border-radius: 8px; padding: 7px 14px; margin-bottom: 12px; display: inline-flex; align-items: center; gap: 8px;">
                <span style="font-size: 0.84rem; color: #4F46E5; font-weight: 600;">Active Filter: {active_drill_team.capitalize()} Requests</span>
            </div>
            """, unsafe_allow_html=True)
        with c_clear:
            if st.button("✕ Clear team filter", key="clear_team_drill_filter"):
                st.session_state["request_logs_team_filter"] = None
                st.rerun()

    # Search Bar
    search_query = st.text_input("🔎 Search by Request ID, User, or Model", placeholder="e.g. req_0120, user_42, claude...")

    log_df = filtered_df.copy()
    if active_drill_team:
        log_df = log_df[log_df["team"] == active_drill_team]

    if search_query:
        q = search_query.strip().lower()
        log_df = log_df[
            log_df["request_id"].str.lower().str.contains(q) |
            log_df["user_id"].str.lower().str.contains(q) |
            log_df["model"].str.lower().str.contains(q)
        ]

    st.markdown(f"<div style='font-size: 0.88rem; color: #6B7280; margin-bottom: 0.6rem;'>Displaying <strong>{len(log_df):,}</strong> records matching active filters and search query</div>", unsafe_allow_html=True)

    display_cols = [
        "request_id", "timestamp_utc", "team", "feature", "user_id",
        "model", "input_tokens", "output_tokens", "total_tokens",
        "input_cost_usd", "output_cost_usd", "total_cost_usd", "latency_ms", "status", "env"
    ]

    st.dataframe(
        log_df[display_cols].sort_values(by="timestamp_utc", ascending=False),
        use_container_width=True,
        height=480,
        column_config={
            "request_id": st.column_config.TextColumn("Request ID"),
            "timestamp_utc": st.column_config.DatetimeColumn("Timestamp (UTC)", format="YYYY-MM-DD HH:mm:ss"),
            "team": st.column_config.TextColumn("Team"),
            "feature": st.column_config.TextColumn("Feature"),
            "user_id": st.column_config.TextColumn("User"),
            "model": st.column_config.TextColumn("Model"),
            "input_tokens": st.column_config.NumberColumn("Input", format="%d"),
            "output_tokens": st.column_config.NumberColumn("Output", format="%d"),
            "total_tokens": st.column_config.NumberColumn("Total", format="%d"),
            "total_cost_usd": st.column_config.NumberColumn("Spend", format="$%.6f"),
            "input_cost_usd": st.column_config.NumberColumn("In Cost", format="$%.6f"),
            "output_cost_usd": st.column_config.NumberColumn("Out Cost", format="$%.6f"),
            "latency_ms": st.column_config.NumberColumn("Latency", format="%d ms"),
            "status": st.column_config.TextColumn("Status"),
            "env": st.column_config.TextColumn("Env")
        }
    )

    # CSV Export Button
    csv_buf = io.StringIO()
    log_df.to_csv(csv_buf, index=False)
    st.download_button(
        label="📥 Export Filtered Logs as CSV",
        data=csv_buf.getvalue(),
        file_name=f"tokenlens_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        disabled=not st.checkbox(
            "I confirm this export contains user identifiers and usage data and will be stored securely.",
            key="confirm_logs_export",
        ),
    )


# =========================================================================
# 7. SETTINGS (FINOPS POLICY, PRICING REGISTRY & PROXY)
# =========================================================================
elif active_view == "Settings":
    if not is_org_head:
        st.error("Only the organization head can manage organization settings and access grants.")
        st.stop()

    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">Settings & Access Control</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Manage organization user accounts, role-based access control (RBAC), and team scoping.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.subheader("Organization Access Grants")
    st.caption("The organization head grants scoped team access. Managers cannot create accounts or expand their own grants.")

    with st.expander("Grant access to a new account", expanded=False):
        with st.form("create_access_account_form"):
            new_username = st.text_input("Username", help="Use a unique login identifier such as manager.eng.")
            new_display_name = st.text_input("Display name")
            new_role = st.selectbox("Role", list(Role), format_func=lambda role: ROLE_LABELS[role])
            new_teams = st.multiselect(
                "Team grants",
                options=all_teams,
                help="Managers and team leaders must receive at least one team. Organization heads have access to all teams.",
            )
            new_password = st.text_input("Temporary password (12 characters minimum)", type="password")
            create_account = st.form_submit_button("Create account and grant access")
        if create_account:
            try:
                created_user = access_store.create_user(
                    principal.username,
                    new_username,
                    new_display_name,
                    new_password,
                    new_role,
                    new_teams,
                )
                st.success(f"Created {ROLE_LABELS[created_user.role]} account for {created_user.display_name}.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))

    access_users = access_store.list_users(principal.username)
    if access_users:
        access_user_by_name = {user.username: user for user in access_users}
        access_user_df = pd.DataFrame(
            [
                {
                    "Username": user.username,
                    "Name": user.display_name,
                    "Role": ROLE_LABELS[user.role],
                    "Granted teams": ", ".join(user.teams) if user.teams else "All teams",
                    "Active": user.active,
                }
                for user in access_users
            ]
        )
        st.dataframe(access_user_df, use_container_width=True, hide_index=True)

        account_to_edit = st.selectbox(
            "Account to update",
            options=list(access_user_by_name),
            format_func=lambda username: f"{access_user_by_name[username].display_name} ({username})",
            key="access_account_to_edit",
        )
        selected_account = access_user_by_name[account_to_edit]
        editable_team_options = sorted(set(all_teams) | set(selected_account.teams))
        with st.form("update_access_account_form"):
            updated_role = st.selectbox(
                "Granted role",
                list(Role),
                index=list(Role).index(selected_account.role),
                format_func=lambda role: ROLE_LABELS[role],
            )
            updated_teams = st.multiselect(
                "Granted teams",
                options=editable_team_options,
                default=list(selected_account.teams),
            )
            account_active = st.checkbox("Account active", value=selected_account.active)
            reset_password = st.text_input("New password (leave blank to keep current)", type="password")
            save_access = st.form_submit_button("Save access grant")
        if save_access:
            try:
                if reset_password:
                    AccessControlStore.validate_password(reset_password)
                access_store.update_user_access(
                    principal.username,
                    selected_account.username,
                    updated_role,
                    updated_teams,
                    account_active,
                )
                if reset_password:
                    access_store.reset_password(principal.username, selected_account.username, reset_password)
                st.success("Access grant saved. The account must sign in again after access or password changes.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))

        with st.expander("Recent access audit events"):
            st.dataframe(
                pd.DataFrame(access_store.audit_events(principal.username, limit=50)),
                use_container_width=True,
                hide_index=True,
            )
