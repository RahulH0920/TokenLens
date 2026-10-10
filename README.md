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
- **FR-09 (Statistical Anomaly Detection):** Z-score and IQR-fence outlier detection flagging cost spikes, prompt-token bloat, and runaway user concentration.
- **FR-10 (Budget Guardrails & Policy Enforcement):** Per-team financial budgets, dynamic utilization tracking, and pre-flight evaluation (ALLOW, WARN, BLOCK).

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
│   └── validation_report.md        # Validation report comparing dashboard totals with known expected values
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
- **Validation Report:** Comparing dashboard totals with known expected values (verification matrix and spot-checks).
- **Spend Detective:** Top cost driver finding and What-If model swap simulator.
- **Live Request Logger:** Interactive test form to log and price requests on-the-fly.

### 2. Run Independent Validation CLI
```bash
python scripts/run_independent_validation.py
```
Outputs audit statistics and generates `reports/validation_report.md`.

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

Main endpoints:

Local CORS is enabled for Streamlit on port 8501 and common React dev servers on ports 5173 and 3000. Set `FINOPS_CORS_ORIGINS` to a comma-separated list of allowed frontend origins when the UI runs elsewhere. A Streamlit Python process calling the API server-side does not rely on browser CORS.

#### Security setup

- The documented Uvicorn command binds to `127.0.0.1`; keep that binding for local development. Streamlit is also configured to bind only to `127.0.0.1`. Without `FINOPS_API_TOKEN`, the API rejects non-loopback clients even if Uvicorn is accidentally started on a public interface.
- API data routes require credentials even on loopback. Create the initial organization-head account in Streamlit, then use `POST /auth/login` with its username and password. The response contains a one-hour bearer credential; pass it as `Authorization: Bearer <access_token>`. `POST /auth/logout` revokes that credential. API sessions use the same role and team grants as the dashboard.
- API bootstrap is also available at `POST /auth/bootstrap`, but it is disabled unless a random `TOKENLENS_BOOTSTRAP_TOKEN` of at least 11 characters is configured. Generate an 11-character token with `python -c "import secrets; print(secrets.token_urlsafe(8))"`. Use `GET /auth/status` to check whether initial setup is complete; it does not reveal credentials.
- For server-to-server clients, set a random `FINOPS_API_TOKEN` of at least 32 characters. This service credential has organization-wide access; keep it server-side. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Remote clients still need `FINOPS_API_TOKEN` for access and for credential login; without it the API rejects non-loopback clients.
- Set `FINOPS_API_SESSION_TTL_SECONDS` to a value from 60 to 86400 to change the account session lifetime (default 3600 seconds).
- Login attempts are throttled after five failures in five minutes, with a five-minute lockout by default. `FINOPS_AUTH_MAX_ATTEMPTS`, `FINOPS_AUTH_WINDOW_SECONDS`, and `FINOPS_AUTH_LOCKOUT_SECONDS` tune the limits. Remote account login can skip sending the service token only when `FINOPS_ALLOW_REMOTE_LOGIN=true`; the server still requires `FINOPS_API_TOKEN` to be configured and should only be exposed behind HTTPS.
- Set `FINOPS_ENV=production` in production. The service refuses to start without `FINOPS_API_TOKEN`.
- Set `FINOPS_ALLOWED_HOSTS` to the API hostnames, `FINOPS_CORS_ORIGINS` to exact frontend origins, and terminate TLS at a trusted reverse proxy. Set `FINOPS_BEHIND_HTTPS_PROXY=true` only when HTTPS is enforced at that proxy.
- The API token is a service credential. Never embed it in browser JavaScript or a public mobile app. A public multi-user frontend should use an identity provider and a server-side session or API gateway that validates short-lived user tokens and enforces user/team access. Add deployment-level rate limits, secret management, and persistent storage before exposing it to the internet.
- Request bodies are limited to 64 KiB by default, POST requests must declare `Content-Length`, and demo writes are capped at 10,000 requests per process. Both limits can be adjusted with `FINOPS_MAX_BODY_BYTES` and `FINOPS_MAX_IN_MEMORY_REQUESTS`.
- Ledger CSV and validation report downloads ask the operator to confirm secure handling because exports contain request identifiers, user identifiers, and usage data. Keep exported files in access-controlled storage and remove them when no longer needed.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | API status |
| `GET /auth/status` | Public setup-completion status |
| `POST /auth/bootstrap` | Create the first organization head with the one-time setup secret |
| `POST /auth/login` | Exchange account credentials for a short-lived bearer session |
| `GET /auth/me` | Return the current account's role and team grants |
| `POST /auth/logout` | Revoke the current account bearer session |
| `GET /api/v1/options` | Filter options for team, feature, model, user, provider, status, and environment |
| `GET /api/v1/summary` | KPI totals; accepts date, attribution, provider, status, and environment filters |
| `GET /api/v1/breakdown?by=team` | Aggregates by team, feature, model, user, or date |
| `GET /api/v1/requests` | Filterable, paginated request ledger |
| `POST /api/v1/requests` | Validate, price, and add one request to the running demo |
| `GET /api/v1/validation` | Validation report comparing dashboard totals with known expected values |
| `GET /api/v1/reconciliation` | Alias for validation checks comparing dashboard totals with known expected values |
| `GET /api/v1/anomalies` | Statistical anomalies (cost spikes, token bloat, runaway users) |
| `GET /api/v1/guardrails` | Live team budget utilization and health states |
| `POST /api/v1/guardrails/check` | Pre-flight request evaluation against team quotas (ALLOW/WARN/BLOCK) |

`POST /api/v1/requests` accepts `request_id`, `model`, `input_tokens`, and `output_tokens`; timestamp, provider, attribution, status, and cached tokens are optional. Duplicate IDs return HTTP 409. Requests added through this endpoint are held in memory and are cleared when the API restarts.

### 5. Run the OpenAI + Gemini usage proxy

The local proxy forwards requests to the real provider APIs and captures the token usage returned by each provider. It does not fetch historical usage for calls made outside TokenLens. Copy `.env.example` to `.env` and enter provider keys locally; `.env` is Git-ignored. Never put keys in source, Streamlit settings, or the DuckDB file.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
python scripts/mock_proxy.py
```

The proxy listens on `http://127.0.0.1:8000`, separate from the dashboard API above. Live requests can incur charges on your provider accounts. It uses `FINOPS_DUCKDB_PATH` when set; otherwise it creates `data/tokenlens_usage.duckdb`. The database is ignored by Git. On an empty database, it imports the supplied sample request data once, then appends live API usage. Restarting the proxy preserves records. The `provider_host` column identifies whether a record came from `api.openai.com`, `generativelanguage.googleapis.com`, or the sample CSV.

| Provider | Request endpoint | Example priced model |
| --- | --- | --- |
| OpenAI Chat Completions | `POST /v1/chat/completions` | `gpt-4o-mini` |
| Gemini `generateContent` | `POST /v1beta/models/{model}:generateContent` | `gemini-2.5-flash` |

Both endpoints accept `X-Team`, `X-Feature`, `X-User`, and `X-Env` attribution headers. Successful responses retain the provider's native body and add a `finops_attribution` object plus `X-FinOps-Cost-USD` and `X-FinOps-Team` headers. The ledger stores usage and attribution metadata, not prompts or API keys. OpenAI and Gemini prompt usage includes cached tokens; TokenLens separates cached tokens from regular input before applying the cached rate. Gemini thinking tokens are included with output tokens. Missing rates are marked `missing_price` and cost is not reported as a valid zero.

Inspect the local database through the proxy:

```text
GET /usage/summary
GET /usage/breakdown?by=provider
GET /usage/requests?provider=google
```

Costs use the rates in `data/model_pricing.csv`; they are TokenLens estimates of token charges and do not include provider-side free tiers, discounts, taxes, or tool charges. Review the provider's current rates before using the totals for billing. The included `gemini-2.5-flash` paid-tier rates follow Google's published text-token schedule; update the CSV if your account uses another model or service tier. See [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat) and [Gemini generateContent](https://ai.google.dev/api/generate-content).

Example requests:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-Team: engineering" -H "X-Feature: code-review" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Explain a hash map briefly."}]}'

curl http://127.0.0.1:8000/v1beta/models/gemini-2.5-flash:generateContent \
  -H "Content-Type: application/json" -H "X-Team: analytics" -H "X-Feature: summarisation" \
  -d '{"contents":[{"role":"user","parts":[{"text":"Explain a hash map briefly."}]}]}'
```
