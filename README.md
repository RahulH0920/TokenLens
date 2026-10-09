# TokenLens — Financial Intelligence & Attribution for LLM Workloads

> **"Every token has a story."**  
> Turn raw LLM request logs into trusted cost breakdowns, actionable optimization insights, and a mathematically reconciled audit report.

---

## 1. Base Prototype Overview (P0 Scope)

This repository provides the complete, production-grade **Base Prototype** adhering to the Product Requirements Document (PRD) and Technical Requirements Document (TRD):

- **FR-01 (Capture Metadata & Tokens):** Ingestion of request IDs, timestamps, token counts (input, output, cached), latencies, models, and environments.
- **FR-02 (Configurable Attribution):** Automated parsing of team, feature, user metadata via `config/attribution.yaml` with taxonomy normalization and explicit `unattributed` tagging.
- **FR-03 (Deterministic Pricing):** Fixed-point / Decimal-safe financial calculation against model rate tables with effective-date lookups and explicit missing-pricing exception alerts.
- **FR-04 (Multi-Dimensional Aggregation):** Rollups across teams, features, models, users, and daily/hourly time series.
- **FR-05 (Filter & Request Ledger Drill-Down):** Full audit trail down to individual request records with CSV export capability.
- **FR-06 (Independent Financial Reconciliation):** Independent recomputation engine comparing calculated actuals with golden test manifests within a 0.01% tolerance.
- **Bonus Wow-Factors (FR-07, FR-08):** "Spend Detective" root-cause analysis and an interactive "Model-Swap Savings Simulator".

---

## 2. Architecture & File Structure

```
d:/LLM_FINOPS/
├── config/
│   └── attribution.yaml            # Configurable taxonomy, headers, and alias rules
├── data/
│   ├── model_pricing.csv           # Model rates (per 1M input, output, cached tokens)
│   ├── sample_requests.csv         # 1,200+ realistic requests with edge cases
│   └── golden_validation.json      # Independently computed expected totals
├── core/
│   ├── models.py                   # Pydantic schemas for requests, pricing, and checks
│   ├── attribution.py              # Metadata extraction & normalization engine
│   ├── cost_engine.py              # Decimal-safe pricing engine with DuckDB SQL views
│   ├── importer.py                 # CSV ingestion, deduplication, and reject routing
│   └── reconciliation.py           # Independent reconciliation & audit report generator
├── reports/
│   └── reconciliation_report.md    # Formal audit and reconciliation report
├── scripts/
│   ├── generate_synthetic_data.py  # Generates test dataset and golden manifests
│   ├── run_independent_validation.py # Standalone CLI for running financial reconciliation
│   └── mock_proxy.py               # FastAPI mock proxy & query endpoints
├── tests/
│   ├── test_attribution.py         # Tests for normalization, aliases, unattributed flags
│   ├── test_cost_engine.py          # Tests for PRD $0.032 math, unpriced models, invariants
│   └── test_reconciliation.py      # Tests for expected vs actual tolerance gates
├── .streamlit/
│   └── config.toml                 # Executive dark theme configuration
├── app.py                          # Full Streamlit interactive command center
├── requirements.txt
└── README.md
```

---

## 3. Cost Calculation Specification

For any model with rates defined per 1,000,000 tokens:

$$C_{\text{input}} = \frac{T_{\text{input}} \times P_{\text{input}}}{1{,}000{,}000}$$

$$C_{\text{output}} = \frac{T_{\text{output}} \times P_{\text{output}}}{1{,}000{,}000}$$

$$C_{\text{cached}} = \frac{T_{\text{cached}} \times P_{\text{cached}}}{1{,}000{,}000}$$

$$C_{\text{request}} = C_{\text{input}} + C_{\text{output}} + C_{\text{cached}}$$

### Non-Negotiable Invariants:
1. **Mathematical Invariant:** $\sum \text{Team Spend} = \sum \text{Model Spend} = \sum \text{Date Spend} = \text{Grand Total}$
2. **Row Invariant:** $\text{Source Rows} = \text{Loaded Rows} + \text{Rejected Rows}$
3. **No Silent Zeroes:** Requests using unpriced models are flagged with `missing_price = True` rather than silently calculated as $0.00.

---

## 4. Quick Start & Execution Commands

### 1. Run the Interactive Streamlit Dashboard
```bash
python -m streamlit run app.py
```
Open your browser at `http://localhost:8501` to view:
- **Usage & Cost Visuals:** KPI strip, spend by team, input vs output daily tokens, model donut, team $\times$ model heatmap.
- **Attribution & Drill-Down Ledger:** Multi-level drill-down with CSV export.
- **Reconciliation & Audit Report:** Expected vs actual verification matrix and spot-checks.
- **Spend Detective:** Top cost driver finding and What-If model swap simulator.
- **Live Request Logger:** Interactive test form to log and price requests on-the-fly.

### 2. Run Independent Financial Reconciliation CLI
```bash
python scripts/run_independent_validation.py
```
Outputs audit statistics and generates `reports/reconciliation_report.md`.

### 3. Run Automated Pytest Suite
```bash
python -m pytest
```
Runs 7 test cases across attribution, cost calculation, PRD formula benchmarks, and reconciliation invariants.

### 4. Run the Backend API for a Separate Frontend

The API serves the seeded CSV dataset and exposes JSON endpoints for dashboard KPIs, chart breakdowns, request drill-down, reconciliation, and request logging.

```bash
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for the interactive API contract. Main endpoints:

Local CORS is enabled for Streamlit on port 8501 and common React dev servers on ports 5173 and 3000. Set `FINOPS_CORS_ORIGINS` to a comma-separated list of allowed frontend origins when the UI runs elsewhere. A Streamlit Python process calling the API server-side does not rely on browser CORS.

#### Security setup

- The documented Uvicorn command binds to `127.0.0.1`; keep that binding for local development. Streamlit is also configured to bind only to `127.0.0.1`. Without `FINOPS_API_TOKEN`, the API rejects non-loopback clients even if Uvicorn is accidentally started on a public interface.
- For a private server-to-server deployment, set a random `FINOPS_API_TOKEN` of at least 32 characters. All API routes except `/health` then require `Authorization: Bearer <token>`. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Set `FINOPS_ENV=production` in production. The service refuses to start without `FINOPS_API_TOKEN`.
- Set `FINOPS_ALLOWED_HOSTS` to the API hostnames, `FINOPS_CORS_ORIGINS` to exact frontend origins, and terminate TLS at a trusted reverse proxy. Set `FINOPS_BEHIND_HTTPS_PROXY=true` only when HTTPS is enforced at that proxy.
- The API token is a service credential. Never embed it in browser JavaScript or a public mobile app. A public multi-user frontend should use an identity provider and a server-side session or API gateway that validates short-lived user tokens and enforces user/team access. Add deployment-level rate limits, secret management, and persistent storage before exposing it to the internet.
- Request bodies are limited to 64 KiB by default, POST requests must declare `Content-Length`, and demo writes are capped at 10,000 requests per process. Both limits can be adjusted with `FINOPS_MAX_BODY_BYTES` and `FINOPS_MAX_IN_MEMORY_REQUESTS`.
- Ledger CSV and reconciliation report downloads ask the operator to confirm secure handling because exports contain request identifiers, user identifiers, and usage data. Keep exported files in access-controlled storage and remove them when no longer needed.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | API status |
| `GET /api/v1/options` | Filter options for team, feature, model, user, provider, status, and environment |
| `GET /api/v1/summary` | KPI totals; accepts date, attribution, provider, status, and environment filters |
| `GET /api/v1/breakdown?by=team` | Aggregates by team, feature, model, user, or date |
| `GET /api/v1/requests` | Filterable, paginated request ledger |
| `POST /api/v1/requests` | Validate, price, and add one request to the running demo |
| `GET /api/v1/reconciliation` | Expected-versus-actual checks for the seeded dataset |

`POST /api/v1/requests` accepts `request_id`, `model`, `input_tokens`, and `output_tokens`; timestamp, provider, attribution, status, and cached tokens are optional. Duplicate IDs return HTTP 409. Requests added through this endpoint are held in memory and are cleared when the API restarts.

### 5. (Optional) Run the legacy mock proxy
```bash
python scripts/mock_proxy.py
```
Runs the OpenAI-style mock completion endpoint on `http://127.0.0.1:8000`; it is separate from the dashboard API above.

### 6. (Optional) Verify an individual DuckDB client against PostgreSQL

DuckDB and its PostgreSQL extension are already included in the Python setup used here. On a new machine, install project dependencies, then install/load the extension once:

```powershell
python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL postgres'); c.execute('LOAD postgres')"
```

Set `PGHOST`, `PGPORT` (defaults to 5432), `PGDATABASE`, `PGUSER`, and `PGPASSWORD` in that machine's protected environment or secret manager, then run:

```powershell
python scripts/check_postgres_connection.py
```

The check attaches PostgreSQL read-only and does not print credentials. For a remote host, set `PGSSLMODE=verify-full` and configure the server certificate trust. Use a dedicated PostgreSQL role with read-only grants for individual analytics clients; do not distribute an owner/admin account. The check requires a reachable server, correct firewall rules, and the connection details above.
