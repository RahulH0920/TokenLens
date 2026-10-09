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
