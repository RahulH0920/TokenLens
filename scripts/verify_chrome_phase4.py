"""Playwright headless Chromium verification script for TokenLens Phase 4."""

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "chrome_verification"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def run_chrome_verification():
    print("=" * 60)
    print("STARTING PLAYWRIGHT CHROMIUM VERIFICATION FOR PHASE 4")
    print("=" * 60)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        # 1. COMMAND CENTER
        print("\n[1/4] Navigating to Command Center...")
        page.goto("http://localhost:8501", wait_until="networkidle", timeout=30000)
        time.sleep(4)

        content = page.content()
        results["cc_spend"] = "$14.36" in content
        results["cc_requests"] = "1,200" in content
        results["cc_reconciliation_equation"] = "Financial Reconciliation Equation" in content and "Reconciled Exact Match" in content
        results["cc_budget_guardrails"] = "FinOps Budget Guardrails" in content and "17.50" in content and "82.1" in content
        results["cc_breakdowns_tabs"] = "Department Breakdown" in content and "Feature Breakdown" in content and "User Breakdown" in content and "Model Breakdown" in content

        print(f" - Spend $14.36 visible: {results['cc_spend']}")
        print(f" - Reconciliation Equation: {results['cc_reconciliation_equation']}")
        print(f" - Budget Guardrails & Utilization: {results['cc_budget_guardrails']}")
        print(f" - Multi-Dimensional Breakdowns Tabs: {results['cc_breakdowns_tabs']}")

        page.screenshot(path=str(OUTPUT_DIR / "1_command_center.png"), full_page=True)
        print(f" - Captured full-page: {OUTPUT_DIR / '1_command_center.png'}")

        # 2. SPEND DETECTIVE
        print("\n[2/4] Testing Spend Detective...")
        spend_det_btn = page.locator("button:has-text('Spend Detective')").first
        if spend_det_btn.count() > 0:
            spend_det_btn.click()
            time.sleep(3)
            content_sd = page.content()
            results["sd_cost_drivers"] = "Cost-Driver Root-Cause Insights" in content_sd and "Workload Cost Driver" in content_sd
            results["sd_model_concentration"] = "Top-2 Model Concentration" in content_sd or "70.9" in content_sd
            print(f" - Cost-Driver Root-Cause Insights: {results['sd_cost_drivers']}")
            print(f" - Model Concentration: {results['sd_model_concentration']}")
            page.screenshot(path=str(OUTPUT_DIR / "2_spend_detective.png"), full_page=True)
            print(f" - Captured full-page: {OUTPUT_DIR / '2_spend_detective.png'}")

        # 3. SAVINGS LAB
        print("\n[3/4] Testing Savings Lab...")
        savings_btn = page.locator("button:has-text('Savings Lab')").first
        if savings_btn.count() > 0:
            savings_btn.click()
            time.sleep(3)
            content_sl = page.content()
            results["sl_recommendation"] = "Model Right-Sizing: Route 'doc-search' to Claude-3-Haiku" in content_sl
            print(f" - Featured Optimization Recommendation: {results['sl_recommendation']}")

            demo_btn = page.locator("button:has-text('Demonstrate This Optimization in Scenario Simulator')").first
            if demo_btn.count() > 0:
                demo_btn.click()
                time.sleep(3)
                content_sl_after = page.content()
                results["sl_demo_applied"] = ("Simulated Spend" in content_sl_after or "Projected Cost Delta" in content_sl_after)
                print(f" - Simulator One-Click Auto-Demo: {results['sl_demo_applied']}")

            page.screenshot(path=str(OUTPUT_DIR / "3_savings_lab.png"), full_page=True)
            print(f" - Captured full-page: {OUTPUT_DIR / '3_savings_lab.png'}")

        # 4. REQUEST LOGS & MULTI-PROVIDER SANDBOX
        print("\n[4/4] Testing Request Logs & Multi-Provider Sandbox...")
        logs_btn = page.locator("button:has-text('Request Logs')").first
        if logs_btn.count() > 0:
            logs_btn.click()
            time.sleep(3)
            content_rl = page.content()
            results["rl_sandbox_visible"] = "Multi-Provider Format Ingestion Sandbox" in content_rl

            # Click Simulated Provider Format Tab
            sim_tab = page.locator("button[role='tab']:has-text('Simulated Provider Format')").first
            if sim_tab.count() > 0:
                sim_tab.click()
                time.sleep(1)

            # Click Ingest & Price Simulated Payload button
            ingest_btn = page.locator("button:has-text('Ingest & Price Simulated Payload')").first
            if ingest_btn.count() > 0:
                ingest_btn.click()
                time.sleep(4)
                content_rl_after = page.content()
                results["rl_simulated_ingested"] = "Successfully ingested Simulated request" in content_rl_after
                print(f" - Simulated Provider Format Ingestion & Pricing: {results['rl_simulated_ingested']}")

            page.screenshot(path=str(OUTPUT_DIR / "4_request_logs.png"), full_page=True)
            print(f" - Captured full-page: {OUTPUT_DIR / '4_request_logs.png'}")

        browser.close()

    print("\n" + "=" * 60)
    print("CHROME VERIFICATION SUMMARY:")
    all_ok = all(results.values())
    for k, v in results.items():
        status = "PASS" if v else "FAIL"
        print(f"[{status:4s}] {k}")
    print(f"\nOverall Status: {'SUCCESS' if all_ok else 'SOME CHECKS FAILED'}")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    success = run_chrome_verification()
    sys.exit(0 if success else 1)
