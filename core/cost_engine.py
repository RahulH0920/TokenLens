"""Cost calculation and aggregation engine for LLM FinOps using DuckDB and Decimal-safe arithmetic."""

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import duckdb

from core.models import RequestRecord, PricingRecord, PricedRequest


ONE_MILLION = Decimal("1000000")


def quantize_cost(val: Decimal) -> Decimal:
    """Keep 6 decimal places for per-request precision, avoiding floating-point drift."""
    return val.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def quantize_currency(val: Decimal) -> Decimal:
    """Standard 2 decimal places for financial reporting."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class CostEngine:
    def __init__(self, duckdb_conn: Optional[duckdb.DuckDBPyConnection] = None):
        self.conn = duckdb_conn or duckdb.connect(database=":memory:")
        self.pricing_registry: Dict[str, List[PricingRecord]] = {}
        self._init_duckdb_schema()

    def _init_duckdb_schema(self):
        """Initialize tables and views in DuckDB matching TRD specification."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pricing (
                model VARCHAR NOT NULL,
                provider VARCHAR,
                input_usd_per_1m DOUBLE NOT NULL,
                output_usd_per_1m DOUBLE NOT NULL,
                cached_usd_per_1m DOUBLE DEFAULT 0.0,
                effective_from DATE NOT NULL,
                effective_to DATE,
                PRIMARY KEY (model, effective_from)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                request_id VARCHAR PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                provider VARCHAR NOT NULL DEFAULT 'openai',
                model VARCHAR NOT NULL,
                input_tokens BIGINT NOT NULL,
                output_tokens BIGINT NOT NULL,
                cached_tokens BIGINT DEFAULT 0,
                latency_ms INTEGER,
                status VARCHAR,
                team VARCHAR,
                feature VARCHAR,
                user_id VARCHAR,
                env VARCHAR,
                source VARCHAR,
                is_unattributed BOOLEAN
            );
        """)

    def load_pricing_records(self, pricing_list: List[PricingRecord]):
        """Load pricing table into memory registry and DuckDB."""
        self.pricing_registry.clear()
        self.conn.execute("DELETE FROM pricing;")

        for p in pricing_list:
            if p.model not in self.pricing_registry:
                self.pricing_registry[p.model] = []
            self.pricing_registry[p.model].append(p)

            self.conn.execute("""
                INSERT INTO pricing (model, provider, input_usd_per_1m, output_usd_per_1m, cached_usd_per_1m, effective_from, effective_to)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                p.model,
                p.provider,
                float(p.input_usd_per_1m),
                float(p.output_usd_per_1m),
                float(p.cached_usd_per_1m),
                p.effective_from,
                p.effective_to
            ])

        # Sort pricing records by effective_from descending for fast date matching
        for model in self.pricing_registry:
            self.pricing_registry[model].sort(key=lambda x: x.effective_from, reverse=True)

        self._refresh_cost_view()

    def _refresh_cost_view(self):
        """Create/refresh standard SQL view matching TRD 2.2."""
        self.conn.execute("DROP VIEW IF EXISTS request_costs;")
        self.conn.execute("""
            CREATE VIEW request_costs AS
            SELECT 
                r.*,
                p.provider AS pricing_provider,
                p.input_usd_per_1m,
                p.output_usd_per_1m,
                p.cached_usd_per_1m,
                (r.input_tokens * COALESCE(p.input_usd_per_1m, 0.0)) / 1000000.0 AS input_cost_usd,
                (r.output_tokens * COALESCE(p.output_usd_per_1m, 0.0)) / 1000000.0 AS output_cost_usd,
                (COALESCE(r.cached_tokens, 0) * COALESCE(p.cached_usd_per_1m, 0.0)) / 1000000.0 AS cached_cost_usd,
                ((r.input_tokens * COALESCE(p.input_usd_per_1m, 0.0)) + 
                 (r.output_tokens * COALESCE(p.output_usd_per_1m, 0.0)) + 
                 (COALESCE(r.cached_tokens, 0) * COALESCE(p.cached_usd_per_1m, 0.0))) / 1000000.0 AS total_cost_usd,
                (p.model IS NULL) AS missing_price
            FROM requests r
            LEFT JOIN (
                SELECT p1.*
                FROM pricing p1
                INNER JOIN (
                    SELECT model, MAX(effective_from) AS max_effective
                    FROM pricing
                    GROUP BY model
                ) p2 ON p1.model = p2.model AND p1.effective_from = p2.max_effective
            ) p ON r.model = p.model;
        """)

    def find_price(self, model: str, request_date: date) -> Optional[PricingRecord]:
        """Find the effective price record for a given model and date."""
        candidates = self.pricing_registry.get(model, [])
        for record in candidates:
            if record.effective_from <= request_date:
                if record.effective_to is None or request_date <= record.effective_to:
                    return record
        return None

    def calculate_request_cost(self, req: RequestRecord) -> PricedRequest:
        """Deterministic decimal calculation for a single request record."""
        req_date = req.timestamp_utc.date()
        pricing = self.find_price(req.model, req_date)

        if pricing is None:
            # Missing price must be explicitly flagged, not silently treated as 0
            return PricedRequest(
                request_id=req.request_id,
                timestamp_utc=req.timestamp_utc,
                team=req.team,
                feature=req.feature,
                user_id=req.user_id,
                provider=req.provider,
                model=req.model,
                input_tokens=req.input_tokens,
                output_tokens=req.output_tokens,
                cached_tokens=req.cached_tokens,
                status=req.status,
                latency_ms=req.latency_ms,
                env=req.env,
                source=req.source,
                input_cost_usd=Decimal("0.0"),
                output_cost_usd=Decimal("0.0"),
                cached_cost_usd=Decimal("0.0"),
                total_cost_usd=Decimal("0.0"),
                missing_price=True,
                is_unattributed=(req.team == "unattributed" or req.feature == "unassigned")
            )

        in_tokens = Decimal(req.input_tokens)
        out_tokens = Decimal(req.output_tokens)
        cached_tokens = Decimal(req.cached_tokens)

        # Standard deterministic formula: (T_in * P_in + T_out * P_out + T_cached * P_cached) / 1,000,000
        in_cost = quantize_cost((in_tokens * pricing.input_usd_per_1m) / ONE_MILLION)
        out_cost = quantize_cost((out_tokens * pricing.output_usd_per_1m) / ONE_MILLION)
        cached_cost = quantize_cost((cached_tokens * pricing.cached_usd_per_1m) / ONE_MILLION)
        total_cost = quantize_cost(in_cost + out_cost + cached_cost)

        return PricedRequest(
            request_id=req.request_id,
            timestamp_utc=req.timestamp_utc,
            team=req.team,
            feature=req.feature,
            user_id=req.user_id,
            provider=req.provider,
            model=req.model,
            input_tokens=req.input_tokens,
            output_tokens=req.output_tokens,
            cached_tokens=req.cached_tokens,
            status=req.status,
            latency_ms=req.latency_ms,
            env=req.env,
            source=req.source,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            cached_cost_usd=cached_cost,
            total_cost_usd=total_cost,
            missing_price=False,
            is_unattributed=(req.team == "unattributed" or req.feature == "unassigned")
        )

    def process_requests(self, requests_list: List[RequestRecord]) -> List[PricedRequest]:
        """Compute costs for all requests and load into DuckDB."""
        priced_records = [self.calculate_request_cost(r) for r in requests_list]

        # Sync to DuckDB requests table
        self.conn.execute("DELETE FROM requests;")
        for pr in priced_records:
            self.conn.execute("""
                INSERT INTO requests (
                    request_id, ts, provider, model, input_tokens, output_tokens, cached_tokens,
                    latency_ms, status, team, feature, user_id, env, source, is_unattributed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                pr.request_id,
                pr.timestamp_utc,
                pr.provider,
                pr.model,
                pr.input_tokens,
                pr.output_tokens,
                pr.cached_tokens,
                pr.latency_ms,
                pr.status,
                pr.team,
                pr.feature,
                pr.user_id,
                pr.env,
                pr.source,
                pr.is_unattributed
            ])

        return priced_records

    def append_priced_request(self, request: PricedRequest) -> None:
        """Append one priced request to the live DuckDB ledger without resetting it."""
        self.conn.execute(
            """
            INSERT INTO requests (
                request_id, ts, provider, model, input_tokens, output_tokens,
                cached_tokens, latency_ms, status, team, feature, user_id, env,
                source, is_unattributed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                request.request_id,
                request.timestamp_utc,
                request.provider,
                request.model,
                request.input_tokens,
                request.output_tokens,
                request.cached_tokens,
                request.latency_ms,
                request.status,
                request.team,
                request.feature,
                request.user_id,
                request.env,
                request.source,
                request.is_unattributed,
            ],
        )

    def get_priced_dataframe(self, priced_records: List[PricedRequest]) -> pd.DataFrame:
        """Convert list of priced records to a structured Pandas DataFrame with float fields for plotting."""
        data = []
        for r in priced_records:
            data.append({
                "request_id": r.request_id,
                "timestamp_utc": r.timestamp_utc,
                "date": r.timestamp_utc.date().isoformat(),
                "hour": r.timestamp_utc.strftime("%Y-%m-%d %H:00"),
                "team": r.team,
                "feature": r.feature,
                "user_id": r.user_id,
                "provider": r.provider,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cached_tokens": r.cached_tokens,
                "total_tokens": r.input_tokens + r.output_tokens + r.cached_tokens,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "env": r.env,
                "source": r.source,
                "input_cost_usd": float(r.input_cost_usd),
                "output_cost_usd": float(r.output_cost_usd),
                "cached_cost_usd": float(r.cached_cost_usd),
                "total_cost_usd": float(r.total_cost_usd),
                "missing_price": r.missing_price,
                "is_unattributed": r.is_unattributed
            })
        return pd.DataFrame(data)

    def verify_invariants(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, float]]:
        """Verify cross-cut invariants:
        Grand Total Cost == Sum(Team Cost) == Sum(Model Cost) == Sum(Date Cost)
        """
        if df.empty:
            return True, {"total": 0.0, "team_sum": 0.0, "model_sum": 0.0, "date_sum": 0.0}

        grand_total = round(float(df["total_cost_usd"].sum()), 4)
        team_sum = round(float(df.groupby("team")["total_cost_usd"].sum().sum()), 4)
        model_sum = round(float(df.groupby("model")["total_cost_usd"].sum().sum()), 4)
        date_sum = round(float(df.groupby("date")["total_cost_usd"].sum().sum()), 4)

        is_invariant = (
            abs(grand_total - team_sum) < 0.0001
            and abs(grand_total - model_sum) < 0.0001
            and abs(grand_total - date_sum) < 0.0001
        )

        return is_invariant, {
            "grand_total": grand_total,
            "team_sum": team_sum,
            "model_sum": model_sum,
            "date_sum": date_sum
        }
