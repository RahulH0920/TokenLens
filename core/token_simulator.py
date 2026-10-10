"""Interactive LLM Token Usage Simulator for TokenLens.

Generates realistic, deterministic synthetic LLM request workloads across multiple
providers and models, calculates costs using the core Decimal-safe CostEngine,
and prepares structured aggregations for live dashboard visualizations while
maintaining strict data isolation from production datasets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
import random
from typing import List, Dict, Any, Optional, Tuple, Sequence
import uuid

import pandas as pd

from core.models import RequestRecord, PricedRequest, PricingRecord
from core.cost_engine import CostEngine, quantize_cost, ONE_MILLION


TRAFFIC_PATTERNS = ("steady", "variable", "burst")
SUPPORTED_PROVIDERS = ("openai", "anthropic", "google", "all")
DEFAULT_MODELS = ("gpt-4o", "claude-3-5-sonnet", "gemini-1.5-flash")


@dataclass
class SimulationConfig:
    """Configuration parameters for synthetic token usage simulation."""
    provider: str = "all"
    models: List[str] = field(default_factory=lambda: ["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-flash"])
    num_requests: int = 60
    input_tokens_min: int = 600
    input_tokens_max: int = 2400
    output_tokens_min: int = 120
    output_tokens_max: int = 600
    enable_cached_tokens: bool = True
    cached_token_ratio: float = 0.35  # Fraction of input tokens that are cached
    duration_hours: int = 24
    traffic_pattern: str = "variable"  # steady, variable, burst
    team: Optional[str] = "engineering"
    feature: Optional[str] = "agent-chat"
    seed: Optional[int] = 42

    def __post_init__(self):
        if self.num_requests < 1:
            raise ValueError("num_requests must be at least 1")
        if self.input_tokens_min < 0 or self.input_tokens_max < self.input_tokens_min:
            raise ValueError("Invalid input token range")
        if self.output_tokens_min < 0 or self.output_tokens_max < self.output_tokens_min:
            raise ValueError("Invalid output token range")
        if not 0.0 <= self.cached_token_ratio <= 1.0:
            raise ValueError("cached_token_ratio must be between 0.0 and 1.0")
        if self.duration_hours < 1:
            raise ValueError("duration_hours must be at least 1")
        if self.traffic_pattern not in TRAFFIC_PATTERNS:
            raise ValueError(f"traffic_pattern must be one of {TRAFFIC_PATTERNS}")


@dataclass
class SimulationSummary:
    """Summary financial and token KPIs for the active simulation."""
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_tokens: int
    total_spend_usd: Decimal
    avg_cost_per_request: Decimal
    input_cost_usd: Decimal
    output_cost_usd: Decimal
    cached_cost_usd: Decimal
    cached_savings_usd: Decimal
    missing_price_count: int
    models_simulated: List[str]
    traffic_pattern: str
    seed_used: Optional[int]


class TokenUsageSimulator:
    """Deterministic synthetic workload generator and FinOps analyzer."""

    def __init__(self, cost_engine: CostEngine):
        self.cost_engine = cost_engine

    def generate(self, config: SimulationConfig) -> Tuple[List[RequestRecord], List[PricedRequest], pd.DataFrame, SimulationSummary]:
        """Generate synthetic requests, price them via CostEngine, and build analysis views.
        
        Guarantees:
        - 100% deterministic given the same random seed.
        - Zero external API calls or billing costs.
        - Reuses CostEngine Decimal-safe arithmetic and rate tables.
        - Strict isolation (tagged as source='simulated', env='simulation').
        """
        rng = random.Random(config.seed) if config.seed is not None else random.Random()
        
        # Reference end timestamp (normalized to UTC)
        base_time = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
        duration_seconds = max(3600, config.duration_hours * 3600)
        start_time = base_time - timedelta(seconds=duration_seconds)

        # Provider model mapping
        provider_model_map = {
            "openai": ["gpt-4o"],
            "anthropic": ["claude-3-5-sonnet"],
            "google": ["gemini-1.5-flash"],
        }

        # Resolve models to sample from
        if config.models:
            eligible_models = [m for m in config.models if m]
        elif config.provider in provider_model_map:
            eligible_models = provider_model_map[config.provider]
        else:
            eligible_models = list(DEFAULT_MODELS)

        if not eligible_models:
            eligible_models = ["gpt-4o"]

        # Synthetic user attribution pools
        sim_users = [f"sim_dev_{i:02d}" for i in range(1, 9)]
        sim_teams = [config.team] if config.team and config.team != "all" else ["engineering", "product", "growth-marketing", "customer-support"]
        sim_features = [config.feature] if config.feature and config.feature != "all" else ["agent-chat", "code-review", "doc-search", "summarisation"]

        raw_requests: List[RequestRecord] = []

        # Generate timestamps according to traffic pattern
        time_offsets = self._generate_time_offsets(
            num_requests=config.num_requests,
            duration_seconds=duration_seconds,
            pattern=config.traffic_pattern,
            rng=rng,
        )

        for i, offset_sec in enumerate(time_offsets):
            req_ts = start_time + timedelta(seconds=offset_sec)
            model_choice = eligible_models[i % len(eligible_models)] if config.traffic_pattern == "steady" else rng.choice(eligible_models)
            
            # Map model to provider
            if "gpt" in model_choice.lower():
                prov = "openai"
            elif "claude" in model_choice.lower():
                prov = "anthropic"
            elif "gemini" in model_choice.lower():
                prov = "google"
            else:
                prov = "simulated"

            # Generate token counts
            in_tok = rng.randint(config.input_tokens_min, config.input_tokens_max)
            out_tok = rng.randint(config.output_tokens_min, config.output_tokens_max)

            # Cached tokens calculation
            cached_tok = 0
            if config.enable_cached_tokens:
                # Add natural jitter to cache ratio
                jitter = rng.uniform(0.7, 1.2)
                effective_ratio = min(0.95, max(0.0, config.cached_token_ratio * jitter))
                cached_tok = int(in_tok * effective_ratio)
                # Invariant: cached_tokens must never exceed input_tokens
                cached_tok = min(cached_tok, in_tok)

            # Simulated latency based on tokens
            base_latency = 180 + int(out_tok * 1.8) + rng.randint(-30, 80)
            latency_ms = max(50, base_latency)

            # Deterministic unique request ID
            seed_prefix = f"s{config.seed}" if config.seed is not None else "rand"
            req_id = f"sim_{seed_prefix}_{i:04d}_{uuid.UUID(int=rng.getrandbits(128), version=4).hex[:8]}"

            req = RequestRecord(
                request_id=req_id,
                timestamp_utc=req_ts,
                team=rng.choice(sim_teams),
                feature=rng.choice(sim_features),
                user_id=rng.choice(sim_users),
                provider=prov,
                model=model_choice,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cached_tokens=cached_tok,
                status="success",
                latency_ms=latency_ms,
                env="simulation",
                source="simulated",
            )
            raw_requests.append(req)

        # Sort chronologically
        raw_requests.sort(key=lambda r: r.timestamp_utc)

        # Price all requests using authoritative Decimal-safe CostEngine
        priced_requests: List[PricedRequest] = [
            self.cost_engine.calculate_request_cost(r) for r in raw_requests
        ]

        # Convert to Pandas DataFrame for high-performance chart aggregation
        df = self._build_simulation_dataframe(priced_requests)

        # Calculate summary statistics and cache savings
        summary = self._compute_summary(priced_requests, config)

        return raw_requests, priced_requests, df, summary

    def _generate_time_offsets(
        self,
        num_requests: int,
        duration_seconds: int,
        pattern: str,
        rng: random.Random,
    ) -> List[float]:
        """Generate relative time offsets (in seconds) matching the requested traffic pattern."""
        if num_requests <= 1:
            return [duration_seconds / 2.0]

        offsets: List[float] = []

        if pattern == "steady":
            # Evenly spaced with slight realistic jitter
            step = duration_seconds / float(num_requests)
            for i in range(num_requests):
                jitter = rng.uniform(-0.25 * step, 0.25 * step)
                t = max(0.0, min(duration_seconds - 1.0, (i * step) + jitter))
                offsets.append(t)

        elif pattern == "variable":
            # Sinusoidal diurnal wave (peak in the middle of duration)
            for _ in range(num_requests):
                # Rejection sampling along sine wave density
                while True:
                    candidate = rng.uniform(0.0, duration_seconds)
                    phase = (candidate / duration_seconds) * 2.0 * math.pi
                    # Probability density: higher during midday peak
                    p = 0.2 + 0.8 * (0.5 * (1.0 + math.sin(phase - math.pi / 2.0)))
                    if rng.random() <= p:
                        offsets.append(candidate)
                        break

        elif pattern == "burst":
            # 65% baseline traffic, 35% concentrated in 3 sharp burst clusters
            burst_centers = [
                0.20 * duration_seconds,
                0.55 * duration_seconds,
                0.85 * duration_seconds,
            ]
            burst_width = duration_seconds * 0.035  # Narrow spike

            for _ in range(num_requests):
                if rng.random() < 0.45:
                    # Burst spike
                    center = rng.choice(burst_centers)
                    t = rng.gauss(center, burst_width)
                    t = max(0.0, min(duration_seconds - 1.0, t))
                    offsets.append(t)
                else:
                    # Background baseline
                    offsets.append(rng.uniform(0.0, duration_seconds))

        offsets.sort()
        return offsets

    def _build_simulation_dataframe(self, priced_records: Sequence[PricedRequest]) -> pd.DataFrame:
        """Convert priced requests into a rich pandas DataFrame for charting."""
        rows = []
        for r in priced_records:
            rows.append({
                "request_id": r.request_id,
                "timestamp_utc": r.timestamp_utc,
                "date": r.timestamp_utc.date(),
                "hour": r.timestamp_utc.strftime("%Y-%m-%d %H:00"),
                "minute": r.timestamp_utc.strftime("%Y-%m-%d %H:%M"),
                "provider": r.provider,
                "model": r.model,
                "team": r.team,
                "feature": r.feature,
                "user_id": r.user_id,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cached_tokens": r.cached_tokens,
                "uncached_input_tokens": max(0, r.input_tokens - r.cached_tokens),
                "total_tokens": r.input_tokens + r.output_tokens,
                "input_cost_usd": float(r.input_cost_usd),
                "output_cost_usd": float(r.output_cost_usd),
                "cached_cost_usd": float(r.cached_cost_usd),
                "total_cost_usd": float(r.total_cost_usd),
                "missing_price": r.missing_price,
                "latency_ms": r.latency_ms,
                "is_simulated": True,
            })
        return pd.DataFrame(rows)

    def _compute_summary(
        self,
        priced_records: Sequence[PricedRequest],
        config: SimulationConfig,
    ) -> SimulationSummary:
        """Aggregate total tokens, spend, and estimated cache savings."""
        tot_in = sum(r.input_tokens for r in priced_records)
        tot_out = sum(r.output_tokens for r in priced_records)
        tot_cached = sum(r.cached_tokens for r in priced_records)
        tot_tok = tot_in + tot_out

        tot_spend = sum((r.total_cost_usd for r in priced_records), Decimal("0.0"))
        in_spend = sum((r.input_cost_usd for r in priced_records), Decimal("0.0"))
        out_spend = sum((r.output_cost_usd for r in priced_records), Decimal("0.0"))
        cached_spend = sum((r.cached_cost_usd for r in priced_records), Decimal("0.0"))

        missing_count = sum(1 for r in priced_records if r.missing_price)
        avg_cost = quantize_cost(tot_spend / Decimal(len(priced_records))) if priced_records else Decimal("0.0")

        # Calculate cached savings vs pricing cached tokens at the uncached input rate
        # Savings = sum( (cached_tokens * (P_input - P_cached)) / 1,000,000 )
        cached_savings = Decimal("0.0")
        for r in priced_records:
            if r.cached_tokens > 0 and not r.missing_price:
                req_date = r.timestamp_utc.date()
                price = self.cost_engine.find_price(r.model, req_date)
                if price and price.cached_usd_per_1m > Decimal("0.0"):
                    diff = max(Decimal("0.0"), price.input_usd_per_1m - price.cached_usd_per_1m)
                    saved = (Decimal(r.cached_tokens) * diff) / ONE_MILLION
                    cached_savings += saved

        cached_savings = quantize_cost(cached_savings)

        return SimulationSummary(
            total_requests=len(priced_records),
            total_input_tokens=tot_in,
            total_output_tokens=tot_out,
            total_cached_tokens=tot_cached,
            total_tokens=tot_tok,
            total_spend_usd=tot_spend,
            avg_cost_per_request=avg_cost,
            input_cost_usd=in_spend,
            output_cost_usd=out_spend,
            cached_cost_usd=cached_spend,
            cached_savings_usd=cached_savings,
            missing_price_count=missing_count,
            models_simulated=sorted(list(set(r.model for r in priced_records))),
            traffic_pattern=config.traffic_pattern,
            seed_used=config.seed,
        )
