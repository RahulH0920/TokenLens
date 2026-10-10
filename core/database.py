"""DuckDB Analytical Database Engine for LLM FinOps.

Provides schema management, persistent storage, pre-aggregated views optimized
for graphical representations (Plotly/Streamlit/BI), and bi-directional connectivity
with PostgreSQL via DuckDB's native postgres extension.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging
import secrets
import os

import duckdb
import pandas as pd

from core.models import PricedRequest, PricingRecord
from core.sql_safety import quote_identifier, sql_string_literal, validate_postgres_connection

LOGGER = logging.getLogger("tokenlens.database")


class DuckDBAnalytics:
    """Manages DuckDB analytics database, graphical views, and PostgreSQL interop."""

    def __init__(self, db_path: Path | str = "data/tokenlens_analytics.duckdb", in_memory: bool = False):
        if in_memory:
            self.db_path = ":memory:"
        else:
            self.db_path = str(Path(db_path).resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize relational schema, constraints, and graphical aggregation views."""
        # 1. Model Pricing Rate Card Table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                input_usd_per_1m DECIMAL(18, 6) NOT NULL,
                output_usd_per_1m DECIMAL(18, 6) NOT NULL,
                cached_usd_per_1m DECIMAL(18, 6) NOT NULL DEFAULT 0.0,
                effective_from DATE NOT NULL,
                effective_to DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (model, effective_from)
            );
        """)

        # 2. Granular LLM Request Ledger Table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_requests (
                request_id VARCHAR PRIMARY KEY,
                timestamp_utc TIMESTAMPTZ NOT NULL,
                provider VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                team VARCHAR NOT NULL,
                feature VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                env VARCHAR NOT NULL DEFAULT 'production',
                status VARCHAR NOT NULL DEFAULT 'success',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                input_tokens BIGINT NOT NULL,
                output_tokens BIGINT NOT NULL,
                cached_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT GENERATED ALWAYS AS (input_tokens + output_tokens) VIRTUAL,
                input_cost_usd DECIMAL(18, 6) NOT NULL,
                output_cost_usd DECIMAL(18, 6) NOT NULL,
                cached_cost_usd DECIMAL(18, 6) NOT NULL DEFAULT 0.0,
                total_cost_usd DECIMAL(18, 6) NOT NULL,
                missing_price BOOLEAN NOT NULL DEFAULT FALSE,
                is_unattributed BOOLEAN NOT NULL DEFAULT FALSE,
                source VARCHAR NOT NULL DEFAULT 'batch_log'
            );
        """)

        # Secondary indexes for acceleration of graphical queries
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_team ON llm_requests(team);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_model ON llm_requests(model);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_ts ON llm_requests(timestamp_utc);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_user ON llm_requests(user_id);")

        # 3. Delegated Workload Tasks Table (Manager to Team Leader)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS delegated_tasks (
                task_id VARCHAR PRIMARY KEY,
                task_name VARCHAR NOT NULL,
                department VARCHAR NOT NULL,
                team_leader VARCHAR NOT NULL,
                manager_name VARCHAR NOT NULL,
                manager_username VARCHAR NOT NULL DEFAULT 'legacy',
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
            );
        """)
        self.conn.execute(
            "ALTER TABLE delegated_tasks ADD COLUMN IF NOT EXISTS manager_username VARCHAR DEFAULT 'legacy';"
        )

        self._init_graphical_views()

    def _init_graphical_views(self) -> None:
        """Create analytical SQL views optimized for graphical dashboards and visual reporting."""
        
        # View 1: Executive KPI Metrics
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_kpi_metrics AS
            SELECT 
                COUNT(*) AS total_requests,
                COALESCE(SUM(total_cost_usd), 0.0) AS total_spend_usd,
                COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                COALESCE(SUM(cached_tokens), 0) AS total_cached_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                ROUND(AVG(total_cost_usd), 6) AS avg_cost_per_request,
                ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
                COUNT(DISTINCT team) AS active_teams,
                COUNT(DISTINCT model) AS active_models,
                COUNT(DISTINCT user_id) AS active_users,
                SUM(CASE WHEN is_unattributed THEN 1 ELSE 0 END) AS unattributed_requests,
                SUM(CASE WHEN missing_price THEN 1 ELSE 0 END) AS missing_price_requests
            FROM llm_requests;
        """)

        # View 2: Spend by Department / Team (Optimized for Team Donut Charts & Vertical Cards)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_team_spend_share AS
            WITH totals AS (
                SELECT COALESCE(SUM(total_cost_usd), 0.000001) AS grand_total FROM llm_requests
            )
            SELECT 
                r.team,
                COUNT(*) AS request_count,
                SUM(r.total_cost_usd) AS total_spend_usd,
                ROUND((SUM(r.total_cost_usd) / t.grand_total) * 100.0, 2) AS spend_percentage,
                SUM(r.input_tokens) AS input_tokens,
                SUM(r.output_tokens) AS output_tokens,
                SUM(r.total_tokens) AS total_tokens,
                ROUND(AVG(r.total_cost_usd), 4) AS avg_cost_per_request,
                COUNT(DISTINCT r.user_id) AS active_callers,
                MODE(r.model) AS primary_model
            FROM llm_requests r, totals t
            GROUP BY r.team, t.grand_total
            ORDER BY total_spend_usd DESC;
        """)

        # View 3: Team & Feature Spend Breakdown (Optimized for Nested / Donut Feature Charts)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_feature_breakdown AS
            SELECT 
                team,
                feature,
                COUNT(*) AS request_count,
                SUM(total_cost_usd) AS total_spend_usd,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(total_tokens) AS total_tokens,
                ROUND(AVG(total_cost_usd), 4) AS avg_cost_per_request,
                ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
                MODE(model) AS primary_model
            FROM llm_requests
            GROUP BY team, feature
            ORDER BY team, total_spend_usd DESC;
        """)

        # View 4: Daily Spend & Token Time Series (Optimized for Daily Trend & Area Charts)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_daily_spend_trend AS
            SELECT 
                CAST(timestamp_utc AS DATE) AS usage_date,
                provider,
                COUNT(*) AS request_count,
                SUM(total_cost_usd) AS total_spend_usd,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(cached_tokens) AS cached_tokens,
                SUM(total_tokens) AS total_tokens,
                ROUND(AVG(latency_ms), 1) AS avg_latency_ms
            FROM llm_requests
            GROUP BY CAST(timestamp_utc AS DATE), provider
            ORDER BY usage_date ASC, provider ASC;
        """)

        # View 5: Model Usage & Cost Distribution (Optimized for Model Donut Charts)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_model_distribution AS
            SELECT 
                model,
                provider,
                COUNT(*) AS request_count,
                SUM(total_cost_usd) AS total_spend_usd,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(cached_tokens) AS cached_tokens,
                SUM(total_tokens) AS total_tokens,
                ROUND(AVG(total_cost_usd), 6) AS avg_cost_per_request,
                ROUND(AVG(latency_ms), 1) AS avg_latency_ms
            FROM llm_requests
            GROUP BY model, provider
            ORDER BY total_spend_usd DESC;
        """)

        # View 6: Team x Model Matrix (Optimized for Plotly Heatmaps)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_team_model_heatmap AS
            SELECT 
                team,
                model,
                SUM(total_cost_usd) AS total_spend_usd,
                SUM(total_tokens) AS total_tokens,
                COUNT(*) AS request_count
            FROM llm_requests
            GROUP BY team, model
            ORDER BY team ASC, model ASC;
        """)

        # View 7: User Consumption Leaderboard (Optimized for User Concentration Bars)
        self.conn.execute("""
            CREATE OR REPLACE VIEW view_user_leaderboard AS
            SELECT 
                user_id,
                team,
                COUNT(*) AS request_count,
                SUM(total_cost_usd) AS total_spend_usd,
                SUM(total_tokens) AS total_tokens,
                ROUND(AVG(total_cost_usd), 4) AS avg_cost_per_request
            FROM llm_requests
            GROUP BY user_id, team
            ORDER BY total_spend_usd DESC;
        """)

    # -------------------------------------------------------------------------
    # Data Loading Methods
    # -------------------------------------------------------------------------

    def load_pricing_csv(self, csv_path: Path | str) -> int:
        """Ingest model pricing records from CSV into model_pricing table."""
        path = str(Path(csv_path).resolve())
        query = """
            INSERT OR REPLACE INTO model_pricing (model, provider, input_usd_per_1m, output_usd_per_1m, cached_usd_per_1m, effective_from, effective_to)
            SELECT 
                model,
                provider,
                CAST(input_usd_per_1m AS DECIMAL(18, 6)),
                CAST(output_usd_per_1m AS DECIMAL(18, 6)),
                CAST(COALESCE(cached_usd_per_1m, 0.0) AS DECIMAL(18, 6)),
                CAST(effective_from AS DATE),
                CASE WHEN effective_to IS NOT NULL AND effective_to != '' THEN CAST(effective_to AS DATE) ELSE NULL END
            FROM read_csv_auto(?);
        """
        self.conn.execute(query, [path])
        return int(self.conn.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0])

    def insert_priced_requests(self, requests: List[PricedRequest]) -> int:
        """Insert a batch of priced requests using parameterized bulk insert."""
        if not requests:
            return 0
        
        rows = [
            (
                r.request_id,
                r.timestamp_utc,
                r.provider,
                r.model,
                r.team,
                r.feature,
                r.user_id,
                r.env,
                r.status,
                r.latency_ms,
                r.input_tokens,
                r.output_tokens,
                r.cached_tokens,
                r.input_cost_usd,
                r.output_cost_usd,
                r.cached_cost_usd,
                r.total_cost_usd,
                r.missing_price,
                r.is_unattributed,
                r.source,
            )
            for r in requests
        ]

        self.conn.executemany("""
            INSERT OR REPLACE INTO llm_requests (
                request_id, timestamp_utc, provider, model, team, feature, user_id, env,
                status, latency_ms, input_tokens, output_tokens, cached_tokens,
                input_cost_usd, output_cost_usd, cached_cost_usd, total_cost_usd,
                missing_price, is_unattributed, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        return len(rows)

    # -------------------------------------------------------------------------
    # Graphical Analytics Query Helpers
    # -------------------------------------------------------------------------

    def get_kpi_summary(self) -> Dict[str, Any]:
        """Fetch summary KPIs for top dashboard cards."""
        res = self.conn.execute("SELECT * FROM view_kpi_metrics").df()
        return res.to_dict(orient="records")[0] if not res.empty else {}

    def get_team_spend_df(self) -> pd.DataFrame:
        """Fetch team spend breakdown for Plotly donut pie chart and vertical detail cards."""
        return self.conn.execute("SELECT * FROM view_team_spend_share").df()

    def get_feature_breakdown_df(self, team: Optional[str] = None) -> pd.DataFrame:
        """Fetch feature breakdown for team-specific donut or bar charts."""
        if team:
            return self.conn.execute("SELECT * FROM view_feature_breakdown WHERE team = ?", [team]).df()
        return self.conn.execute("SELECT * FROM view_feature_breakdown").df()

    def get_daily_trends_df(self) -> pd.DataFrame:
        """Fetch daily time-series trends for Plotly Line / Stacked Area charts."""
        return self.conn.execute("SELECT * FROM view_daily_spend_trend").df()

    def get_model_distribution_df(self) -> pd.DataFrame:
        """Fetch model spend and token distribution for model pie charts."""
        return self.conn.execute("SELECT * FROM view_model_distribution").df()

    def get_team_model_heatmap_df(self) -> pd.DataFrame:
        """Fetch team x model cross-tabulation for heatmaps."""
        return self.conn.execute("SELECT * FROM view_team_model_heatmap").df()

    def get_user_leaderboard_df(self, limit: int = 10) -> pd.DataFrame:
        """Fetch top users by total spend."""
        return self.conn.execute("SELECT * FROM view_user_leaderboard LIMIT ?", [limit]).df()

    # -------------------------------------------------------------------------
    # Delegated Tasks Management (Manager -> Team Leader)
    # -------------------------------------------------------------------------

    def create_delegated_task(
        self,
        task_id: str,
        task_name: str,
        department: str,
        team_leader: str,
        model: str,
        allocated_input_tokens: int,
        allocated_output_tokens: int,
        allocated_budget_usd: Decimal | float,
        manager_name: str = "Engineering Manager",
        manager_username: str = "legacy",
        priority: str = "Medium",
        notes: str = "",
    ) -> str:
        """Create or update a delegated workload task assigned by a manager to a team leader."""
        self.conn.execute("""
            INSERT INTO delegated_tasks (
                task_id, task_name, department, team_leader, manager_name, manager_username,
                model, allocated_input_tokens, allocated_output_tokens,
                allocated_budget_usd, consumed_tokens, consumed_spend_usd,
                priority, status, created_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0.0, ?, 'Assigned', CURRENT_TIMESTAMP, ?)
        """, [
            task_id, task_name, department, team_leader, manager_name, manager_username,
            model, int(allocated_input_tokens), int(allocated_output_tokens),
            float(allocated_budget_usd), priority, notes
        ])
        return task_id

    def get_delegated_tasks_df(
        self,
        department: Optional[str] = None,
        team_leader: Optional[str] = None,
    ) -> pd.DataFrame:
        """Retrieve all delegated tasks as a pandas DataFrame."""
        conditions = []
        parameters = []
        if department and department != "All Departments":
            conditions.append("lower(department) = lower(?)")
            parameters.append(department)
        if team_leader:
            conditions.append("lower(team_leader) = lower(?)")
            parameters.append(team_leader)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.conn.execute(
            f"SELECT * FROM delegated_tasks{where_clause} ORDER BY created_at DESC",
            parameters,
        ).df()

    def get_delegated_task(self, task_id: str) -> dict[str, Any] | None:
        """Return one task for a fresh authorization check before a mutation."""
        cursor = self.conn.execute(
            """
            SELECT task_id, task_name, department, team_leader, manager_name,
                   manager_username, model, allocated_input_tokens,
                   allocated_output_tokens, allocated_budget_usd,
                   consumed_tokens, consumed_spend_usd, priority, status, notes
            FROM delegated_tasks WHERE task_id = ?
            """,
            [task_id],
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip((column[0] for column in cursor.description), row))

    def update_task_status(self, task_id: str, status: str) -> None:
        """Update workflow status of an assigned task."""
        self.conn.execute(
            "UPDATE delegated_tasks SET status = ? WHERE task_id = ?",
            [status, task_id]
        )

    def update_task_tokens(self, task_id: str, consumed_tokens: int, consumed_spend_usd: float = 0.0) -> None:
        """Update token usage consumed so far for a delegated workload."""
        self.conn.execute(
            "UPDATE delegated_tasks SET consumed_tokens = ?, consumed_spend_usd = ? WHERE task_id = ?",
            [int(consumed_tokens), float(consumed_spend_usd), task_id]
        )

    def delete_delegated_task(self, task_id: str) -> None:
        """Delete an assigned task."""
        self.conn.execute("DELETE FROM delegated_tasks WHERE task_id = ?", [task_id])

    # -------------------------------------------------------------------------
    # PostgreSQL Connectivity & Interoperability
    # -------------------------------------------------------------------------

    def attach_postgres(
        self,
        host: str = "127.0.0.1",
        port: int = 5432,
        dbname: str = "tokenlens",
        user: str | None = None,
        password: str | None = None,
        sslmode: str = "prefer",
        pg_schema_alias: str = "pg",
    ) -> bool:
        """Attach PostgreSQL using a temporary DuckDB secret, without embedding credentials in ATTACH."""
        user = user or os.getenv("PGUSER")
        if not user:
            raise ValueError("Supply a least-privilege PostgreSQL user or set PGUSER.")
        if password is None:
            password = os.getenv("PGPASSWORD")
        port, sslmode = validate_postgres_connection(host, port, dbname, user, password, sslmode)
        alias = quote_identifier(pg_schema_alias)

        self.conn.execute("INSTALL postgres;")
        self.conn.execute("LOAD postgres;")

        secret_name = f"tokenlens_pg_{secrets.token_hex(8)}"
        secret_sql = f"""
            CREATE SECRET {quote_identifier(secret_name)} (
                TYPE postgres,
                HOST {sql_string_literal(host, field="PostgreSQL host", max_length=253)},
                PORT {port},
                DATABASE {sql_string_literal(dbname, field="PostgreSQL database name", max_length=63)},
                USER {sql_string_literal(user, field="PostgreSQL user", max_length=63)},
                PASSWORD {sql_string_literal(password, field="PostgreSQL password") if password else "''"},
                SSLMODE {sslmode}
            );
        """
        try:
            self.conn.execute(secret_sql)
        except Exception:
            # DuckDB may include a failing statement in its error text. Never
            # return the CREATE SECRET statement, which contains the password.
            raise RuntimeError("Could not configure temporary PostgreSQL credentials; driver details were suppressed.") from None

        self.conn.execute(f"ATTACH '' AS {alias} (TYPE POSTGRES, SECRET {quote_identifier(secret_name)});")
        LOGGER.info("Attached PostgreSQL database as schema '%s'", pg_schema_alias)
        return True

    def sync_to_postgres(self, pg_schema_alias: str = "pg") -> Dict[str, int]:
        """Export/synchronize DuckDB tables directly to an attached PostgreSQL instance."""
        alias = quote_identifier(pg_schema_alias)
        # 1. Sync pricing table
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {alias}.model_pricing AS
            SELECT * FROM model_pricing WITH NO DATA;
        """)
        self.conn.execute(f"""
            INSERT INTO {alias}.model_pricing
            SELECT * FROM model_pricing
            ON CONFLICT (model, effective_from) DO UPDATE SET
                input_usd_per_1m = EXCLUDED.input_usd_per_1m,
                output_usd_per_1m = EXCLUDED.output_usd_per_1m,
                cached_usd_per_1m = EXCLUDED.cached_usd_per_1m,
                effective_to = EXCLUDED.effective_to;
        """)
        pricing_count = int(self.conn.execute(f"SELECT COUNT(*) FROM {alias}.model_pricing").fetchone()[0])

        # 2. Sync request ledger
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {alias}.llm_requests AS
            SELECT * EXCLUDE (total_tokens) FROM llm_requests WITH NO DATA;
        """)
        self.conn.execute(f"""
            INSERT INTO {alias}.llm_requests
            SELECT * EXCLUDE (total_tokens) FROM llm_requests
            ON CONFLICT (request_id) DO NOTHING;
        """)
        requests_count = int(self.conn.execute(f"SELECT COUNT(*) FROM {alias}.llm_requests").fetchone()[0])

        return {"pricing_synced": pricing_count, "requests_synced": requests_count}

    @staticmethod
    def generate_postgres_ddl() -> str:
        """Generate clean standard PostgreSQL DDL for recreating the TokenLens schema in Postgres."""
        return """
-- ============================================================================
-- TOKENLENS POSTGRESQL STORAGE SPECIFICATION DDL
-- Compatible with PostgreSQL 14, 15, 16, 17
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tokenlens;
SET search_path TO tokenlens, public;

-- 1. Model Pricing Rate Card Table
CREATE TABLE IF NOT EXISTS model_pricing (
    model VARCHAR(200) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    input_usd_per_1m NUMERIC(18, 6) NOT NULL,
    output_usd_per_1m NUMERIC(18, 6) NOT NULL,
    cached_usd_per_1m NUMERIC(18, 6) NOT NULL DEFAULT 0.0,
    effective_from DATE NOT NULL,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model, effective_from)
);

-- 2. Granular LLM Request Ledger Table
CREATE TABLE IF NOT EXISTS llm_requests (
    request_id VARCHAR(200) PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model VARCHAR(200) NOT NULL,
    team VARCHAR(200) NOT NULL,
    feature VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    env VARCHAR(64) NOT NULL DEFAULT 'production',
    status VARCHAR(64) NOT NULL DEFAULT 'success',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    cached_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    input_cost_usd NUMERIC(18, 6) NOT NULL,
    output_cost_usd NUMERIC(18, 6) NOT NULL,
    cached_cost_usd NUMERIC(18, 6) NOT NULL DEFAULT 0.0,
    total_cost_usd NUMERIC(18, 6) NOT NULL,
    missing_price BOOLEAN NOT NULL DEFAULT FALSE,
    is_unattributed BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(64) NOT NULL DEFAULT 'batch_log'
);

-- 3. Optimization Indexes for Analytical Queries & Dashboard Visualization
CREATE INDEX IF NOT EXISTS idx_llm_requests_team ON llm_requests(team);
CREATE INDEX IF NOT EXISTS idx_llm_requests_model ON llm_requests(model);
CREATE INDEX IF NOT EXISTS idx_llm_requests_ts ON llm_requests(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_llm_requests_user ON llm_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_llm_requests_feature ON llm_requests(team, feature);

-- 4. Analytical Views for Graphical Dashboards
CREATE OR REPLACE VIEW view_kpi_metrics AS
SELECT 
    COUNT(*) AS total_requests,
    COALESCE(SUM(total_cost_usd), 0.0) AS total_spend_usd,
    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
    COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
    COALESCE(SUM(cached_tokens), 0) AS total_cached_tokens,
    COALESCE(SUM(total_tokens), 0) AS total_tokens,
    ROUND(AVG(total_cost_usd), 6) AS avg_cost_per_request,
    ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
    COUNT(DISTINCT team) AS active_teams,
    COUNT(DISTINCT model) AS active_models,
    COUNT(DISTINCT user_id) AS active_users
FROM llm_requests;

CREATE OR REPLACE VIEW view_team_spend_share AS
WITH totals AS (
    SELECT COALESCE(SUM(total_cost_usd), 0.000001) AS grand_total FROM llm_requests
)
SELECT 
    r.team,
    COUNT(*) AS request_count,
    SUM(r.total_cost_usd) AS total_spend_usd,
    ROUND((SUM(r.total_cost_usd) / t.grand_total) * 100.0, 2) AS spend_percentage,
    SUM(r.input_tokens) AS input_tokens,
    SUM(r.output_tokens) AS output_tokens,
    SUM(r.total_tokens) AS total_tokens,
    ROUND(AVG(r.total_cost_usd), 4) AS avg_cost_per_request,
    COUNT(DISTINCT r.user_id) AS active_callers
FROM llm_requests r, totals t
GROUP BY r.team, t.grand_total
ORDER BY total_spend_usd DESC;

CREATE OR REPLACE VIEW view_daily_spend_trend AS
SELECT 
    CAST(timestamp_utc AS DATE) AS usage_date,
    provider,
    COUNT(*) AS request_count,
    SUM(total_cost_usd) AS total_spend_usd,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cached_tokens) AS cached_tokens,
    SUM(total_tokens) AS total_tokens,
    ROUND(AVG(latency_ms), 1) AS avg_latency_ms
FROM llm_requests
GROUP BY CAST(timestamp_utc AS DATE), provider
ORDER BY usage_date ASC, provider ASC;
""".strip()

    def close(self) -> None:
        """Close connection cleanly."""
        self.conn.close()
