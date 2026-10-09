"""TokenLens Demo Rehearsal & High-Resolution Screenshot Capture Utility.

Automates the end-to-end presentation flow across Streamlit (8501) and FastAPI (8000),
capturing crisp, presentation-grade screenshots in `reports/demo_kit/` for offline/backup demo use.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "demo_kit"
STREAMLIT_URL = "http://localhost:8501"
FASTAPI_DOCS_URL = "http://127.0.0.1:8000/docs"


def capture_all():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[*] Starting Demo Rehearsal & Screenshot Suite to {OUTPUT_DIR}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 950},
            device_scale_factor=2,  # Retina crispness
        )
        page = context.new_page()

        # -------------------------------------------------------------
        # STEP 1: Command Center - Top KPIs & Invariant Banner
        # -------------------------------------------------------------
        print("\n[*] 1/6 Capturing Command Center Overview...")
        page.goto(STREAMLIT_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        btn_cc = page.locator("button:has-text('Command Center')").first
        if btn_cc.count() > 0:
            btn_cc.click()
            time.sleep(2)

        page.evaluate(
            """() => {
            const main = document.querySelector('section.stMain');
            if (main) main.scrollTop = 0;
            window.scrollTo(0, 0);
        }"""
        )
        time.sleep(1)
        path1 = OUTPUT_DIR / "01_command_center_overview.png"
        page.screenshot(path=str(path1), full_page=False)
        print(f"[+] Saved {path1.name}")

        # -------------------------------------------------------------
        # STEP 2: Command Center - Budget Guardrails & Attribution Breakdown
        # -------------------------------------------------------------
        print("\n[*] 2/6 Capturing Budget Guardrails & Attribution Breakdowns...")
        # Scroll to Budget Guardrails section
        guardrails_header = page.locator("text=FinOps Budget Guardrails & Department Utilization").first
        if guardrails_header.count() > 0:
            guardrails_header.scroll_into_view_if_needed()
            time.sleep(1.5)
        else:
            page.evaluate(
                """() => {
                const main = document.querySelector('section.stMain');
                if (main) main.scrollTop = 1200;
            }"""
            )
            time.sleep(1.5)

        path2 = OUTPUT_DIR / "02_budget_guardrails_and_breakdowns.png"
        page.screenshot(path=str(path2), full_page=False)
        print(f"[+] Saved {path2.name}")

        # -------------------------------------------------------------
        # STEP 3: Spend Detective - Root-Cause Drivers & Anomalies
        # -------------------------------------------------------------
        print("\n[*] 3/6 Capturing Spend Detective Root-Cause Insights...")
        spend_det_btn = page.locator("button:has-text('Spend Detective')").first
        if spend_det_btn.count() > 0:
            spend_det_btn.click()
            time.sleep(3)

        # Scroll to top of Spend Detective
        page.evaluate(
            """() => {
            const main = document.querySelector('section.stMain');
            if (main) main.scrollTop = 0;
            window.scrollTo(0, 0);
        }"""
        )
        time.sleep(1)
        path3 = OUTPUT_DIR / "03_spend_detective_insights.png"
        page.screenshot(path=str(path3), full_page=False)
        print(f"[+] Saved {path3.name}")

        # -------------------------------------------------------------
        # STEP 4: Savings Lab - Optimization Recommendations & What-If
        # -------------------------------------------------------------
        print("\n[*] 4/6 Capturing Savings Lab Recommendation & What-If...")
        savings_btn = page.locator("button:has-text('Savings Lab')").first
        if savings_btn.count() > 0:
            savings_btn.click()
            time.sleep(3)

        # Click the Apply Recommendation button to showcase simulation pre-fill
        sim_btn = page.locator('button:has-text("Apply to Simulator Below")').first
        if sim_btn.count() > 0:
            sim_btn.click()
            time.sleep(2)

        page.evaluate(
            """() => {
            const main = document.querySelector('section.stMain');
            if (main) main.scrollTop = 0;
            window.scrollTo(0, 0);
        }"""
        )
        time.sleep(1)
        path4 = OUTPUT_DIR / "04_savings_lab_recommendation.png"
        page.screenshot(path=str(path4), full_page=False)
        print(f"[+] Saved {path4.name}")

        # -------------------------------------------------------------
        # STEP 5: Request Logs - Quarantine Auditing & Multi-Provider Ingestion
        # -------------------------------------------------------------
        print("\n[*] 5/6 Capturing Request Logs & Multi-Provider Sandbox...")
        logs_btn = page.locator("button:has-text('Request Logs')").first
        if logs_btn.count() > 0:
            logs_btn.click()
            time.sleep(3)

        # Open quarantine expander
        quarantine_expander = page.locator("text=Quarantined Ingestion Ledger").first
        if quarantine_expander.count() > 0:
            quarantine_expander.click()
            time.sleep(1)

        # Ingest a simulated sample
        ingest_btn = page.locator('button:has-text("Ingest via Adapter")').first
        if ingest_btn.count() > 0:
            ingest_btn.click()
            time.sleep(2.5)

        sandbox_header = page.locator("text=Multi-Provider Format Ingestion Sandbox").first
        if sandbox_header.count() > 0:
            sandbox_header.scroll_into_view_if_needed()
            time.sleep(1)

        path5 = OUTPUT_DIR / "05_request_logs_quarantine_and_multi_provider.png"
        page.screenshot(path=str(path5), full_page=False)
        print(f"[+] Saved {path5.name}")

        # -------------------------------------------------------------
        # STEP 6: FastAPI Swagger Documentation
        # -------------------------------------------------------------
        print("\n[*] 6/6 Capturing FastAPI Interactive Swagger UI...")
        page.goto(FASTAPI_DOCS_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        path6 = OUTPUT_DIR / "06_api_swagger_docs.png"
        page.screenshot(path=str(path6), full_page=False)
        print(f"[+] Saved {path6.name}")

        browser.close()
        print(f"\n[+] All 6 Demo Kit Screenshots captured successfully in {OUTPUT_DIR}!")


if __name__ == "__main__":
    capture_all()
