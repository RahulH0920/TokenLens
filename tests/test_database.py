"""Unit tests for DuckDB analytical database engine and graphical views."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from core.database import DuckDBAnalytics
from core.models import PricedRequest


@pytest.fixture
def test_db():
    """In-memory DuckDB analytics instance for isolated testing."""
    db = DuckDBAnalytics(in_memory=True)
    yield db
    db.close()


def test_schema_initialization(test_db):
    """Verify tables, indexes, and views are initialized."""
    tables = [r[0] for r in test_db.conn.execute("SHOW TABLES").fetchall()]
    assert "model_pricing" in tables
    assert "llm_requests" in tables
    assert "view_kpi_metrics" in tables
    assert "view_team_spend_share" in tables
    assert "view_feature_breakdown" in tables
    assert "view_daily_spend_trend" in tables
    assert "view_model_distribution" in tables
    assert "view_team_model_heatmap" in tables
    assert "view_user_leaderboard" in tables


def test_insert_and_graphical_kpis(test_db):
    """Verify request insertion and KPI computation."""
    reqs = [
        PricedRequest(
            request_id="req-1",
            timestamp_utc=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
            team="engineering",
            feature="code-review",
            user_id="user-1",
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=200,
            cached_tokens=0,
            status="success",
            latency_ms=250,
            env="production",
            source="test",
            input_cost_usd=Decimal("0.002500"),
            output_cost_usd=Decimal("0.002000"),
            cached_cost_usd=Decimal("0.000000"),
            total_cost_usd=Decimal("0.004500"),
            missing_price=False,
            is_unattributed=False,
        ),
        PricedRequest(
            request_id="req-2",
            timestamp_utc=datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc),
            team="product",
            feature="chat",
            user_id="user-2",
            provider="google",
            model="gemini-1.5-flash",
            input_tokens=2000,
            output_tokens=500,
            cached_tokens=0,
            status="success",
            latency_ms=180,
            env="production",
            source="test",
            input_cost_usd=Decimal("0.000150"),
            output_cost_usd=Decimal("0.000150"),
            cached_cost_usd=Decimal("0.000000"),
            total_cost_usd=Decimal("0.000300"),
            missing_price=False,
            is_unattributed=False,
        ),
    ]

    inserted = test_db.insert_priced_requests(reqs)
    assert inserted == 2

    kpi = test_db.get_kpi_summary()
    assert kpi["total_requests"] == 2
    assert float(kpi["total_spend_usd"]) == pytest.approx(0.0048, rel=1e-4)
    assert kpi["total_tokens"] == 3700
    assert kpi["active_teams"] == 2
    assert kpi["active_models"] == 2


def test_team_spend_share_view(test_db):
    """Verify team spend share and percentage calculation for Pie charts."""
    reqs = [
        PricedRequest(
            request_id="r1",
            timestamp_utc=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
            team="engineering",
            feature="code-review",
            user_id="u1",
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=200,
            cached_tokens=0,
            status="success",
            latency_ms=250,
            env="production",
            source="test",
            input_cost_usd=Decimal("0.007500"),
            output_cost_usd=Decimal("0.000000"),
            cached_cost_usd=Decimal("0.000000"),
            total_cost_usd=Decimal("0.007500"),
            missing_price=False,
            is_unattributed=False,
        ),
        PricedRequest(
            request_id="r2",
            timestamp_utc=datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc),
            team="marketing",
            feature="copywriting",
            user_id="u2",
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=200,
            cached_tokens=0,
            status="success",
            latency_ms=200,
            env="production",
            source="test",
            input_cost_usd=Decimal("0.002500"),
            output_cost_usd=Decimal("0.000000"),
            cached_cost_usd=Decimal("0.000000"),
            total_cost_usd=Decimal("0.002500"),
            missing_price=False,
            is_unattributed=False,
        ),
    ]
    test_db.insert_priced_requests(reqs)

    team_df = test_db.get_team_spend_df()
    assert len(team_df) == 2
    eng_row = team_df[team_df["team"] == "engineering"].iloc[0]
    mkt_row = team_df[team_df["team"] == "marketing"].iloc[0]

    assert float(eng_row["total_spend_usd"]) == pytest.approx(0.0075, rel=1e-4)
    assert float(eng_row["spend_percentage"]) == pytest.approx(75.0, rel=1e-2)
    assert float(mkt_row["total_spend_usd"]) == pytest.approx(0.0025, rel=1e-4)
    assert float(mkt_row["spend_percentage"]) == pytest.approx(25.0, rel=1e-2)


def test_postgres_ddl_generation():
    """Verify PostgreSQL DDL generation produces valid schema definitions."""
    ddl = DuckDBAnalytics.generate_postgres_ddl()
    assert "CREATE TABLE IF NOT EXISTS model_pricing" in ddl
    assert "CREATE TABLE IF NOT EXISTS llm_requests" in ddl
    assert "NUMERIC(18, 6)" in ddl
    assert "CREATE OR REPLACE VIEW view_kpi_metrics" in ddl
    assert "CREATE OR REPLACE VIEW view_team_spend_share" in ddl
