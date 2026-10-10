"""CLI utility to initialize the TokenLens DuckDB analytical database.

Populates tables from the seed pricing and request datasets, calculates deterministic
costs, verifies all analytical views for graphical representation, and displays
storage specifications and PostgreSQL connection details.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.database import DuckDBAnalytics
from core.importer import DataImporter
from core.cost_engine import CostEngine


def main():
    print("=" * 70)
    print("TOKENLENS — DUCKDB ANALYTICAL DATABASE INITIALIZER")
    print("=" * 70)

    data_dir = BASE_DIR / "data"
    pricing_csv = data_dir / "model_pricing.csv"
    requests_csv = data_dir / "sample_requests.csv"
    duckdb_path = data_dir / "tokenlens_analytics.duckdb"

    # 1. Initialize Database & Storage Specification Schema
    print(f"\n[1/4] Initializing DuckDB database at: {duckdb_path}")
    db = DuckDBAnalytics(db_path=duckdb_path)
    print("  ✓ Relational tables created: model_pricing, llm_requests")
    print("  ✓ Performance indexes created on team, model, timestamp_utc, user_id")
    print("  ✓ 7 Graphical representation views compiled into query engine")

    # 2. Ingest Pricing Table
    print(f"\n[2/4] Loading model pricing rate card from: {pricing_csv.name}")
    pricing_count = db.load_pricing_csv(pricing_csv)
    print(f"  ✓ Loaded {pricing_count} active pricing rate entries into model_pricing")

    # 3. Process & Load Requests
    print(f"\n[3/4] Processing request records from: {requests_csv.name}")
    importer = DataImporter()
    pricing_records = importer.load_pricing(pricing_csv)
    raw_requests, rejects, stats = importer.load_requests(requests_csv)

    cost_engine = CostEngine()
    cost_engine.load_pricing_records(pricing_records)
    priced_requests = cost_engine.process_requests(raw_requests)

    inserted = db.insert_priced_requests(priced_requests)
    print(f"  ✓ Loaded and priced {inserted:,} requests with deterministic Decimal arithmetic")
    print(f"  ✓ Ingestion stats: {stats['loaded_rows']} valid, {len(rejects)} rejects")

    # 4. Verify Graphical Representation Views
    print("\n[4/4] Verifying analytical views for graphical representation:")
    
    # KPI Metrics
    kpi = db.get_kpi_summary()
    print(f"  • Executive KPIs:")
    print(f"    - Total Spend: ${kpi['total_spend_usd']:,.4f}")
    print(f"    - Total Volume: {kpi['total_requests']:,} requests | {kpi['total_tokens']:,} tokens")
    print(f"    - Active Dimensions: {kpi['active_teams']} teams, {kpi['active_models']} models, {kpi['active_users']} callers")

    # Team Spend Share (Donut / Pie)
    team_df = db.get_team_spend_df()
    print(f"\n  • Team Spend Share (view_team_spend_share — for Donut/Pie Charts & Detail Cards):")
    for _, row in team_df.iterrows():
        print(f"    - {row['team']:<14}: ${row['total_spend_usd']:>7.2f} ({row['spend_percentage']:>5.1f}%) | {row['request_count']:>4} reqs | Top: {row['primary_model']}")

    # Feature Breakdown (Nested / Stacked Bar)
    feat_df = db.get_feature_breakdown_df()
    print(f"\n  • Top Feature Workloads (view_feature_breakdown — for Stacked Bar Charts):")
    for _, row in feat_df.head(5).iterrows():
        print(f"    - {row['team']} / {row['feature']:<16}: ${row['total_spend_usd']:>7.2f} ({row['request_count']:>4} reqs)")

    # Daily Trends (Time Series Line / Area)
    daily_df = db.get_daily_trends_df()
    print(f"\n  • Time Series Horizon (view_daily_spend_trend — for Plotly Area / Trend Lines):")
    print(f"    - Date Range: {daily_df['usage_date'].min()} to {daily_df['usage_date'].max()} ({len(daily_df)} date/provider points)")

    # Model Distribution (Model Donut)
    model_df = db.get_model_distribution_df()
    print(f"\n  • Model Share (view_model_distribution — for Model Donut Charts):")
    for _, row in model_df.iterrows():
        print(f"    - {row['model']:<20} ({row['provider']:<8}): ${row['total_spend_usd']:>7.2f} ({row['total_tokens']:,} tokens)")

    # User Leaderboard (Horizontal Bars)
    user_df = db.get_user_leaderboard_df(limit=3)
    print(f"\n  • Top 3 Consumers (view_user_leaderboard — for User Spend Concentration Bars):")
    for _, row in user_df.iterrows():
        print(f"    - {row['user_id']:<15} ({row['team']:<12}): ${row['total_spend_usd']:>7.2f}")

    print("\n" + "=" * 70)
    print("POSTGRESQL CONNECTIVITY & EXPORT SPECIFICATION")
    print("=" * 70)
    print("To attach PostgreSQL directly to this DuckDB database:")
    print("  Use a least-privilege PostgreSQL role (not the postgres superuser).")
    print("  Set PGUSER and PGPASSWORD in the process environment, then use:")
    print("     import os")
    print("     db.attach_postgres(host='127.0.0.1', port=5432, dbname='tokenlens', user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])")
    print("     db.sync_to_postgres(pg_schema_alias='pg')")
    print("  Credentials are held in a temporary DuckDB secret; remote hosts require sslmode='verify-full'.")
    print("=" * 70)
    print(f"SUCCESS: Database created at {duckdb_path}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
