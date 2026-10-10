"""Unit tests for reconciliation engine and report generation."""

from decimal import Decimal

import pandas as pd
from core.reconciliation import ReconciliationEngine
from core.models import ValidationCheckResult


def test_reconciliation_pass_scenario():
    reconciler = ReconciliationEngine(tolerance_usd=0.01)
    
    # Mock calculated dataframe
    df = pd.DataFrame([
        {
            "request_id": "r1",
            "team": "engineering",
            "feature": "code-review",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached_tokens": 0,
            "total_cost_usd": 0.0075,
            "missing_price": False,
            "is_unattributed": False
        },
        {
            "request_id": "r2",
            "team": "product",
            "feature": "doc-search",
            "model": "gpt-4o",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "cached_tokens": 0,
            "total_cost_usd": 0.0150,
            "missing_price": False,
            "is_unattributed": False
        }
    ])

    expected_manifest = {
        "total_requests": 2,
        "total_input_tokens": 3000,
        "total_output_tokens": 1500,
        "total_cost_usd": 0.0225,
        "expected_missing_price_records": 0
    }

    ingestion_stats = {
        "source_rows": 2,
        "loaded_rows": 2,
        "rejected_rows": 0,
        "duplicate_count": 0
    }

    checks = reconciler.run_reconciliation(
        actual_df=df,
        expected_manifest=expected_manifest,
        ingestion_stats=ingestion_stats,
        rejected_records=[]
    )

    # All primary checks should PASS
    assert all(c.status == "PASS" for c in checks)


def test_generate_validation_report_title_and_structure():
    reconciler = ReconciliationEngine(tolerance_usd=0.01)
    checks = []
    scope_info = {
        "dataset_name": "sample_requests.csv",
        "total_source_rows": 100,
        "valid_records": 100,
        "pricing_version": "v2026.10",
        "audit_time": "2026-10-10T00:00:00Z",
    }
    report = reconciler.generate_markdown_report(checks, scope_info, [])
    assert "LLM FinOps Validation Report: Comparing Dashboard Totals with Known Expected Values" in report
    assert "Validation Results Matrix (Expected vs Actual Dashboard Totals)" in report


def test_duplicate_exclusions_and_open_exceptions_produce_warnings_without_double_counting():
    reconciler = ReconciliationEngine(tolerance_usd=0.01)
    frame = pd.DataFrame([{
        "request_id": "kept-1",
        "team": "unattributed",
        "model": "unknown-model",
        "input_tokens": 100,
        "output_tokens": 25,
        "total_cost_usd": 0.0,
        "missing_price": True,
        "is_unattributed": True,
    }])
    checks = reconciler.run_reconciliation(
        frame,
        {
            "total_requests": 1,
            "total_input_tokens": 100,
            "total_output_tokens": 25,
            "total_cost_usd": 0,
            "expected_missing_price_records": 1,
            "team_costs": {"unattributed": 0},
        },
        {"source_rows": 2, "loaded_rows": 1, "rejected_rows": 1, "duplicate_count": 1},
        [{"reason": "Duplicate request_id: kept-1"}],
    )

    by_metric = {check.metric: check for check in checks}
    assert by_metric["Source Row Reconciliation (Source = Loaded + Rejected)"].status == "PASS"
    assert by_metric["Duplicate Request IDs"].status == "WARNING"
    assert by_metric["Missing Pricing Exceptions"].status == "WARNING"
    assert by_metric["Unattributed Requests and Spend"].status == "WARNING"
    assert "included once" in by_metric["Source Row Reconciliation (Source = Loaded + Rejected)"].details
    assert reconciler.overall_status(checks) == "PASS WITH WARNINGS"


def test_grand_total_uses_unrounded_decimal_before_display_rounding():
    reconciler = ReconciliationEngine(tolerance_usd=Decimal("0.00000001"), tolerance_percent=Decimal("0"))
    frame = pd.DataFrame([{
        "request_id": "r1",
        "team": "engineering",
        "model": "gpt-4o",
        "input_tokens": 1,
        "output_tokens": 0,
        "total_cost_usd": 0.0000513,
        "missing_price": False,
        "is_unattributed": False,
    }])

    checks = reconciler.run_reconciliation(
        frame,
        {"total_cost_usd": "0.0000514", "team_costs": {"engineering": "0.0000514"}},
        {"source_rows": 1, "loaded_rows": 1, "rejected_rows": 0, "duplicate_count": 0},
        [],
    )
    grand = next(check for check in checks if check.metric == "Grand Total Spend (USD)")
    assert grand.status == "FAIL"
    assert grand.difference == "-$0.00000010"
    assert grand.expected == grand.actual  # Both round to the same six-decimal display value.


def test_overall_status_supports_all_three_states_and_spot_check_failure():
    check = lambda status: ValidationCheckResult(
        metric="test", category="test", expected=0, actual=0, difference=0,
        tolerance="0", status=status, details="test",
    )
    reconciler = ReconciliationEngine()
    assert reconciler.overall_status([check("PASS")], [{"expected_cost": "1", "actual_cost": "1"}]) == "PASS"
    assert reconciler.overall_status([check("WARNING")], [{"expected_cost": "1", "actual_cost": "1"}]) == "PASS WITH WARNINGS"
    assert reconciler.overall_status([check("FAIL")], [{"expected_cost": "1", "actual_cost": "1"}]) == "FAIL"
    assert reconciler.overall_status([check("PASS")], [{"expected_cost": "1", "actual_cost": "1.1"}]) == "FAIL"
