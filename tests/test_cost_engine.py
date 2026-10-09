"""Unit tests for cost calculation engine, pricing lookup, and invariant verification."""

from datetime import datetime, date
from decimal import Decimal
import pytest

from core.models import RequestRecord, PricingRecord
from core.cost_engine import CostEngine


@pytest.fixture
def sample_engine():
    engine = CostEngine()
    pricing = [
        PricingRecord(
            model="example-model",
            provider="openai",
            input_usd_per_1m=Decimal("2.00"),
            output_usd_per_1m=Decimal("8.00"),
            cached_usd_per_1m=Decimal("0.50"),
            effective_from=date(2026, 1, 1)
        ),
        PricingRecord(
            model="example-model-tiered",
            provider="openai",
            input_usd_per_1m=Decimal("5.00"),
            output_usd_per_1m=Decimal("15.00"),
            cached_usd_per_1m=Decimal("1.00"),
            effective_from=date(2026, 6, 1)
        )
    ]
    engine.load_pricing_records(pricing)
    return engine


def test_prd_example_cost_calculation(sample_engine):
    """PRD 2.3 Example:
    8,000 input tokens and 2,000 output tokens with rates of $2 and $8 per million cost $0.032.
    """
    req = RequestRecord(
        request_id="req_test_001",
        timestamp_utc=datetime(2026, 3, 1, 12, 0, 0),
        team="engineering",
        feature="code-review",
        user_id="user_42",
        model="example-model",
        input_tokens=8000,
        output_tokens=2000,
        cached_tokens=0,
        status="success"
    )

    priced = sample_engine.calculate_request_cost(req)

    # 8,000 * 2 / 1,000,000 = 0.016
    # 2,000 * 8 / 1,000,000 = 0.016
    # Total = 0.032
    assert priced.input_cost_usd == Decimal("0.016000")
    assert priced.output_cost_usd == Decimal("0.016000")
    assert priced.total_cost_usd == Decimal("0.032000")
    assert priced.missing_price is False


def test_missing_pricing_flagged_not_zeroed(sample_engine):
    """Ensure unpriced model is flagged as missing_price=True."""
    req = RequestRecord(
        request_id="req_unknown_002",
        timestamp_utc=datetime(2026, 3, 1, 12, 0, 0),
        team="support",
        feature="agent-chat",
        user_id="user_99",
        model="unpriced-experimental-model",
        input_tokens=5000,
        output_tokens=1000,
        cached_tokens=0,
        status="success"
    )

    priced = sample_engine.calculate_request_cost(req)
    assert priced.missing_price is True
    assert priced.total_cost_usd == Decimal("0.0")


def test_invariants_verification(sample_engine):
    """Verify team sum equals model sum equals grand total."""
    reqs = [
        RequestRecord(
            request_id=f"req_{i}",
            timestamp_utc=datetime(2026, 3, i, 10, 0, 0),
            team="engineering" if i % 2 == 0 else "product",
            feature="code-review",
            user_id="user_1",
            model="example-model",
            input_tokens=10000 * i,
            output_tokens=2000 * i,
            cached_tokens=500 * i,
            status="success"
        )
        for i in range(1, 5)
    ]

    priced_list = sample_engine.process_requests(reqs)
    df = sample_engine.get_priced_dataframe(priced_list)

    inv_ok, stats = sample_engine.verify_invariants(df)
    assert inv_ok is True
    assert abs(stats["grand_total"] - stats["team_sum"]) < 0.0001
    assert abs(stats["grand_total"] - stats["model_sum"]) < 0.0001


def test_cached_token_cost_calculation(sample_engine):
    """Verify that cached tokens are priced at the designated cached rate and add to total cost."""
    req = RequestRecord(
        request_id="req_cached_test_01",
        timestamp_utc=datetime(2026, 3, 15, 14, 0, 0),
        team="research",
        feature="agent-chat",
        user_id="user_77",
        model="example-model",
        input_tokens=10000,
        output_tokens=2000,
        cached_tokens=4000,
        status="success"
    )

    priced = sample_engine.calculate_request_cost(req)

    # In: 10,000 * 2.00 / 1M = 0.020000
    # Out: 2,000 * 8.00 / 1M = 0.016000
    # Cached: 4,000 * 0.50 / 1M = 0.002000
    # Total = 0.038000
    assert priced.input_cost_usd == Decimal("0.020000")
    assert priced.output_cost_usd == Decimal("0.016000")
    assert priced.cached_cost_usd == Decimal("0.002000")
    assert priced.total_cost_usd == Decimal("0.038000")
    assert priced.missing_price is False


def test_duckdb_view_cached_and_missing_pricing(sample_engine):
    """Verify DuckDB view request_costs matches decimal logic for cached tokens and missing prices."""
    reqs = [
        RequestRecord(
            request_id="req_valid",
            timestamp_utc=datetime(2026, 3, 1, 10, 0, 0),
            team="engineering",
            feature="code-review",
            user_id="u1",
            model="example-model",
            input_tokens=5000,
            output_tokens=1000,
            cached_tokens=2000,
            status="success"
        ),
        RequestRecord(
            request_id="req_unpriced",
            timestamp_utc=datetime(2026, 3, 1, 11, 0, 0),
            team="product",
            feature="doc-search",
            user_id="u2",
            model="unpriced-model-xyz",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=0,
            status="success"
        )
    ]
    priced = sample_engine.process_requests(reqs)
    df = sample_engine.get_priced_dataframe(priced)

    # Check missing pricing summary
    summary = sample_engine.get_missing_pricing_summary(df)
    assert summary["count"] == 1
    assert "unpriced-model-xyz" in summary["models"]

    # Check DuckDB SQL view
    row = sample_engine.conn.execute("""
        SELECT 
            SUM(input_cost_usd), SUM(output_cost_usd), SUM(cached_cost_usd), SUM(total_cost_usd),
            SUM(CASE WHEN missing_price THEN 1 ELSE 0 END)
        FROM request_costs
    """).fetchone()

    # Valid req: 5000*2/1M = 0.01, 1000*8/1M = 0.008, 2000*0.5/1M = 0.001 -> Total = 0.019
    # Unpriced req: 0.0
    assert abs(row[0] - 0.01) < 0.00001
    assert abs(row[1] - 0.008) < 0.00001
    assert abs(row[2] - 0.001) < 0.00001
    assert abs(row[3] - 0.019) < 0.00001
    assert row[4] == 1

