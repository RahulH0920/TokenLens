"""Independent reconciliation runner generating validation report and verifying all financial invariants."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import random
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.importer import DataImporter
from core.cost_engine import CostEngine
from core.reconciliation import ReconciliationEngine

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def run_audit():
    pricing_file = DATA_DIR / "model_pricing.csv"
    requests_file = DATA_DIR / "sample_requests.csv"
    golden_file = DATA_DIR / "golden_validation.json"

    print("=" * 60)
    print("RUNNING LLM FINOPS FINANCIAL AUDIT & RECONCILIATION")
    print("=" * 60)

    # 1. Ingest Data
    importer = DataImporter()
    pricing_records = importer.load_pricing(pricing_file)
    requests_records, rejects, stats = importer.load_requests(requests_file)

    print(f"Ingestion Stats: {stats}")

    # 2. Process via Cost Engine
    engine = CostEngine()
    engine.load_pricing_records(pricing_records)
    priced_requests = engine.process_requests(requests_records)
    df = engine.get_priced_dataframe(priced_requests)

    # 3. Check Cross-cut Invariants
    inv_ok, inv_stats = engine.verify_invariants(df)
    print(f"Invariant Check: {'PASS' if inv_ok else 'FAIL'}")
    print(f"Grand Total: ${inv_stats['grand_total']:.4f} | Team Sum: ${inv_stats['team_sum']:.4f} | Model Sum: ${inv_stats['model_sum']:.4f}")

    # 4. Independent Golden Dataset Comparison
    with open(golden_file, "r", encoding="utf-8") as f:
        golden_manifest = json.load(f)

    reconciler = ReconciliationEngine(tolerance_usd=0.01, tolerance_percent=0.0001)
    results = reconciler.run_reconciliation(
        actual_df=df,
        expected_manifest=golden_manifest,
        ingestion_stats=stats,
        rejected_records=rejects
    )

    # 5. Hand-calculated Spot Checks (10 random records)
    pricing_map = {p.model: p for p in pricing_records}
    spot_checks = []
    
    # Pick 10 sample requests
    random.seed(99)
    sample_priced = random.sample([r for r in priced_requests if not r.missing_price], 10)
    
    for r in sample_priced:
        p = pricing_map[r.model]
        # Independent manual calculation: (in_tok * in_rate + out_tok * out_rate + cached * cached_rate) / 1e6
        in_cost = (Decimal(r.input_tokens) * p.input_usd_per_1m) / Decimal("1000000")
        out_cost = (Decimal(r.output_tokens) * p.output_usd_per_1m) / Decimal("1000000")
        cached_cost = (Decimal(r.cached_tokens) * p.cached_usd_per_1m) / Decimal("1000000")
        expected_cost = float((in_cost + out_cost + cached_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
        
        spot_checks.append({
            "request_id": r.request_id,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cached_tokens": r.cached_tokens,
            "in_rate": float(p.input_usd_per_1m),
            "out_rate": float(p.output_usd_per_1m),
            "cached_rate": float(p.cached_usd_per_1m),
            "expected_cost": expected_cost,
            "actual_cost": float(r.total_cost_usd)
        })

    # 6. Generate Markdown Report
    scope_info = {
        "dataset_name": "sample_requests.csv",
        "total_source_rows": stats["source_rows"],
        "valid_records": stats["loaded_rows"],
        "pricing_version": "v2026.10",
        "audit_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    report_md = reconciler.generate_markdown_report(results, scope_info, spot_checks)
    report_path = REPORT_DIR / "reconciliation_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nReconciliation report generated at: {report_path}\n")
    print("=" * 60)
    print("AUDIT RESULTS SUMMARY:")
    for res in results:
        badge = "[PASS]" if res.status == "PASS" else f"[{res.status}]"
        print(f"{badge:<10} {res.metric:<35} Expected: {str(res.expected):<15} Actual: {str(res.actual):<15}")
    print("=" * 60)

    # Return True if all pass
    all_passed = all(r.status in {"PASS", "WARNING"} for r in results) and inv_ok
    return all_passed, report_md


if __name__ == "__main__":
    success, _ = run_audit()
    if not success:
        print("FAIL: Some reconciliation checks did not pass.")
        exit(1)
    print("SUCCESS: All reconciliation checks and invariants PASSED.")
