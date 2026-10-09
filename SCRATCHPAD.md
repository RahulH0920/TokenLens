# TokenLens — Project Scratchpad & Features Roadmap

> **Challenge:** PS-04 | Commvault Challenge | MITK AI Vision 24H Hackathon  
> **Topic:** LLM FinOps Dashboard — Attribute LLM usage and cost to teams, features, and users  
> **Status:** 33 / 33 Pytest Tests Passing (100%) · DOM Headless Chrome Tests Passing (100%) · Branch: `rahul`

---

## 1. Hackathon Problem & Judging Criteria Alignment

| Expected Deliverable / Criterion | Implementation Status in TokenLens | Verified Files |
| :--- | :--- | :--- |
| **1. Request Logger or Proxy with Configurable Attribution** | **Complete (100%)** — Header parsing (`X-Team`, `X-Feature`, `X-User`, `X-Env`), normalization, alias matching, and fallback to `unattributed`. | [core/attribution.py](file:///d:/LLM_FINOPS/TokenLens/core/attribution.py), [config/attribution.yaml](file:///d:/LLM_FINOPS/TokenLens/config/attribution.yaml), [api/main.py](file:///d:/LLM_FINOPS/TokenLens/api/main.py) |
| **2. Cost & Usage Calculations with Pricing Table** | **Complete (100%)** — Deterministic `Decimal` fixed-point math ($C_{\text{input}} + C_{\text{output}} + C_{\text{cached}}$), missing pricing alerts, DuckDB views. | [core/cost_engine.py](file:///d:/LLM_FINOPS/TokenLens/core/cost_engine.py), [data/model_pricing.csv](file:///d:/LLM_FINOPS/TokenLens/data/model_pricing.csv) |
| **3. Interactive Dashboard with Multi-Dimensional Breakdown** | **Complete (100%)** — 6 dedicated workspaces: Command Center, Spend Detective, Savings Lab, Trend, Request Logs, Settings. | [app.py](file:///d:/LLM_FINOPS/TokenLens/app.py) |
| **4. Validation Report Comparing Expected Manifest** | **Complete (100%)** — Independent reconciliation engine verifying actuals vs golden manifest within 0.01% tolerance; 10 spot-checks. | [core/reconciliation.py](file:///d:/LLM_FINOPS/TokenLens/core/reconciliation.py), [reports/reconciliation_report.md](file:///d:/LLM_FINOPS/TokenLens/reports/reconciliation_report.md) |
| **5. Provider Coverage: Support $\ge 2$ Provider Formats** | **Complete (100%)** — Focused 3-model architecture: Real-time API key tracking for **Gemini** (`gemini-1.5-flash`) and **ChatGPT** (`gpt-4o`), plus zero-cost synthetic dummy telemetry for **Claude** (`claude-3-5-sonnet`). | [scripts/mock_proxy.py](file:///d:/LLM_FINOPS/TokenLens/scripts/mock_proxy.py), [core/usage_store.py](file:///d:/LLM_FINOPS/TokenLens/core/usage_store.py) |
| **6. Usability: Cost Drivers & Trends Easy to Interpret** | **Complete (100%)** — Streamlined time-series views, KPI cards, What-If model swap simulator, heatmaps, and outlier triage feed. | [app.py](file:///d:/LLM_FINOPS/TokenLens/app.py) |

---

## 2. Present Features (Already Built & Working)

### 1. Focused 3-Model Registry Architecture & Dual-Mode Routing
- **Active Models:**
  1. `gpt-4o` (OpenAI / ChatGPT): 🟢 **Real-Time API Key Usage** (`OPENAI_API_KEY`) — Rate: $2.50 in / $10.00 out / $1.25 cached per 1M.
  2. `gemini-1.5-flash` (Google / Gemini): 🟢 **Real-Time API Key Usage** (`GEMINI_API_KEY`) — Rate: $0.075 in / $0.30 out / $0.01875 cached per 1M.
  3. `claude-3-5-sonnet` (Anthropic): 🟣 **Dummy Data Mode** (No API Key Required) — Rate: $3.00 in / $15.00 out / $0.30 cached per 1M.
- **Problems Solved:** Clean model registry without legacy clutter; live production tracking for primary LLMs while preserving synthetic dummy telemetry for development, load testing, and offline benchmarking.
- **Routing Implementation:**
  - Real-time models forward upstream via `httpx` to `api.openai.com` and `generativelanguage.googleapis.com`, capturing real-time token metrics into DuckDB.
  - Dummy model simulates assistant responses and realistic token usage without needing an API key, returning `X-FinOps-Dummy-Mode: true`.

### 2. Configurable Attribution & Ingestion Engine
- **Tools Used:** `Pydantic v2`, `PyYAML`, `Pandas`
- **What Part It Plays:** Ingestion gateway for raw logs and live proxy calls.
- **Problems Solved:** Eliminates "shadow AI" by automatically attributing requests; resolves inconsistent naming (`"eng"`, `"Engineering"`, `"ENGINEERING"`) into canonical taxonomies; flags missing tags as `unattributed`.

### 3. Deterministic Decimal Cost Engine & Invariant Verifier
- **Tools Used:** Python `Decimal` (`ROUND_HALF_UP`), `DuckDB` SQL engine
- **What Part It Plays:** Financial calculation engine computing deterministic spend down to 6 decimal places.
- **Problems Solved:** Prevents floating-point rounding errors and silent zeroes on newly launched/unpriced models; strictly enforces mathematical invariants:
  $$\sum \text{Team Spend} = \sum \text{Model Spend} = \sum \text{Date Spend} = \text{Grand Total}$$

### 4. Streamlined Executive Command Center Dashboard
- **Tools Used:** `Streamlit`, `Plotly`, Custom SaaS CSS Design System
- **What Part It Plays:** Interactive command center for FinOps practitioners, engineering leads, and CFOs.
- **Settings Workspace Integration:** Displays model rate cards with visual source badges (`Real-Time API Key` vs `Dummy Data Mode`), live credential configuration inputs for OpenAI and Gemini, and one-click synthetic Claude request injection.

### 5. Production Security & Access Controls (Phase 3 P0 Scope)
- **Tools Used:** `FastAPI`, `Starlette`, `secrets`, `TrustedHostMiddleware`, `CORSMiddleware`
- **What Part It Plays:** Hardens the API and frontend against unauthorized access, DoS, and credential leakage.
- **Problems Solved:** Restricts bindings to `127.0.0.1`; rejects remote clients with HTTP 403 when `FINOPS_API_TOKEN` is unset; uses timing-safe token verification (`secrets.compare_digest`); enforces 64 KiB body limits and streaming safety; requires operator confirmation checkboxes before downloading sensitive logs.

### 6. Statistical Anomaly Detection Engine (FR-09)
- **Tools Used:** `NumPy`, `Pandas`, Statistical Z-scores & IQR Fences
- **What Part It Plays:** Automated early-warning system spotting abnormal spend and usage patterns.
- **Problems Solved:** Catches runaway recursive loops and prompt bloat before receiving a surprise end-of-month bill:
  - **Cost Spikes:** Flags requests with $Z \ge 3.0$ standard deviations above model baseline.
  - **Prompt Bloat:** Uses upper IQR fences ($Q75 + 3\times \text{IQR}$) to detect context window explosion.
  - **Runaway Users:** Flags individual callers driving $>20\%$ of total organization spend.

### 7. FinOps Budget Guardrails & Pre-Flight Policy Engine (FR-10)
- **Tools Used:** `Decimal`, `Pydantic v2`, REST API (`/api/v1/guardrails/check`)
- **What Part It Plays:** Team-level budget enforcement and proactive guardrails.
- **Problems Solved:** Prevents a single team from blowing through the company's shared AI budget; provides pre-flight checks returning `ALLOW`, `WARN`, or `BLOCK` before expensive API calls are made.

### 8. Headless Chrome DOM Verification Test Suite
- **Tools Used:** Chrome DevTools Protocol (CDP), `websockets`, `asyncio`
- **What Part It Plays:** Automated end-to-end browser DOM test in [tests/dom_test.py](file:///d:/LLM_FINOPS/TokenLens/tests/dom_test.py).
- **Problems Solved:** Validates full React DOM mounting, H1 headers, KPI values, tab interactions, and presence of all 3 models directly on the live dashboard.

---

## 3. What Should Be Done Next (Roadmap for Maximum Impact)

### 💡 Priority 1: Semantic Prompt Cache & Token Redundancy Analyzer
- **Problem It Solves:** 50% to 80% of enterprise LLM spend is burned on identical system prompts sent repeatedly across thousands of calls without taking advantage of prompt caching.
- **How It Works:**
  - Analyzes prompt prefix repetition and matches against the rate card's `cached_usd_per_1m` tier ($0.30 vs $3.00/1M).
  - Displays exact monthly dollar savings achievable by enabling Anthropic/OpenAI prompt caching.
- **Efficiency Gain:** Unlocks 50–70% cost reduction without downgrading model intelligence.

### ⚡ Priority 2: Pre-Flight Token Estimator (Deterministic BPE Tokenizer)
- **Problem It Solves:** Character heuristics (`len(text)//4`) have 15–25% estimation error, causing inaccurate pre-flight cost estimates.
- **How It Works:**
  - Uses `tiktoken` (cl100k_base / o200k_base) for exact token counting before calling LLMs.
- **Efficiency Gain:** 100% deterministic pre-flight cost predictability.

### 🔔 Priority 3: Webhook & Slack Incident Dispatcher
- **Problem It Solves:** Anomaly alerts currently require viewing the dashboard.
- **How It Works:**
  - Automatically posts structured JSON alerts to Slack, Discord, or webhooks whenever a `CRITICAL` anomaly or `BUDGET BREACH` occurs.

---

## 4. Architecture & Efficiency Matrix

| Component | Primary Engine | Key Optimization |
| :--- | :--- | :--- |
| **Attribution Parser** | `PyYAML` + Regex Aliasing | Single-pass regex compilation |
| **Cost Engine** | `Decimal` + `DuckDB` | Zero floating-point drift, in-memory vectorized SQL |
| **Usage Store** | `DuckDB` Embedded SQL | Direct SQL rollups without loading full dataframes |
| **Proxy Adapter** | `FastAPI` + `httpx` | Real-time upstream forwarding + dummy simulation |
| **Anomaly Detector** | `NumPy` Z-Scores + IQR | Dynamic baseline fencing without arbitrary thresholds |
| **Budget Guardrails** | `Pydantic` + REST API | $O(1)$ in-memory quota checking |
| **Security Middleware** | `Starlette` + `secrets` | Constant-time auth checks, streaming body limits |
| **Interactive Dashboard** | `Streamlit` + `Plotly` | Streamlined Trend view, cached session frames |
| **DOM Test Suite** | Chrome CDP + `websockets` | Native headless browser DOM assertion |
