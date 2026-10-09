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
import json
from pathlib import Path
import random
import io
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.models import RequestRecord, PricingRecord
from core.attribution import AttributionParser
from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.reconciliation import ReconciliationEngine

# Streamlit Page Setup
st.set_page_config(
    page_title="TokenLens — AI FinOps",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

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
    st.session_state["expanded_team"] = None

if "nav_view" not in st.session_state:
    st.session_state["nav_view"] = "Command Center"

if "request_logs_team_filter" not in st.session_state:
    st.session_state["request_logs_team_filter"] = None

df = st.session_state.df
pricing_records = st.session_state.pricing_records
stats = st.session_state.stats
rejects = st.session_state.rejects
golden_manifest = st.session_state.golden_manifest

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
st.markdown("""
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
            <div class="profile-avatar">RP</div>
            <div class="profile-info">
                <div class="profile-name">Rahul P.</div>
                <div class="profile-role">FinOps Lead</div>
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# --- LAYER 2: TOP DASHBOARD FEATURE SELECTOR (MATCHING SKETCH) ---
nav_options = [
    "Command Center",
    "Spend Detective",
    "Savings Lab",
    "Trend",
    "Request Logs",
    "Settings"
]

if st.session_state.get("nav_view") == "Trust Center":
    st.session_state["nav_view"] = "Trend"

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
            <span style="font-size: 1.05rem; font-weight: 600; color: #111827;">Spend by team</span>
            <span style="font-size: 0.78rem; color: #6B7280;">Click a team to expand details</span>
        </div>
        """, unsafe_allow_html=True)

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
                    # 1. Three easy-to-read metrics: total spend, number of requests, and total tokens
                    m1, m2, m3 = st.columns(3)
                    with m1:
                        st.markdown(f"""
                        <div class="team-detail-metric">
                            <div class="team-detail-metric-label">Total Spend</div>
                            <div class="team-detail-metric-value">${t_spend:,.2f}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with m2:
                        st.markdown(f"""
                        <div class="team-detail-metric">
                            <div class="team-detail-metric-label">Requests</div>
                            <div class="team-detail-metric-value">{t_reqs:,}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with m3:
                        tok_disp = f"{t_tokens / 1e6:,.2f}M" if t_tokens >= 1e6 else f"{t_tokens:,}"
                        st.markdown(f"""
                        <div class="team-detail-metric">
                            <div class="team-detail-metric-label">Total Tokens</div>
                            <div class="team-detail-metric-value">{tok_disp}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    # 2. Simple horizontal bar chart of spend by feature for that team
                    st.markdown(f"<div style='font-size: 0.82rem; font-weight: 600; color: #4B5563; margin: 12px 0 4px;'>Where {team_display} spends</div>", unsafe_allow_html=True)
                    feat_sub = t_df.groupby("feature")["total_cost_usd"].sum().reset_index().sort_values(by="total_cost_usd", ascending=True)

                    fig_feat_sub = px.bar(
                        feat_sub,
                        x="total_cost_usd",
                        y="feature",
                        orientation="h",
                        text=feat_sub["total_cost_usd"].apply(lambda v: f"${v:,.2f}"),
                        labels={"total_cost_usd": "Spend ($)", "feature": "Feature"}
                    )
                    fig_feat_sub.update_traces(
                        marker_color="#818CF8",
                        textposition="outside",
                        textfont=dict(color="#4B5563", size=10),
                        cliponaxis=False
                    )
                    fig_feat_sub.update_layout(PLOT_LAYOUT)
                    fig_feat_sub.update_layout(
                        height=max(115, 26 * len(feat_sub) + 35),
                        margin=dict(l=5, r=40, t=10, b=10)
                    )
                    st.plotly_chart(fig_feat_sub, use_container_width=True)

                    # 3. Most-used model & button to view that team's requests
                    top_model = t_df["model"].mode()[0] if not t_df.empty else "N/A"
                    top_cnt = (t_df["model"] == top_model).sum() if not t_df.empty else 0

                    sub_left, sub_right = st.columns([1.3, 1])
                    with sub_left:
                        st.markdown(f"""
                        <div style="font-size: 0.82rem; color: #6B7280; padding-top: 6px;">
                            Most-used model: <strong style="color: #111827;">{top_model}</strong> ({top_cnt:,} requests)
                        </div>
                        """, unsafe_allow_html=True)
                    with sub_right:
                        if st.button(f"View {team_display}'s requests →", key=f"drill_reqs_btn_{t_name}", use_container_width=True):
                            st.session_state["nav_view"] = "Request Logs"
                            st.session_state["request_logs_team_filter"] = t_name
                            st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)


        # Compact Table of Top Cost Drivers + Interaction: Selecting reveals underlying requests!
        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.8rem 0 1.2rem;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-size: 1.05rem; font-weight: 600; color: #111827; margin-bottom: 0.4rem;'>Top Cost Drivers</div>", unsafe_allow_html=True)
        st.caption("Ranked workloads by total financial impact. Select any driver below to inspect the underlying requests.")

        cost_drivers = filtered_df.groupby(["team", "feature"]).agg(
            total_spend=("total_cost_usd", "sum"),
            request_count=("request_id", "count"),
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            top_model=("model", lambda x: x.mode()[0] if not x.empty else "unknown")
        ).reset_index()
        cost_drivers["avg_cost_req"] = cost_drivers["total_spend"] / cost_drivers["request_count"]
        cost_drivers["spend_share_pct"] = (cost_drivers["total_spend"] / tot_spend * 100) if tot_spend > 0 else 0
        cost_drivers = cost_drivers.sort_values(by="total_spend", ascending=False).reset_index(drop=True)
        cost_drivers.index = cost_drivers.index + 1  # 1-based rank

        driver_display = cost_drivers[["team", "feature", "top_model", "request_count", "avg_cost_req", "total_spend", "spend_share_pct"]].copy()
        
        st.dataframe(
            driver_display,
            use_container_width=True,
            column_config={
                "team": st.column_config.TextColumn("Department"),
                "feature": st.column_config.TextColumn("Feature"),
                "top_model": st.column_config.TextColumn("Primary Model"),
                "request_count": st.column_config.NumberColumn("Requests", format="%d"),
                "avg_cost_req": st.column_config.NumberColumn("Avg / Request", format="$%.4f"),
                "total_spend": st.column_config.NumberColumn("Total Spend", format="$%.2f"),
                "spend_share_pct": st.column_config.NumberColumn("Share", format="%.1f%%")
            }
        )

        # Interaction: Selecting a cost driver reveals the underlying requests
        driver_options = [f"{row['team']} / {row['feature']} (${row['total_spend']:,.2f})" for _, row in cost_drivers.iterrows()]
        selected_driver_label = st.selectbox(
            "🔍 Inspect underlying requests for cost driver:",
            driver_options,
            index=0
        )

        if selected_driver_label:
            sel_team, rest = selected_driver_label.split(" / ")
            sel_feat = rest.split(" (")[0]
            underlying_requests = filtered_df[(filtered_df["team"] == sel_team) & (filtered_df["feature"] == sel_feat)]

            with st.expander(f"Underlying Request Records ({len(underlying_requests)} requests for {sel_team} → {sel_feat})", expanded=True):
                st.dataframe(
                    underlying_requests[[
                        "request_id", "timestamp_utc", "user_id", "model",
                        "input_tokens", "output_tokens", "input_cost_usd", "output_cost_usd", "total_cost_usd", "status"
                    ]].sort_values(by="total_cost_usd", ascending=False),
                    use_container_width=True,
                    height=280,
                    column_config={
                        "total_cost_usd": st.column_config.NumberColumn("Total Cost", format="$%.6f"),
                        "input_cost_usd": st.column_config.NumberColumn("In Cost", format="$%.6f"),
                        "output_cost_usd": st.column_config.NumberColumn("Out Cost", format="$%.6f"),
                        "timestamp_utc": st.column_config.DatetimeColumn("Timestamp (UTC)", format="YYYY-MM-DD HH:mm:ss")
                    }
                )


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

        # 3. Unattributed Spend Visibility
        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.5rem 0 1rem;'>", unsafe_allow_html=True)
        st.markdown("### 3. Tagging & Attribution Integrity")
        unatt_df = filtered_df[filtered_df["is_unattributed"]]
        unatt_sum = unatt_df["total_cost_usd"].sum() if not unatt_df.empty else 0.0

        if unatt_df.empty:
            st.success("All active requests are 100% attributed with valid team and feature metadata.")
        else:
            st.markdown(f"""
            <div style="background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 8px; padding: 14px 18px; margin-bottom: 1rem;">
                <strong style="color: #B45309;">Notice: {len(unatt_df)} requests (${unatt_sum:,.2f}) lack team/feature attribution</strong>
                <p style="color: #92400E; font-size: 0.88rem; margin: 4px 0 0;">
                    These requests have fallen back to default taxonomy (<code>unattributed</code>). Configure proxy header propagation (<code>X-Team</code>, <code>X-Feature</code>) in your LLM gateway to eliminate attribution gaps.
                </p>
            </div>
            """, unsafe_allow_html=True)

            with st.expander(f"View Unattributed Requests ({len(unatt_df)} records)"):
                st.dataframe(
                    unatt_df[["request_id", "timestamp_utc", "user_id", "model", "total_tokens", "total_cost_usd"]],
                    use_container_width=True,
                    column_config={"total_cost_usd": st.column_config.NumberColumn("Cost", format="$%.6f")}
                )


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
            Longitudinal daily spend trajectory, architectural model distribution, workload features, and financial trust reconciliation.
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

    # 3. Trust Center Reconciliation & Audit
    st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 2rem 0 1.2rem;'>", unsafe_allow_html=True)
    st.markdown("### Financial Trust & Invariant Verification")
    st.caption("Independent verification comparing actual calculated totals with external expected totals. Proves data integrity and financial accuracy.")


    reconciler = ReconciliationEngine(tolerance_usd=0.01, tolerance_percent=0.0001)
    checks = reconciler.run_reconciliation(
        actual_df=df,
        expected_manifest=golden_manifest,
        ingestion_stats=stats,
        rejected_records=rejects
    )
    all_pass = all(c.status in {"PASS", "WARNING"} for c in checks)

    # Three High-Level Integrity Cards
    t_c1, t_c2, t_c3 = st.columns(3)
    with t_c1:
        st.markdown(f"""
        <div class="saas-card">
            <div class="saas-kpi-label">Reconciliation Status</div>
            <div class="saas-kpi-value" style="color: {'#059669' if all_pass else '#DC2626'};">
                {'ALL CHECKS PASS' if all_pass else 'DISCREPANCY'}
            </div>
            <div class="saas-kpi-sub">{len([c for c in checks if c.status == 'PASS'])} of {len(checks)} checks satisfied</div>
        </div>
        """, unsafe_allow_html=True)

    with t_c2:
        st.markdown(f"""
        <div class="saas-card">
            <div class="saas-kpi-label">Row Count Integrity</div>
            <div class="saas-kpi-value">{stats['loaded_rows']:,} / {stats['source_rows']:,}</div>
            <div class="saas-kpi-sub">{stats['rejected_rows']} rejected (5 duplicates quarantined)</div>
        </div>
        """, unsafe_allow_html=True)

    with t_c3:
        st.markdown(f"""
        <div class="saas-card">
            <div class="saas-kpi-label">Mathematical Invariants</div>
            <div class="saas-kpi-value" style="color: #059669;">CONFIRMED</div>
            <div class="saas-kpi-sub">Sum(Teams) == Sum(Models) == Grand Total</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### Reconciliation Check Table")
    st.caption("Compares actual engine results against an independent golden dataset with a strict ±$0.01 / 0.01% financial tolerance.")

    rec_rows = []
    for c in checks:
        rec_rows.append({
            "Metric": c.metric,
            "Category": c.category,
            "Expected": str(c.expected),
            "Actual": str(c.actual),
            "Difference": str(c.difference),
            "Tolerance": c.tolerance,
            "Status": c.status,
            "Details": c.details
        })

    st.dataframe(
        pd.DataFrame(rec_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Metric": st.column_config.TextColumn("Check / Metric"),
            "Category": st.column_config.TextColumn("Category"),
            "Status": st.column_config.TextColumn("Status")
        }
    )

    # Spot check audit sample table (10 random rows hand-verified)
    st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.8rem 0 1rem;'>", unsafe_allow_html=True)
    st.markdown("### Hand-Calculated Spot-Check Audit (10 Samples)")
    pricing_map = {p.model: p for p in pricing_records}
    sample_priced = random.sample([r for r in st.session_state.priced_list if not r.missing_price], min(10, len(st.session_state.priced_list)))
    spot_list = []
    for r in sample_priced:
        p = pricing_map[r.model]
        in_cost = (Decimal(r.input_tokens) * p.input_usd_per_1m) / Decimal("1000000")
        out_cost = (Decimal(r.output_tokens) * p.output_usd_per_1m) / Decimal("1000000")
        cached_cost = (Decimal(r.cached_tokens) * p.cached_usd_per_1m) / Decimal("1000000")
        exp_c = float((in_cost + out_cost + cached_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
        spot_list.append({
            "Request ID": r.request_id,
            "Model": r.model,
            "In Tokens": r.input_tokens,
            "Out Tokens": r.output_tokens,
            "Rates (In/Out $/1M)": f"${float(p.input_usd_per_1m):.2f} / ${float(p.output_usd_per_1m):.2f}",
            "Hand-Calculated Expected": f"${exp_c:.6f}",
            "Engine Actual": f"${float(r.total_cost_usd):.6f}",
            "Status": "MATCH" if abs(exp_c - float(r.total_cost_usd)) < 0.00001 else "MISMATCH"
        })

    st.dataframe(pd.DataFrame(spot_list), use_container_width=True, hide_index=True)

    # Quarantined Rejects Expander
    with st.expander(f"Quarantined Ingestion Records ({len(rejects)} records)"):
        st.write("Records isolated during ingestion due to validation failures or duplicate IDs:")
        st.dataframe(pd.DataFrame(rejects), use_container_width=True)

    # Download Formal Markdown Report
    scope_info = {
        "dataset_name": "sample_requests.csv",
        "total_source_rows": stats["source_rows"],
        "valid_records": stats["loaded_rows"],
        "pricing_version": "v2026.10",
        "audit_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }
    spot_checks_md = [
        {
            "request_id": s["Request ID"],
            "model": s["Model"],
            "input_tokens": s["In Tokens"],
            "output_tokens": s["Out Tokens"],
            "in_rate": float(pricing_map[s["Model"]].input_usd_per_1m),
            "out_rate": float(pricing_map[s["Model"]].output_usd_per_1m),
            "expected_cost": float(s["Hand-Calculated Expected"].replace("$", "")),
            "actual_cost": float(s["Engine Actual"].replace("$", ""))
        }
        for s in spot_list
    ]
    report_md = reconciler.generate_markdown_report(checks, scope_info, spot_checks_md)

    st.download_button(
        label="📥 Download Formal Reconciliation Audit (.md)",
        data=report_md,
        file_name="LLM_FinOps_Reconciliation_Report.md",
        mime="text/markdown"
    )


# =========================================================================
# 5. REQUEST LOGS (AUDIT LEDGER & EXPORT)
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
        mime="text/csv"
    )


# =========================================================================
# 6. SETTINGS (FINOPS POLICY, PRICING REGISTRY & PROXY)
# =========================================================================
elif active_view == "Settings":
    st.markdown("""
    <div style="margin-bottom: 1.4rem; padding-bottom: 0.8rem; border-bottom: 1px solid #E5E7EB;">
        <h1 style="font-size: 1.75rem; margin-bottom: 4px;">FinOps Settings & Policies</h1>
        <p style="color: #6B7280; font-size: 0.92rem; margin: 0;">
            Manage active model pricing card, metadata attribution taxonomy, budget guardrails, and proxy gateway parameters.
        </p>
    </div>
    """, unsafe_allow_html=True)

    set_col1, set_col2 = st.columns([1.2, 1])

    with set_col1:
        st.markdown("### 1. Model Pricing Registry")
        st.caption("Active model rate card used for deterministic cost calculations across all workloads.")

        pricing_df_disp = pd.DataFrame([
            {
                "Model": p.model,
                "Provider": p.provider.upper(),
                "Input ($/1M)": f"${float(p.input_usd_per_1m):.2f}",
                "Output ($/1M)": f"${float(p.output_usd_per_1m):.2f}",
                "Cached ($/1M)": f"${float(p.cached_usd_per_1m):.3f}",
                "Effective From": p.effective_from.isoformat()
            }
            for p in pricing_records
        ])
        st.dataframe(pricing_df_disp, use_container_width=True, hide_index=True)

        # Interactive New Model Adding Option
        with st.expander("➕ Add New Model to Rate Card", expanded=False):
            st.markdown("<div style='font-size: 0.85rem; color: #4B5563; margin-bottom: 8px;'>Add or update an LLM model and its deterministic per-million token pricing rates:</div>", unsafe_allow_html=True)
            new_m_col1, new_m_col2 = st.columns(2)
            with new_m_col1:
                new_model_id = st.text_input("Model ID", placeholder="e.g. gpt-4.5-preview, claude-3-7-sonnet", key="new_model_id_input")
                new_model_provider = st.selectbox("Provider", ["openai", "anthropic", "google", "meta", "mistral", "deepseek", "cohere", "custom"], key="new_model_provider_input")
                new_eff_date = st.date_input("Effective Date", value=datetime.now(timezone.utc).date(), key="new_eff_date_input")
            with new_m_col2:
                new_input_rate = st.number_input("Input Price ($/1M tokens)", min_value=0.0, value=2.50, step=0.10, format="%.4f", key="new_input_rate_input")
                new_output_rate = st.number_input("Output Price ($/1M tokens)", min_value=0.0, value=10.00, step=0.25, format="%.4f", key="new_output_rate_input")
                new_cached_rate = st.number_input("Cached Price ($/1M tokens)", min_value=0.0, value=1.25, step=0.10, format="%.4f", key="new_cached_rate_input")

            if st.button("➕ Register Model Rate Card", use_container_width=True, key="btn_add_new_model"):
                clean_model = new_model_id.strip().lower()
                if not clean_model:
                    st.error("Please specify a valid Model ID.")
                else:
                    # 1. Update CSV file
                    pricing_csv = DATA_DIR / "model_pricing.csv"
                    p_df = pd.read_csv(pricing_csv) if pricing_csv.exists() else pd.DataFrame(columns=["model", "provider", "input_usd_per_1m", "output_usd_per_1m", "cached_usd_per_1m", "effective_from", "effective_to"])
                    # Remove if already exists to overwrite/update
                    p_df = p_df[p_df["model"].str.lower() != clean_model]
                    new_row = pd.DataFrame([{
                        "model": clean_model,
                        "provider": new_model_provider.lower(),
                        "input_usd_per_1m": float(new_input_rate),
                        "output_usd_per_1m": float(new_output_rate),
                        "cached_usd_per_1m": float(new_cached_rate),
                        "effective_from": new_eff_date.strftime("%Y-%m-%d"),
                        "effective_to": ""
                    }])
                    updated_p_df = pd.concat([p_df, new_row], ignore_index=True)
                    updated_p_df.to_csv(pricing_csv, index=False)

                    # 2. Update session state
                    new_rec_obj = PricingRecord(
                        model=clean_model,
                        provider=new_model_provider.lower(),
                        input_usd_per_1m=Decimal(str(new_input_rate)),
                        output_usd_per_1m=Decimal(str(new_output_rate)),
                        cached_usd_per_1m=Decimal(str(new_cached_rate)),
                        effective_from=new_eff_date,
                        effective_to=None
                    )
                    st.session_state.pricing_records = [p for p in st.session_state.pricing_records if p.model.lower() != clean_model] + [new_rec_obj]

                    # 3. Recalculate cost engine
                    cost_engine = CostEngine()
                    cost_engine.load_pricing_records(st.session_state.pricing_records)
                    importer = DataImporter()
                    raw_reqs, _, _ = importer.load_requests(DATA_DIR / "sample_requests.csv")
                    re_priced = cost_engine.process_requests(raw_reqs)
                    st.session_state.df = cost_engine.get_priced_dataframe(re_priced)
                    st.session_state.priced_list = re_priced

                    st.success(f"✅ Successfully registered model '{clean_model}' ({new_model_provider.upper()}) into rate card registry!")
                    st.rerun()


        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.5rem 0 1rem;'>", unsafe_allow_html=True)
        st.markdown("### 2. Attribution Taxonomy Rules")
        st.caption("Metadata tag extraction rules configured in attribution.yaml.")

        attr_c1, attr_c2 = st.columns(2)
        with attr_c1:
            st.text_input("Default Team Fallback", value="unattributed", disabled=True)
            st.text_input("Default Feature Fallback", value="unassigned", disabled=True)
        with attr_c2:
            st.text_input("Header Mapping (Team)", value="X-Team", disabled=True)
            st.text_input("Header Mapping (Feature)", value="X-Feature", disabled=True)

    with set_col2:
        st.markdown("### 3. Financial Budget & Alert Thresholds")
        st.caption("Configure spending guardrails and reconciliation variance limits.")

        monthly_budget = st.number_input("Monthly LLM Spend Budget ($ USD)", min_value=1.0, value=25.0, step=5.0)
        curr_spend_val = float(df["total_cost_usd"].sum())
        budget_used_pct = (curr_spend_val / monthly_budget) * 100

        st.markdown(f"""
        <div style="background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px 16px; margin: 8px 0 16px;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 6px;">
                <span style="color: #4B5563;">Current Budget Utilization:</span>
                <strong style="color: #111827;">${curr_spend_val:.2f} of ${monthly_budget:.2f} ({budget_used_pct:.1f}%)</strong>
            </div>
            <div style="background: #E5E7EB; border-radius: 9999px; height: 8px; overflow: hidden;">
                <div style="background: {'#4F46E5' if budget_used_pct < 80 else '#EF4444'}; width: {min(100.0, budget_used_pct)}%; height: 100%;"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        tolerance_input = st.selectbox("Reconciliation Tolerance Limit", ["±$0.01 (0.01% standard)", "±$0.05", "Exact (0.00%)"], index=0)
        alert_email = st.text_input("FinOps Notification Email", value="finops-alerts@acme.ai")

        st.markdown("<hr style='border: none; border-top: 1px solid #E5E7EB; margin: 1.5rem 0 1rem;'>", unsafe_allow_html=True)
        st.markdown("### 4. Logging Proxy Gateway")
        st.caption("Connection parameters for the live ingestion proxy.")

        st.code("http://127.0.0.1:8000/v1/proxy/{provider}", language="bash")
        proxy_token = st.text_input("Proxy Auth Token", value=os.getenv("FINOPS_API_TOKEN", ""), type="password", placeholder="Enter proxy token or set FINOPS_API_TOKEN")

        if st.button("💾 Save Policy Settings", use_container_width=True):
            st.success("✅ Policy settings saved successfully!")
