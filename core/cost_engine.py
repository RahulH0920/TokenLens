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
                ROUND((r.input_tokens * COALESCE(p.input_usd_per_1m, 0.0)) / 1000000.0, 6) AS input_cost_usd,
                ROUND((r.output_tokens * COALESCE(p.output_usd_per_1m, 0.0)) / 1000000.0, 6) AS output_cost_usd,
                ROUND((COALESCE(r.cached_tokens, 0) * COALESCE(p.cached_usd_per_1m, 0.0)) / 1000000.0, 6) AS cached_cost_usd,
                ROUND(((r.input_tokens * COALESCE(p.input_usd_per_1m, 0.0)) + 
                 (r.output_tokens * COALESCE(p.output_usd_per_1m, 0.0)) + 
                 (COALESCE(r.cached_tokens, 0) * COALESCE(p.cached_usd_per_1m, 0.0))) / 1000000.0, 6) AS total_cost_usd,
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
                    request_id, ts, model, input_tokens, output_tokens, cached_tokens,
                    latency_ms, status, team, feature, user_id, env, source, is_unattributed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                pr.request_id,
                pr.timestamp_utc,
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

    def get_missing_pricing_summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Summarize requests that encountered missing pricing cards."""
        if df.empty or "missing_price" not in df.columns:
            return {"count": 0, "models": [], "requests": []}

        unpriced_df = df[df["missing_price"]]
        if unpriced_df.empty:
            return {"count": 0, "models": [], "requests": []}

        unpriced_models = sorted(unpriced_df["model"].unique().tolist())
        return {
            "count": len(unpriced_df),
            "models": unpriced_models,
            "total_tokens_unpriced": int(unpriced_df["total_tokens"].sum()),
            "affected_teams": sorted(unpriced_df["team"].unique().tolist()),
            "requests": unpriced_df[["request_id", "timestamp_utc", "model", "team", "feature", "user_id", "total_tokens"]].to_dict(orient="records")
        }

    def get_budget_utilization(self, df: pd.DataFrame, custom_budgets: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Calculate department-level and organizational budget utilization and threshold alerts."""
        default_budgets = {
            "engineering": 4.00,
            "research": 3.50,
            "product": 3.50,
            "support": 3.00,
            "marketing": 2.50,
            "unattributed": 1.00
        }
        budgets = dict(default_budgets)
        if custom_budgets:
            budgets.update(custom_budgets)

        team_spend = {}
        if not df.empty:
            team_spend = df.groupby("team")["total_cost_usd"].sum().to_dict()

        departments = []
        total_budget = sum(budgets.values())
        total_actual = 0.0

        for team, budget in budgets.items():
            actual = float(team_spend.get(team, 0.0))
            total_actual += actual
            pct = (actual / budget * 100.0) if budget > 0 else 0.0
            remaining = budget - actual

            if pct >= 100.0:
                status = "CRITICAL"
                badge = "🔴 OVER BUDGET"
            elif pct >= 80.0:
                status = "WARNING"
                badge = "🟡 NEAR CAPACITY"
            else:
                status = "SAFE"
                badge = "🟢 ON TRACK"

            departments.append({
                "team": team,
                "budget_usd": round(budget, 2),
                "actual_spend_usd": round(actual, 4),
                "remaining_usd": round(remaining, 4),
                "utilization_pct": round(pct, 1),
                "status": status,
                "badge": badge
            })

        # Sort by utilization desc
        departments.sort(key=lambda d: d["utilization_pct"], reverse=True)

        overall_pct = (total_actual / total_budget * 100.0) if total_budget > 0 else 0.0
        alerts_count = sum(1 for d in departments if d["status"] in {"WARNING", "CRITICAL"})

        return {
            "total_budget_usd": round(total_budget, 2),
            "total_actual_spend_usd": round(total_actual, 4),
            "total_remaining_usd": round(total_budget - total_actual, 4),
            "overall_utilization_pct": round(overall_pct, 1),
            "overall_status": "WARNING" if overall_pct >= 80.0 else "SAFE",
            "active_alerts_count": alerts_count,
            "departments": departments
        }

    def get_cost_driver_insights(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze top cost drivers, model concentration, and prompt efficiency anomalies."""
        if df.empty:
            return {"top_drivers": [], "model_concentration": {}, "efficiency_anomalies": []}

        tot_spend = float(df["total_cost_usd"].sum())

        # 1. Top Feature / Team Drivers
        grouped = df.groupby(["team", "feature"]).agg(
            spend=("total_cost_usd", "sum"),
            requests=("request_id", "count"),
            in_tokens=("input_tokens", "sum"),
            out_tokens=("output_tokens", "sum"),
            top_model=("model", lambda x: x.mode()[0] if not x.empty else "unknown")
        ).reset_index().sort_values("spend", ascending=False)

        top_drivers = []
        for _, row in grouped.head(5).iterrows():
            sp = float(row["spend"])
            top_drivers.append({
                "team": row["team"],
                "feature": row["feature"],
                "top_model": row["top_model"],
                "spend_usd": round(sp, 4),
                "requests": int(row["requests"]),
                "share_pct": round((sp / tot_spend * 100.0) if tot_spend > 0 else 0.0, 1)
            })

        # 2. Model Concentration
        model_spend = df.groupby("model")["total_cost_usd"].sum().sort_values(ascending=False)
        top_2_models = model_spend.head(2)
        top_2_share = float(top_2_models.sum() / tot_spend * 100.0) if tot_spend > 0 else 0.0

        model_concentration = {
            "top_models": [
                {"model": m, "spend_usd": round(float(v), 4), "share_pct": round(float(v / tot_spend * 100.0) if tot_spend > 0 else 0.0, 1)}
                for m, v in model_spend.items()
            ],
            "top_2_concentration_pct": round(top_2_share, 1)
        }

        # 3. Prompt-heavy efficiency anomalies (high input-to-output ratio)
        anomalies = []
        feat_tokens = df.groupby("feature").agg(
            in_tok=("input_tokens", "sum"),
            out_tok=("output_tokens", "sum"),
            cached_tok=("cached_tokens", "sum"),
            spend=("total_cost_usd", "sum")
        ).reset_index()

        for _, row in feat_tokens.iterrows():
            total_t = row["in_tok"] + row["out_tok"]
            in_ratio = (row["in_tok"] / total_t * 100.0) if total_t > 0 else 0.0
            if in_ratio > 80.0 and row["spend"] > 1.0:
                anomalies.append({
                    "feature": row["feature"],
                    "input_ratio_pct": round(in_ratio, 1),
                    "spend_usd": round(float(row["spend"]), 4),
                    "recommendation": "Review context stuffing; implement prompt caching or prompt compression"
                })

        return {
            "top_drivers": top_drivers,
            "model_concentration": model_concentration,
            "efficiency_anomalies": anomalies
        }

    def get_optimization_recommendations(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Generate high-impact, data-backed FinOps optimization recommendations with audited ROI."""
        if df.empty:
            return []

        recommendations = []

        # Recommendation 1: Model Right-Sizing for doc-search
        doc_search_df = df[(df["feature"] == "doc-search") & (df["model"] == "claude-3-5-sonnet")]
        if not doc_search_df.empty:
            curr_spend = float(doc_search_df["total_cost_usd"].sum())
            in_tok = int(doc_search_df["input_tokens"].sum())
            out_tok = int(doc_search_df["output_tokens"].sum())
            cached_tok = int(doc_search_df["cached_tokens"].sum())

            # Target model: claude-3-haiku ($0.25 in / $1.25 out / $0.03 cached)
            haiku_price_in = Decimal("0.25")
            haiku_price_out = Decimal("1.25")
            haiku_price_cached = Decimal("0.03")
            sim_spend = float(
                ((Decimal(in_tok) * haiku_price_in + Decimal(out_tok) * haiku_price_out + Decimal(cached_tok) * haiku_price_cached) / Decimal("1000000"))
                .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            )
            savings = curr_spend - sim_spend
            savings_pct = (savings / curr_spend * 100.0) if curr_spend > 0 else 0.0

            recommendations.append({
                "id": "rec_rightsize_doc_search",
                "title": "Model Right-Sizing: Route 'doc-search' to Claude-3-Haiku",
                "category": "Architectural Right-Sizing",
                "priority": "HIGH",
                "workload_feature": "doc-search",
                "current_model": "claude-3-5-sonnet",
                "recommended_model": "claude-3-haiku",
                "affected_requests": len(doc_search_df),
                "current_spend_usd": round(curr_spend, 4),
                "projected_spend_usd": round(sim_spend, 4),
                "projected_savings_usd": round(savings, 4),
                "savings_pct": round(savings_pct, 1),
                "annualized_monthly_savings_usd": round(savings * 4.33, 2),
                "description": (
                    f"Downgrading search indexing from Tier-1 Claude-3.5-Sonnet to Claude-3-Haiku yields "
                    f"a projected {savings_pct:.1f}% reduction (${savings:.2f} savings per period) without compromising retrieval quality."
                ),
                "action_steps": [
                    "Update model parameter in prompt template config from 'claude-3-5-sonnet' to 'claude-3-haiku'.",
                    "Conduct retrieval benchmark verification across standard golden evaluation queries.",
                    "Deploy to staging gateway and monitor latency (Haiku expected 2-3x faster)."
                ]
            })

        # Recommendation 2: Expand Prompt Caching on repetitive workloads
        high_input_df = df[df["input_tokens"] > 5000]
        if not high_input_df.empty:
            uncached_reqs = high_input_df[high_input_df["cached_tokens"] == 0]
            if not uncached_reqs.empty:
                uncached_in_tok = int(uncached_reqs["input_tokens"].sum())
                est_cache_savings = float(uncached_in_tok * 1.5 / 1000000.0)  # ~$1.50/M avg differential
                recommendations.append({
                    "id": "rec_prompt_caching_expansion",
                    "title": "Prompt Caching: Standardize 1K+ Prefix Caching on Large Workloads",
                    "category": "Prompt Engineering",
                    "priority": "MEDIUM",
                    "workload_feature": "high-context-prompts",
                    "current_model": "multi-model",
                    "recommended_model": "cached-endpoints",
                    "affected_requests": len(uncached_reqs),
                    "current_spend_usd": round(float(uncached_reqs["total_cost_usd"].sum()), 4),
                    "projected_spend_usd": round(float(uncached_reqs["total_cost_usd"].sum()) - est_cache_savings, 4),
                    "projected_savings_usd": round(est_cache_savings, 4),
                    "savings_pct": round((est_cache_savings / float(uncached_reqs["total_cost_usd"].sum()) * 100.0) if float(uncached_reqs["total_cost_usd"].sum()) > 0 else 0.0, 1),
                    "annualized_monthly_savings_usd": round(est_cache_savings * 4.33, 2),
                    "description": (
                        f"Found {len(uncached_reqs)} high-context requests with zero cache utilization. "
                        f"Enabling prefix caching provides up to ${est_cache_savings:.2f} in immediate prompt cost reduction."
                    ),
                    "action_steps": [
                        "Order system instructions and tools at the start of prompts before user dynamic content.",
                        "Set Anthropic 'cache_control': {'type': 'ephemeral'} on static message prefixes.",
                        "Verify OpenAI prompt cache hit metrics in request logs."
                    ]
                })

        return recommendations

