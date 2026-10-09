"""Unit tests for reconciliation engine and report generation."""

import pandas as pd
from core.reconciliation import ReconciliationEngine


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


def test_reconciliation_with_cached_tokens_and_team_costs():
    """Verify that cached tokens, team costs, and rejection stats are verified against golden values."""
    reconciler = ReconciliationEngine(tolerance_usd=0.01)

    df = pd.DataFrame([
        {
            "request_id": "r1",
            "team": "engineering",
            "feature": "code-review",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached_tokens": 200,
            "total_cost_usd": 0.00775,
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
            "cached_tokens": 400,
            "total_cost_usd": 0.01550,
            "missing_price": False,
            "is_unattributed": False
        }
    ])

    expected_manifest = {
        "total_requests": 2,
        "total_input_tokens": 3000,
        "total_output_tokens": 1500,
        "total_cached_tokens": 600,
        "total_cost_usd": 0.0233,
        "expected_rejected_rows": 2,
        "expected_duplicate_ids": 1,
        "expected_missing_price_records": 0,
        "team_costs": {
            "engineering": 0.0078,
            "product": 0.0155
        }
    }

    ingestion_stats = {
        "source_rows": 4,
        "loaded_rows": 2,
        "rejected_rows": 2,
        "duplicate_count": 1
    }

    checks = reconciler.run_reconciliation(
        actual_df=df,
        expected_manifest=expected_manifest,
        ingestion_stats=ingestion_stats,
        rejected_records=[{"reason": "duplicate"}, {"reason": "negative tokens"}]
    )

    check_metrics = {c.metric: c for c in checks}
    assert "Total Cached Tokens" in check_metrics
    assert check_metrics["Total Cached Tokens"].status == "PASS"
    assert "Team Allocation Multi-Dimensional Reconciliation" in check_metrics
    assert check_metrics["Team Allocation Multi-Dimensional Reconciliation"].status == "PASS"
    assert "Quarantined Rejection Accounting" in check_metrics
    assert check_metrics["Quarantined Rejection Accounting"].status == "PASS"


def test_reconciliation_detects_cost_mismatch():
    """Verify that financial variance outside tolerance fails the reconciliation check."""
    reconciler = ReconciliationEngine(tolerance_usd=0.01)

    df = pd.DataFrame([
        {
            "request_id": "r1",
            "team": "engineering",
            "feature": "code-review",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached_tokens": 0,
            "total_cost_usd": 10.00,  # Deliberate mismatch
            "missing_price": False,
            "is_unattributed": False
        }
    ])

    expected_manifest = {
        "total_requests": 1,
        "total_input_tokens": 1000,
        "total_output_tokens": 500,
        "total_cost_usd": 0.0075
    }

    checks = reconciler.run_reconciliation(
        actual_df=df,
        expected_manifest=expected_manifest,
        ingestion_stats={"source_rows": 1, "loaded_rows": 1, "rejected_rows": 0, "duplicate_count": 0},
        rejected_records=[]
    )

    cost_check = next(c for c in checks if c.metric == "Total Spend ($ USD)")
    assert cost_check.status == "FAIL"

