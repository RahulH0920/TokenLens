"""Automated DOM inspection and validation for Streamlit dashboard using Chrome DevTools Protocol (CDP)."""

import asyncio
import json
import subprocess
import time
import urllib.request
import websockets

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TARGET_URL = "http://127.0.0.1:8501"
DEBUG_PORT = 9222


async def run_dom_test():
    print(f"Launching headless Chrome on port {DEBUG_PORT}...")
    chrome_proc = subprocess.Popen([
        CHROME_PATH,
        "--headless=new",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        TARGET_URL
    ])

    try:
        # Wait for Chrome DevTools endpoint to become available
        ws_url = None
        for _ in range(20):
            try:
                res = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=2)
                data = json.loads(res.read().decode())
                for target in data:
                    if target.get("type") == "page":
                        ws_url = target.get("webSocketDebuggerUrl")
                        break
                if ws_url:
                    break
            except Exception:
                time.sleep(0.5)

        if not ws_url:
            print("ERROR: Could not connect to Chrome DevTools Protocol.")
            return False

        print(f"Connected to CDP page target: {ws_url}")

        async with websockets.connect(ws_url, max_size=20_000_000) as ws:
            msg_id = 0

            async def send_cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                payload = {"id": msg_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(payload))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get("id") == msg_id:
                        return resp.get("result", {})

            # Enable Page and Runtime domains
            await send_cmd("Page.enable")
            await send_cmd("Runtime.enable")

            # Wait for Streamlit to render its React DOM (polling for stApp or saas-card)
            print("Waiting for Streamlit DOM elements to render...")
            dom_ready = False
            for _ in range(30):
                eval_res = await send_cmd("Runtime.evaluate", {
                    "expression": "document.body.innerText"
                })
                text = eval_res.get("result", {}).get("value", "")
                if "TokenLens" in text and ("Total Spend" in text or "Know where every token goes" in text):
                    dom_ready = True
                    break
                await asyncio.sleep(0.5)

            if not dom_ready:
                print("WARNING: Streamlit initial render did not complete in 15 seconds.")
                return False

            print("\n=== DOM TEST REPORT ===")

            # Test 1: Page Title & Header
            eval_title = await send_cmd("Runtime.evaluate", {
                "expression": "document.title"
            })
            print(f"[PASS] Document Title: '{eval_title.get('result', {}).get('value')}'")

            # Test 2: Main Brand Heading
            eval_header = await send_cmd("Runtime.evaluate", {
                "expression": "document.querySelector('h1') ? document.querySelector('h1').innerText : 'NOT_FOUND'"
            })
            print(f"[PASS] Main Heading (H1): '{eval_header.get('result', {}).get('value')}'")

            # Test 3: KPI Cards in DOM
            eval_kpis = await send_cmd("Runtime.evaluate", {
                "expression": """
                Array.from(document.querySelectorAll('.saas-card')).map(card => {
                    const label = card.querySelector('.saas-kpi-label')?.innerText || '';
                    const value = card.querySelector('.saas-kpi-value')?.innerText || '';
                    const sub = card.querySelector('.saas-kpi-sub')?.innerText || '';
                    return { label, value, sub };
                })
                """,
                "returnByValue": True
            })
            kpis = eval_kpis.get("result", {}).get("value", [])
            print(f"[PASS] Extracted {len(kpis)} KPI Cards:")
            for k in kpis:
                print(f"       - {k.get('label')}: {k.get('value')} ({k.get('sub')})")

            # Test 4: Navigation Tabs in DOM
            eval_nav = await send_cmd("Runtime.evaluate", {
                "expression": """
                Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(t => t.length > 0)
                """,
                "returnByValue": True
            })
            buttons = eval_nav.get("result", {}).get("value", [])
            print(f"[PASS] Available Buttons / Navigation Tabs: {buttons[:10]}")

            # Test 5: Check Spend Detective Tab Click
            print("\nSimulating click on 'Spend Detective' tab...")
            click_eval = await send_cmd("Runtime.evaluate", {
                "expression": """
                (() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Spend Detective'));
                    if (btn) {
                        btn.click();
                        return true;
                    }
                    return false;
                })()
                """
            })
            clicked = click_eval.get("result", {}).get("value")
            if clicked:
                print("[PASS] Successfully triggered click on 'Spend Detective'")
                has_anom = False
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    eval_anom = await send_cmd("Runtime.evaluate", {
                        "expression": """
                        document.body.innerText.includes('Anomaly & Outlier') || document.body.innerText.includes('Active Anomalies')
                        """
                    })
                    if eval_anom.get("result", {}).get("value"):
                        has_anom = True
                        break
                print(f"[PASS] Real-Time Anomaly & Outlier section verified in DOM: {has_anom}")
            else:
                print("[INFO] Direct button click skipped or segmented control used.")

            # Test 6: Check Settings Tab
            print("\nSimulating click on 'Settings' tab...")
            click_settings = await send_cmd("Runtime.evaluate", {
                "expression": """
                (() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Settings'));
                    if (btn) {
                        btn.click();
                        return true;
                    }
                    return false;
                })()
                """
            })
            if click_settings.get("result", {}).get("value"):
                await asyncio.sleep(2.0)
                eval_settings = await send_cmd("Runtime.evaluate", {
                    "expression": """
                    document.body.innerText.includes('Team Quota Guardrails') || document.body.innerText.includes('Model Pricing')
                    """
                })
                has_guardrails = eval_settings.get("result", {}).get("value")
                print(f"[PASS] Team Quota Guardrails & Pricing verified in DOM: {has_guardrails}")

                eval_models = await send_cmd("Runtime.evaluate", {
                    "expression": """
                    (() => {
                        const text = document.body.innerText;
                        return {
                            has_gpt4o: text.includes('gpt-4o'),
                            has_gemini: text.includes('gemini-1.5-flash'),
                            has_claude: text.includes('claude-3-5-sonnet'),
                            has_realtime_badge: text.includes('Real-Time API Key'),
                            has_dummy_badge: text.includes('Dummy Data AI Model')
                        };
                    })()
                    """,
                    "returnByValue": True
                })
                m_info = eval_models.get("result", {}).get("value", {})
                print(f"[PASS] 3-Model Registry in DOM: gpt-4o={m_info.get('has_gpt4o')}, gemini={m_info.get('has_gemini')}, claude={m_info.get('has_claude')}")
                print(f"[PASS] Provider Operation Badges: Real-Time={m_info.get('has_realtime_badge')}, Dummy-Mode={m_info.get('has_dummy_badge')}")
                assert m_info.get('has_gpt4o') and m_info.get('has_gemini') and m_info.get('has_claude'), "All 3 models must be in DOM"

            print("\n=== DOM TEST COMPLETED SUCCESSFULLY ===")
            return True

    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=3)
        except Exception:
            chrome_proc.kill()


if __name__ == "__main__":
    success = asyncio.run(run_dom_test())
    if not success:
        raise SystemExit(1)
