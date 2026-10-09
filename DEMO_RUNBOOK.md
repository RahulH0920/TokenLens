# TokenLens — Live Demo Runbook & Rehearsal Guide
**Role:** FinOps Lead / Engineering Executive  
**Platform:** TokenLens AI FinOps & Token Intelligence Platform  
**Architecture:** FastAPI (Port 8000) · Streamlit Dashboard (Port 8501) · DuckDB Embedded Engine  
**Reconciliation Status:** ✅ 100% Invariants Verified against Golden Manifest  

---

## 1. Executive Summary & Value Proposition
Large organizations adopting LLMs face a critical governance challenge: **uncontrolled cloud spend with zero unit economics transparency**. 
Standard cloud bills aggregate token usage into opaque line items, failing to reveal:
- Which internal departments (Engineering, Research, Product) consume what budget.
- Which features (`doc-search`, `summarisation`, `agent-chat`) drive cost vs return on investment.
- How prompt caching is priced, or where silent unpriced model requests corrupt accounting.

**TokenLens** delivers deterministic attribution, real-time budget guardrails, mathematical cost reconciliation, and automated model right-sizing recommendations.

---

## 2. Live Demo Script (5-Minute Walkthrough)

### Stop 1: Executive Command Center (`01_command_center_overview.png`)
* **URL:** `http://localhost:8501` -> Select `Command Center`
* **Narrative:** 
  > *"Welcome to TokenLens. The first thing an engineering executive or CFO sees is our verified financial truth. Across our active billing cycle, our organization has executed 1,200 valid LLM requests consuming 4.71M input tokens and 1.01M output tokens for a grand total spend of $14.36."*
* **Key Visuals to Highlight:**
  1. **Financial Reconciliation Equation:**  
     $$\text{Input Cost (\$7.79)} + \text{Output Cost (\$6.39)} + \text{Cached Cost (\$0.18)} = \mathbf{\$14.36}$$
     *(Point to the green badge: `Reconciled Exact Match`).*
  2. **Ingestion Quality & Quarantine:**  
     Point out `5 duplicate request IDs quarantined · 8 total rejects`. Emphasize that TokenLens never allows duplicates or negative tokens to poison the financial ledger.
  3. **No-Silent-Zeroes Guarantee:**  
     Show the warning banner: `6 requests with unpriced models (unsupported-legacy-model-v0)`. Explain that TokenLens explicitly surfaces unpriced traffic as an unrated exception rather than silently dropping or zeroing it.

---

### Stop 2: FinOps Budget Guardrails & Multi-Dimensional Breakdowns (`02_budget_guardrails_and_breakdowns.png`)
* **Action:** Scroll down in `Command Center`.
* **Narrative:**
  > *"FinOps is about accountability. Here we see our organizational budget cap: $17.50 allocated monthly cap, with $14.36 actual burn—putting our organization at 82.1% utilization, triggering a Warning threshold."*
* **Key Visuals to Highlight:**
  1. **Department Threshold Matrix:**
     - `Research`: $3.13 / $3.50 (89.3% - Near Capacity 🟡)
     - `Marketing`: $2.18 / $2.50 (87.3% - Near Capacity 🟡)
     - `Support`: $2.46 / $3.00 (82.1% - Near Capacity 🟡)
     - `Unattributed`: $1.0012 / $1.00 (100.1% - Over Budget 🔴)
     - `Product` & `Engineering`: Safe / On Track 🟢
  2. **Attribution Breakdowns:**
     - Toggle between **Department**, **Feature**, **User**, and **Model** tabs to show multi-dimensional slice-and-dice.

---

### Stop 3: Spend Detective — Root-Cause Investigation (`03_spend_detective_insights.png`)
* **Action:** Click `Spend Detective` in top navigation.
* **Narrative:**
  > *"When spend rises, teams need to know why in seconds, not weeks. Spend Detective performs automated root-cause attribution."*
* **Key Visuals to Highlight:**
  1. **#1 Workload Cost Driver:** `Research / summarisation` driving **$1.36 (9.5% of org spend)**.
  2. **Architecture Concentration:** **74.9%** of organizational budget is concentrated in just two frontier models: Claude 3.5 Sonnet and GPT-4o.
  3. **Efficiency Anomalies:** 6 requests detected with high input-to-output ratios (context stuffing).

---

### Stop 4: Savings Lab — Optimization Recommendations & What-If (`04_savings_lab_recommendation.png`)
* **Action:** Click `Savings Lab` in top navigation.
* **Narrative:**
  > *"TokenLens doesn't just display historical costs—it acts as an automated optimization advisor. Here is our featured recommendation."*
* **Key Visuals to Highlight:**
  1. **Top Recommendation Card:**  
     *Right-size `doc-search` from Claude-3-5-Sonnet to Claude-3-Haiku*.  
     - Projected ROI: **87.5% Cost Reduction**  
     - Net Savings: **$2.74 per period (~$11.86/mo run-rate)**  
     - Restores Research department budget from 89.3% down to 18.3%.
  2. **Interactive What-If Scenario Builder:**  
     Click `"👉 Demonstrate This Optimization in Scenario Simulator Below"`. Show the live dynamic recalculation with explicit `[ESTIMATE / SCENARIO PROJECTION]` labeling.

---

### Stop 5: Request Logs & Multi-Provider Ingestion Sandbox (`05_request_logs_quarantine_and_multi_provider.png`)
* **Action:** Click `Request Logs` in top navigation.
* **Narrative:**
  > *"Under the hood, TokenLens operates as an enterprise ingestion gateway. Notice our Quarantined Ledger isolating malformed and duplicate requests."*
* **Key Visuals to Highlight:**
  1. **Quarantined Ledger:** Expand `🛡️ Quarantined Ingestion Ledger (8 records)` showing the 5 duplicates (`req_0011`, `req_0021`, etc.) and 3 malformed schema rejections.
  2. **Multi-Provider Format Ingestion Sandbox:**  
     Show live support for **OpenAI (`chat.completion`)**, **Anthropic (`messages`)**, and **Simulated Provider** formats.  
     Click `"Ingest via Adapter"` to show instant rating, cost calculation, and banner confirmation.

---

### Stop 6: Production FastAPI Backend & Swagger UI (`06_api_swagger_docs.png`)
* **URL:** `http://127.0.0.1:8000/docs`
* **Narrative:**
  > *"All dashboard analytics are backed by our high-performance FastAPI microservice, exposing RESTful endpoints for CI/CD pipelines, billing systems, and Slack/PagerDuty alerts."*
* **Endpoints:**
  - `GET /health` — Service health check
  - `GET /api/v1/summary` — Enterprise totals including cached breakdowns
  - `GET /api/v1/budgets` — Departmental budget guardrails and alert thresholds
  - `GET /api/v1/insights` — Cost-driver root cause and model concentration
  - `GET /api/v1/recommendations` — Automated optimization suggestions
  - `POST /api/v1/ingest/provider` — Multi-provider format ingestion gateway

---

## 3. Financial Invariant Proofs

| Invariant Rule | Expected Formula | Verified Value | Status |
| :--- | :--- | :--- | :---: |
| **Component Additivity** | $\text{In Cost} + \text{Out Cost} + \text{Cached Cost} = \text{Total Cost}$ | $\$7.7940 + \$6.3934 + \$0.1760 = \$14.3634$ | ✅ PASS |
| **Row Count Conservation** | $\text{Source} = \text{Loaded} + \text{Quarantined Duplicates} + \text{Schema Rejected}$ | $1,208 = 1,200 + 5 + 3$ | ✅ PASS |
| **Cross-Cut Invariance** | $\sum \text{Team Spend} = \sum \text{Model Spend} = \text{Grand Total}$ | $\$14.3634 = \$14.3634 = \$14.3634$ | ✅ PASS |
| **No-Silent-Zeroes** | Requests missing rate card entries are isolated as exceptions | 6 unpriced requests flagged | ✅ PASS |
| **Golden Manifest Parity** | 100% agreement with independently hand-calculated sample | Hand-audit verified to 6 decimals | ✅ PASS |

---

## 4. Disaster Recovery & Contingency Plan (Demo Backup Kit)

If the live environment or network fails during the presentation:

### Option A: 1-Command Pristine State Reset
If data in the live app was altered or corrupted during live user testing:
```bash
python3 scripts/restore_backup.py
```
*This instantaneously restores pristine `data/sample_requests.csv`, `data/model_pricing.csv`, `config/attribution.yaml`, and `data/golden_validation.json` from `data/backup/`.*

### Option B: High-Resolution Offline Screenshot Presentation
All screens are pre-captured at Retina 2x resolution and ready for presentation:
1. [01_command_center_overview.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/01_command_center_overview.png)
2. [02_budget_guardrails_and_breakdowns.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/02_budget_guardrails_and_breakdowns.png)
3. [03_spend_detective_insights.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/03_spend_detective_insights.png)
4. [04_savings_lab_recommendation.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/04_savings_lab_recommendation.png)
5. [05_request_logs_quarantine_and_multi_provider.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/05_request_logs_quarantine_and_multi_provider.png)
6. [06_api_swagger_docs.png](file:///Users/gsunilkumar/Desktop/tokenlens/TokenLens/reports/demo_kit/06_api_swagger_docs.png)

---

## 5. Service Operation Commands

```bash
# Full test suite regression
python3 -m pytest -v

# Independent reconciliation audit
python3 scripts/run_independent_validation.py

# Launch FastAPI Backend (Port 8000)
python3 -m uvicorn api.main:app --port 8000 --host 127.0.0.1

# Launch Streamlit Frontend (Port 8501)
python3 -m streamlit run app.py --server.port 8501 --server.headless true
```
