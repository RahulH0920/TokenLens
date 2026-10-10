"""Independent reconciliation runner generating validation report and verifying all financial invariants."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import random
import sys
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.importer import DataImporter
from core.cost_engine import CostEngine
from core.reconciliation import ReconciliationEngine

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    duplicate_records = list(importer.duplicate_records)

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
    priced_with_rate = [r for r in priced_requests if not r.missing_price]
    sample_priced = random.sample(priced_with_rate, min(10, len(priced_with_rate)))
    
    for r in sample_priced:
        p = pricing_map[r.model]
        # Independent manual calculation: (in_tok * in_rate + out_tok * out_rate + cached * cached_rate) / 1e6
        in_cost = (Decimal(r.input_tokens) * p.input_usd_per_1m) / Decimal("1000000")
        out_cost = (Decimal(r.output_tokens) * p.output_usd_per_1m) / Decimal("1000000")
        cached_cost = (Decimal(r.cached_tokens) * p.cached_usd_per_1m) / Decimal("1000000")
        expected_cost = (in_cost + out_cost + cached_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        actual_cost = r.total_cost_usd

        spot_checks.append({
            "request_id": r.request_id,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "in_rate": p.input_usd_per_1m,
            "out_rate": p.output_usd_per_1m,
            "expected_cost": expected_cost,
            "actual_cost": actual_cost,
            "tolerance_usd": Decimal("0.000001"),
        })

    team_totals = defaultdict(Decimal)
    model_totals = defaultdict(Decimal)
    missing_by_model = defaultdict(list)
    unattributed_requests = []
    for record in priced_requests:
        team_totals[record.team] += record.total_cost_usd
        model_totals[record.model] += record.total_cost_usd
        if record.missing_price:
            missing_by_model[record.model].append(record.request_id)
        if record.is_unattributed:
            unattributed_requests.append({"request_id": record.request_id})

    # 6. Generate Markdown Report
    scope_info = {
        "dataset_name": "sample_requests.csv",
        "total_source_rows": stats["source_rows"],
        "valid_records": stats["loaded_rows"],
        "rejected_records": stats["rejected_rows"],
        "pricing_version": "v2026.10",
        "audit_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset_sha256": file_sha256(requests_file)[:16],
        "pricing_sha256": file_sha256(pricing_file)[:16],
        "expected_team_totals": golden_manifest.get("team_costs", {}),
        "team_totals": dict(team_totals),
        "model_totals": dict(model_totals),
        "duplicate_ids": sorted({str(item["request_id"]) for item in duplicate_records}),
        "missing_pricing_exceptions": [
            {"model": model, "count": len(request_ids), "request_ids": sorted(request_ids)}
            for model, request_ids in sorted(missing_by_model.items())
        ],
        "unattributed_requests": unattributed_requests,
    }

    report_md = reconciler.generate_markdown_report(results, scope_info, spot_checks)
    validation_report_path = REPORT_DIR / "validation_report.md"
    reconciliation_report_path = REPORT_DIR / "reconciliation_report.md"
    with open(validation_report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    with open(reconciliation_report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nValidation report comparing dashboard totals with known expected values generated at:\n  {validation_report_path}\n")
    print("=" * 60)
    print("AUDIT RESULTS SUMMARY:")
    for res in results:
        badge = "[PASS]" if res.status == "PASS" else f"[{res.status}]"
        print(f"{badge:<10} {res.metric:<35} Expected: {str(res.expected):<15} Actual: {str(res.actual):<15}")
    print("=" * 60)

    # Return True if all pass
    overall_status = reconciler.overall_status(results, spot_checks)
    all_passed = overall_status != "FAIL" and inv_ok
    return all_passed, report_md


if __name__ == "__main__":
    success, report = run_audit()
    if not success:
        print("FAIL: One or more reconciliation or spot-check checks failed.")
        exit(1)
    status_line = next((line for line in report.splitlines() if line.startswith("**Overall Status:**")), "")
    print(status_line)
