# TokenLens — DuckDB Storage Specification & PostgreSQL Architecture

> **Architecture:** Embedded High-Performance Analytical Engine (`DuckDB 1.5+`)  
> **Target Database:** `data/tokenlens_analytics.duckdb`  
> **Interoperability:** Native PostgreSQL Bi-Directional Integration (`duckdb-postgres`)

---

## 1. Storage Architecture Overview

TokenLens utilizes **DuckDB** as its analytical data store, providing vectorized, column-oriented SQL execution with microsecond query response times for financial rollups. The storage engine enforces **deterministic fixed-point `DECIMAL(18, 6)` arithmetic** to prevent IEEE-754 floating-point drift across multi-million token workloads.

```
┌────────────────────────────────────────────────────────┐
│                   Data Ingestion                       │
│    (CSV Batches · Real-Time Proxy · API Gateway)       │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│             DuckDB Analytical Database                 │
│         (data/tokenlens_analytics.duckdb)              │
│                                                        │
│  ┌─────────────────────────┐  ┌──────────────────────┐ │
│  │   model_pricing (Table) │  │  llm_requests (Table)│ │
│  └─────────────────────────┘  └──────────────────────┘ │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Pre-Aggregated Analytical Views          │  │
│  │  • view_kpi_metrics         • view_team_spend    │  │
│  │  • view_feature_breakdown   • view_daily_trend   │  │
│  │  • view_model_distribution  • view_user_ranking  │  │
│  │  • view_team_model_heatmap                       │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────┬──────────────────────────┬──────────────┘
               │                          │
               ▼                          ▼
┌─────────────────────────────┐  ┌───────────────────────┐
│ Graphical Dashboards        │  │ PostgreSQL Warehouse  │
│ (Plotly / Streamlit / BI)   │  │ (Direct DuckDB ATTACH)│
└─────────────────────────────┘  └───────────────────────┘
```

---

## 2. Relational Table Schemas

### 2.1. Model Pricing Rate Card (`model_pricing`)
Stores tiered pricing per 1,000,000 tokens with date-effective validity windows.

| Column | Data Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `model` | `VARCHAR` | **No** | — | Canonical model identifier (e.g. `gpt-4o`, `gemini-1.5-flash`) |
| `provider` | `VARCHAR` | **No** | — | Provider name (`openai`, `google`, `anthropic`, `internal`) |
| `input_usd_per_1m` | `DECIMAL(18, 6)` | **No** | — | Cost in USD per 1,000,000 standard input prompt tokens |
| `output_usd_per_1m` | `DECIMAL(18, 6)` | **No** | — | Cost in USD per 1,000,000 generated completion tokens |
| `cached_usd_per_1m` | `DECIMAL(18, 6)` | **No** | `0.000000` | Discounted rate per 1,000,000 cached prompt tokens |
| `effective_from` | `DATE` | **No** | — | Inclusive start date for rate applicability |
| `effective_to` | `DATE` | Yes | `NULL` | Inclusive end date (`NULL` = currently active rate) |
| `created_at` | `TIMESTAMPTZ` | **No** | `CURRENT_TIMESTAMP` | Record creation audit timestamp |

**Primary Key:** `(model, effective_from)`

```sql
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
```

---

### 2.2. Granular LLM Request Ledger (`llm_requests`)
Immutable transactional record for every logged LLM inference request.

| Column | Data Type | Nullable | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `request_id` | `VARCHAR` | **No** | — | Unique request identifier (**Primary Key**) |
| `timestamp_utc` | `TIMESTAMPTZ` | **No** | — | ISO-8601 UTC timestamp of execution |
| `provider` | `VARCHAR` | **No** | — | Provider taxonomy (`openai`, `google`, `anthropic`) |
| `model` | `VARCHAR` | **No** | — | Model executed |
| `team` | `VARCHAR` | **No** | — | Cost center / departmental tag (or `unattributed`) |
| `feature` | `VARCHAR` | **No** | — | Workload feature taxonomy (e.g. `agent-chat`, `code-review`) |
| `user_id` | `VARCHAR` | **No** | — | Caller identifier / service account |
| `env` | `VARCHAR` | **No** | `'production'` | Runtime environment (`production`, `staging`, `dev`) |
| `status` | `VARCHAR` | **No** | `'success'` | HTTP / completion status |
| `latency_ms` | `INTEGER` | **No** | `0` | Latency duration in milliseconds |
| `input_tokens` | `BIGINT` | **No** | `0` | Uncached input prompt tokens |
| `output_tokens` | `BIGINT` | **No** | `0` | Generated completion tokens |
| `cached_tokens` | `BIGINT` | **No** | `0` | Context tokens retrieved from prompt cache |
| `total_tokens` | `BIGINT` | **No** | Generated | **Virtual column:** `(input_tokens + output_tokens)` |
| `input_cost_usd` | `DECIMAL(18, 6)` | **No** | `0.000000` | Deterministic prompt cost ($T_{\text{in}} \times P_{\text{in}} / 10^6$) |
| `output_cost_usd` | `DECIMAL(18, 6)` | **No** | `0.000000` | Deterministic completion cost ($T_{\text{out}} \times P_{\text{out}} / 10^6$) |
| `cached_cost_usd` | `DECIMAL(18, 6)` | **No** | `0.000000` | Discounted cache cost ($T_{\text{cached}} \times P_{\text{cached}} / 10^6$) |
| `total_cost_usd` | `DECIMAL(18, 6)` | **No** | `0.000000` | Sum of input + output + cached costs |
| `missing_price` | `BOOLEAN` | **No** | `FALSE` | Flagged `TRUE` if model rate was unpriced |
| `is_unattributed`| `BOOLEAN` | **No** | `FALSE` | Flagged `TRUE` if team or feature tags were missing |
| `source` | `VARCHAR` | **No** | `'batch_log'` | Ingestion origin (`proxy`, `batch_log`, `sdk`) |

**Indexes:**
- `idx_requests_team`: `CREATE INDEX idx_requests_team ON llm_requests(team);`
- `idx_requests_model`: `CREATE INDEX idx_requests_model ON llm_requests(model);`
- `idx_requests_ts`: `CREATE INDEX idx_requests_ts ON llm_requests(timestamp_utc);`
- `idx_requests_user`: `CREATE INDEX idx_requests_user ON llm_requests(user_id);`

---

## 3. Pre-Aggregated Views for Graphical Representation

These analytical views are compiled directly into DuckDB, allowing dashboard frontends (Plotly, Streamlit, Grafana, Apache Superset) to execute $O(1)$ queries without runtime Pandas aggregations.

### 3.1. Executive KPI Strip (`view_kpi_metrics`)
- **Visual Target:** Top-level executive metric cards (KPI Strip)
- **Output:** Total Spend, Volume, Tokens, Avg Cost/Req, Active Entities

```sql
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
```

---

### 3.2. Team Spend & Share (`view_team_spend_share`)
- **Visual Target:** Department Spend Share Donut (`px.pie`) & Vertical Details Cards
- **Output:** Department spend, % share of organization, total volume, primary model

```sql
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
```

---

### 3.3. Feature Workload Breakdown (`view_feature_breakdown`)
- **Visual Target:** Team-specific Feature Donut charts (`px.pie`) & Stacked Workload Bars
- **Output:** Department $\times$ Feature financial breakdown

```sql
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
```

---

### 3.4. Daily Spend & Token Trend (`view_daily_spend_trend`)
- **Visual Target:** Time-Series Spend Line Chart (`px.line`) & Stacked Area Token Horizon
- **Output:** Daily chronological rollups by provider

```sql
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
```

---

### 3.5. Model Distribution & Efficiency (`view_model_distribution`)
- **Visual Target:** Model Volume & Cost Donut charts
- **Output:** Model spend, input vs output tokens, avg unit cost

```sql
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
```

---

### 3.6. Team $\times$ Model Matrix (`view_team_model_heatmap`)
- **Visual Target:** Plotly Matrix Heatmap (`px.imshow`)
- **Output:** Cross-tabulation of spend per team and model combination

```sql
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
```

---

### 3.7. User Consumption Ranking (`view_user_leaderboard`)
- **Visual Target:** Top 10 User Spend Horizontal Bar Chart
- **Output:** Individual caller attribution ranking

```sql
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
```

---

## 4. PostgreSQL Connection & Interoperability

DuckDB connects natively to PostgreSQL via its official **`postgres` extension**, allowing seamless querying of PostgreSQL tables from DuckDB and syncing DuckDB analytics into PostgreSQL without intermediary CSV exports.

### 4.1. DuckDB Native PostgreSQL ATTACH

Use the Python helper below for credential handling. It stores credentials in a
temporary DuckDB secret and keeps them out of the `ATTACH` connection string. Use
a least-privilege PostgreSQL role rather than the `postgres` superuser. Remote
PostgreSQL connections require `sslmode="verify-full"`.

```sql
INSTALL postgres;
LOAD postgres;
```

---

### 4.2. Python Programmatic Integration (`core.database.DuckDBAnalytics`)

```python
import os
from core.database import DuckDBAnalytics

# 1. Connect to DuckDB
db = DuckDBAnalytics("data/tokenlens_analytics.duckdb")

# 2. Attach external PostgreSQL instance
db.attach_postgres(
    host="127.0.0.1",
    port=5432,
    dbname="tokenlens",
    user=os.environ["PGUSER"],
    password=os.environ["PGPASSWORD"],
    pg_schema_alias="pg"
)

# 3. Synchronize rate cards & request ledger to PostgreSQL
stats = db.sync_to_postgres(pg_schema_alias="pg")
print(f"Synced {stats['requests_synced']} requests and {stats['pricing_synced']} rates to PostgreSQL!")
```

---

### 4.3. PostgreSQL Mirror DDL
To initialize the exact schema on PostgreSQL before ingestion:

```sql
CREATE SCHEMA IF NOT EXISTS tokenlens;
SET search_path TO tokenlens, public;

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

CREATE INDEX IF NOT EXISTS idx_requests_team ON llm_requests(team);
CREATE INDEX IF NOT EXISTS idx_requests_model ON llm_requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON llm_requests(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_requests_user ON llm_requests(user_id);
```

---

## 5. Verification & Execution Commands

```bash
# 1. Initialize and populate DuckDB database with analytical views
python scripts/init_duckdb.py

# 2. Run automated test suite verifying views & calculations
python -m pytest tests/test_database.py
```
