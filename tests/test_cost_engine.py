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
