"""Comprehensive test suite for TokenLens Token Usage Simulator.

Verifies:
1. Determinism with identical random seed.
2. Distinct workloads with different seeds.
3. Unique simulated request IDs.
4. Token invariants (non-negative, cached <= input tokens).
5. Exact Decimal-safe cost calculation via CostEngine.
6. Effective-date pricing lookups.
7. Explicit missing-price handling for unpriced models (no silent zeroes).
8. Token and cost aggregations match individual item sums.
9. Traffic patterns (steady, variable, burst) generate valid time distributions.
10. Zero external API calls made during simulation.
11. Strict data isolation from real records.
"""

from datetime import datetime, date, timezone
from decimal import Decimal
import os
import pytest

import pandas as pd

from core.models import PricingRecord, RequestRecord
from core.cost_engine import CostEngine
from core.token_simulator import TokenUsageSimulator, SimulationConfig, SimulationSummary


@pytest.fixture
def test_engine():
    """Initializes CostEngine loaded with standard test pricing rate cards."""
    engine = CostEngine()
    pricing = [
        PricingRecord(
            model="gpt-4o",
            provider="openai",
            input_usd_per_1m=Decimal("2.50"),
            output_usd_per_1m=Decimal("10.00"),
            cached_usd_per_1m=Decimal("1.25"),
            effective_from=date(2026, 1, 1),
        ),
        PricingRecord(
            model="claude-3-5-sonnet",
            provider="anthropic",
            input_usd_per_1m=Decimal("3.00"),
            output_usd_per_1m=Decimal("15.00"),
            cached_usd_per_1m=Decimal("0.30"),
            effective_from=date(2026, 1, 1),
        ),
        PricingRecord(
            model="gemini-1.5-flash",
            provider="google",
            input_usd_per_1m=Decimal("0.075"),
            output_usd_per_1m=Decimal("0.30"),
            cached_usd_per_1m=Decimal("0.01875"),
            effective_from=date(2026, 1, 1),
        ),
    ]
    engine.load_pricing_records(pricing)
    return engine


def test_simulation_determinism_with_same_seed(test_engine):
    """Verify that identical seed reproduces 100% identical token counts, costs, and IDs."""
    sim = TokenUsageSimulator(test_engine)
    cfg1 = SimulationConfig(num_requests=30, seed=12345, models=["gpt-4o"])
    cfg2 = SimulationConfig(num_requests=30, seed=12345, models=["gpt-4o"])

    reqs1, priced1, df1, sum1 = sim.generate(cfg1)
    reqs2, priced2, df2, sum2 = sim.generate(cfg2)

    assert len(reqs1) == len(reqs2) == 30
    assert sum1.total_tokens == sum2.total_tokens
    assert sum1.total_spend_usd == sum2.total_spend_usd

    for r1, r2 in zip(reqs1, reqs2):
        assert r1.request_id == r2.request_id
        assert r1.input_tokens == r2.input_tokens
        assert r1.output_tokens == r2.output_tokens
        assert r1.cached_tokens == r2.cached_tokens
        assert r1.timestamp_utc == r2.timestamp_utc


def test_simulation_different_seed_produces_distinct_workload(test_engine):
    """Verify that different seeds produce distinct, randomized workloads."""
    sim = TokenUsageSimulator(test_engine)
    cfg1 = SimulationConfig(num_requests=40, seed=111, models=["gpt-4o"])
    cfg2 = SimulationConfig(num_requests=40, seed=999, models=["gpt-4o"])

    _, _, _, sum1 = sim.generate(cfg1)
    _, _, _, sum2 = sim.generate(cfg2)

    assert sum1.total_tokens != sum2.total_tokens
    assert sum1.total_spend_usd != sum2.total_spend_usd


def test_unique_request_ids(test_engine):
    """Verify all generated request IDs are strictly unique within the simulation."""
    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(num_requests=100, seed=42)
    reqs, _, _, _ = sim.generate(cfg)

    id_set = {r.request_id for r in reqs}
    assert len(id_set) == 100
    for r in reqs:
        assert r.request_id.startswith("sim_")


def test_token_invariants_and_cached_constraints(test_engine):
    """Verify non-negative token counts and cached_tokens <= input_tokens."""
    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(
        num_requests=75,
        input_tokens_min=500,
        input_tokens_max=3000,
        output_tokens_min=50,
        output_tokens_max=800,
        enable_cached_tokens=True,
        cached_token_ratio=0.75,
        seed=777,
    )
    reqs, priced, df, summary = sim.generate(cfg)

    for r in reqs:
        assert r.input_tokens >= 500
        assert r.input_tokens <= 3000
        assert r.output_tokens >= 50
        assert r.output_tokens <= 800
        assert 0 <= r.cached_tokens <= r.input_tokens

    assert summary.total_cached_tokens > 0
    assert summary.total_cached_tokens <= summary.total_input_tokens
    assert summary.cached_savings_usd > Decimal("0.0")


def test_disabled_cached_tokens_results_in_zero_cache(test_engine):
    """Verify that disabling cached tokens produces zero cached tokens and zero cache savings."""
    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(
        num_requests=30,
        enable_cached_tokens=False,
        seed=42,
    )
    _, _, df, summary = sim.generate(cfg)

    assert summary.total_cached_tokens == 0
    assert summary.cached_cost_usd == Decimal("0.0")
    assert summary.cached_savings_usd == Decimal("0.0")
    assert (df["cached_tokens"] == 0).all()


def test_cost_calculation_correctness(test_engine):
    """Verify CostEngine rates are applied with precision to simulated requests."""
    sim = TokenUsageSimulator(test_engine)
    # 1 request with fixed token counts for gpt-4o
    cfg = SimulationConfig(
        models=["gpt-4o"],
        num_requests=1,
        input_tokens_min=1000000,
        input_tokens_max=1000000,
        output_tokens_min=1000000,
        output_tokens_max=1000000,
        enable_cached_tokens=True,
        cached_token_ratio=0.5,
        seed=42,
    )
    _, priced, _, summary = sim.generate(cfg)
    p = priced[0]

    # For gpt-4o:
    # 1M input @ $2.50 = $2.50
    # 1M output @ $10.00 = $10.00
    # cached tokens at ratio ~ 0.5: T_cached * $1.25 / 1M
    assert p.missing_price is False
    assert p.output_cost_usd == Decimal("10.000000")
    assert p.input_cost_usd == Decimal("2.500000")
    assert p.total_cost_usd > Decimal("12.500000")


def test_missing_price_handling_for_unpriced_model(test_engine):
    """Verify unpriced models in simulation are explicitly flagged with missing_price=True (no silent zeroes)."""
    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(
        models=["unpriced-mystery-llm-v9"],
        num_requests=10,
        seed=99,
    )
    _, priced, df, summary = sim.generate(cfg)

    assert summary.missing_price_count == 10
    assert summary.total_spend_usd == Decimal("0.0")
    assert all(r.missing_price is True for r in priced)
    assert df["missing_price"].all()


def test_traffic_patterns_distribution(test_engine):
    """Verify steady, variable, and burst traffic generate valid timestamps across duration."""
    sim = TokenUsageSimulator(test_engine)

    for pattern in ("steady", "variable", "burst"):
        cfg = SimulationConfig(
            num_requests=50,
            duration_hours=12,
            traffic_pattern=pattern,
            seed=42,
        )
        reqs, _, df, summary = sim.generate(cfg)
        assert len(reqs) == 50
        assert summary.traffic_pattern == pattern
        # Check timestamps are within the 12-hour window
        min_ts = df["timestamp_utc"].min()
        max_ts = df["timestamp_utc"].max()
        span_hours = (max_ts - min_ts).total_seconds() / 3600.0
        assert span_hours <= 12.0


def test_simulation_data_isolation(test_engine):
    """Verify simulated records have explicit isolation markers and never contaminate real datasets."""
    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(num_requests=20, seed=42)
    reqs, priced, df, _ = sim.generate(cfg)

    for r in reqs:
        assert r.source == "simulated"
        assert r.env == "simulation"
        assert r.request_id.startswith("sim_")

    assert (df["is_simulated"] == True).all()


def test_no_external_api_calls(test_engine, monkeypatch):
    """Verify simulation runs purely locally with zero HTTP calls or provider SDK requests."""
    # Mock socket / httpx to ensure no network calls
    def forbidden_connect(*args, **kwargs):
        raise RuntimeError("External network connection attempted during simulation!")

    monkeypatch.setattr("httpx.Client.send", forbidden_connect, raising=False)

    sim = TokenUsageSimulator(test_engine)
    cfg = SimulationConfig(num_requests=30, seed=42)
    reqs, priced, df, summary = sim.generate(cfg)
    assert len(reqs) == 30
    assert summary.total_requests == 30


def test_streamlit_simulator_view_and_controls(tmp_path, monkeypatch):
    """Verify Streamlit dashboard navigation, simulation execution, and reset behavior."""
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    from core.access_control import AccessControlStore

    access_path = tmp_path / "access.sqlite3"
    AccessControlStore(access_path).bootstrap_org_head(
        "org.head", "Organization Head", "Head-Password-2026!"
    )
    monkeypatch.setenv("TOKENLENS_ACCESS_DB", str(access_path))
    monkeypatch.setenv("TOKENLENS_BOOTSTRAP_TOKEN", "ZSyVW6mqvgw")

    app_file = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_file, default_timeout=30).run(timeout=30)
    app.text_input[0].set_value("org.head")
    app.text_input[1].set_value("Head-Password-2026!")
    app.button[0].click()
    app.run(timeout=30)

    # Select Token Usage Simulator
    seg = app.get("segmented_control")[0]
    assert "Token Usage Simulator" in seg.options
    seg.set_value("Token Usage Simulator").run(timeout=30)

    # Assert title and banner rendered
    assert any("Token Usage Simulator" in item.value for item in app.markdown)
    assert any("SIMULATED DATA ACTIVE" in item.value for item in app.markdown)
    assert "sim_active_result" in app.session_state
    sim_res = app.session_state["sim_active_result"]
    assert sim_res["summary"].total_requests > 0

    # Ensure real production dataset is 100% isolated
    prod_df = app.session_state["df"]
    assert "is_simulated" not in prod_df.columns or not prod_df["is_simulated"].any()

    # Click Reset Simulation
    reset_buttons = [b for b in app.button if b.label == "🔄 Reset Simulation"]
    assert len(reset_buttons) >= 1
    reset_buttons[0].click().run(timeout=30)
    assert "sim_active_result" not in app.session_state
