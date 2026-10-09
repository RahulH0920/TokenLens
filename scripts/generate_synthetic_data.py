"""Script to generate realistic test data: model_pricing.csv, sample_requests.csv, and golden_validation.json."""

import json
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 1. Pricing Registry definition
PRICING_DATA = [
    {
        "model": "gpt-4o",
        "provider": "openai",
        "input_usd_per_1m": "2.50",
        "output_usd_per_1m": "10.00",
        "cached_usd_per_1m": "1.25",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_usd_per_1m": "0.15",
        "output_usd_per_1m": "0.60",
        "cached_usd_per_1m": "0.075",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
        "input_usd_per_1m": "3.00",
        "output_usd_per_1m": "15.00",
        "cached_usd_per_1m": "0.30",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "claude-3-haiku",
        "provider": "anthropic",
        "input_usd_per_1m": "0.25",
        "output_usd_per_1m": "1.25",
        "cached_usd_per_1m": "0.03",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "gemini-1.5-pro",
        "provider": "google",
        "input_usd_per_1m": "3.50",
        "output_usd_per_1m": "10.50",
        "cached_usd_per_1m": "0.875",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "gemini-1.5-flash",
        "provider": "google",
        "input_usd_per_1m": "0.075",
        "output_usd_per_1m": "0.30",
        "cached_usd_per_1m": "0.01875",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "llama-3.1-70b",
        "provider": "meta",
        "input_usd_per_1m": "0.80",
        "output_usd_per_1m": "0.80",
        "cached_usd_per_1m": "0.00",
        "effective_from": "2026-01-01",
        "effective_to": ""
    },
    {
        "model": "text-embedding-3-small",
        "provider": "openai",
        "input_usd_per_1m": "0.02",
        "output_usd_per_1m": "0.00",
        "cached_usd_per_1m": "0.00",
        "effective_from": "2026-01-01",
        "effective_to": ""
    }
]

def generate():
    random.seed(42)  # Deterministic generation
    pricing_df = pd.DataFrame(PRICING_DATA)
    pricing_csv_path = DATA_DIR / "model_pricing.csv"
    pricing_df.to_csv(pricing_csv_path, index=False)
    print(f"Generated pricing table: {pricing_csv_path}")

    # Lookup map for independent cost calculation
    rate_map = {}
    for p in PRICING_DATA:
        rate_map[p["model"]] = {
            "in": Decimal(p["input_usd_per_1m"]),
            "out": Decimal(p["output_usd_per_1m"]),
            "cached": Decimal(p["cached_usd_per_1m"])
        }

    teams = ["engineering", "support", "product", "research", "marketing"]
    team_features = {
        "engineering": ["code-review", "agent-chat", "doc-search"],
        "support": ["agent-chat", "summarisation", "translation"],
        "product": ["doc-search", "summarisation", "classification"],
        "research": ["agent-chat", "summarisation", "code-review"],
        "marketing": ["summarisation", "translation", "classification"]
    }
    models = ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-haiku", "gemini-1.5-pro", "gemini-1.5-flash", "llama-3.1-70b"]
    
    start_time = datetime(2026, 10, 1, 8, 0, 0)
    
    requests_rows = []
    
    # We will independently accumulate golden expected metrics
    total_valid_requests = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    total_cost_decimal = Decimal("0.0")
    team_costs = {t: Decimal("0.0") for t in teams}
    team_costs["unattributed"] = Decimal("0.0")
    missing_pricing_count = 0
    
    # Generate 1,200 standard requests
    for i in range(1, 1201):
        req_id = f"req_{i:04d}"
        
        # Time distribution across 7 days
        offset_minutes = random.randint(0, 7 * 24 * 60 - 1)
        req_ts = start_time + timedelta(minutes=offset_minutes)
        
        # 8% chance of unattributed metadata to test FR-02 & D3 unattributed panel
        is_missing_team = (random.random() < 0.08)
        if is_missing_team:
            team = ""
            feature = ""
            norm_team = "unattributed"
            norm_feature = "unassigned"
        else:
            team = random.choice(teams)
            feature = random.choice(team_features[team])
            # occasionally introduce space or uppercase to test normalization
            if random.random() < 0.15:
                team = team.upper() if random.random() < 0.5 else f" {team} "
            norm_team = str(team).strip().lower()
            norm_feature = str(feature).strip().lower()

        user_id = f"user_{random.randint(10, 45)}"
        
        # 1% chance of unknown model to test FR-06 missing-pricing validation exception
        is_unpriced_model = (random.random() < 0.01)
        if is_unpriced_model:
            model = "unsupported-legacy-model-v0"
            provider = "internal"
            missing_pricing_count += 1
        else:
            model = random.choice(models)
            provider = next((p["provider"] for p in PRICING_DATA if p["model"] == model), "openai")

        # Realistic token ranges
        if "embed" in model:
            in_tok = random.randint(200, 1500)
            out_tok = 0
            cached_tok = 0
        elif "mini" in model or "haiku" in model or "flash" in model:
            in_tok = random.randint(300, 4000)
            out_tok = random.randint(100, 800)
            cached_tok = random.randint(0, in_tok // 2) if random.random() < 0.3 else 0
        else:
            in_tok = random.randint(1500, 12000)
            out_tok = random.randint(400, 2500)
            cached_tok = random.randint(0, in_tok // 2) if random.random() < 0.4 else 0

        status = "success" if random.random() < 0.97 else "error"
        latency = random.randint(180, 2400)
        env = "production" if random.random() < 0.85 else "staging"

        requests_rows.append({
            "request_id": req_id,
            "timestamp": req_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "team": team,
            "feature": feature,
            "user": user_id,
            "model": model,
            "provider": provider,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cached_tokens": cached_tok,
            "status": status,
            "latency_ms": latency,
            "env": env
        })

        # Calculate golden expected cost independently
        total_valid_requests += 1
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        total_cached_tokens += cached_tok

        if model in rate_map:
            rates = rate_map[model]
            in_c = (Decimal(in_tok) * rates["in"]) / Decimal("1000000")
            out_c = (Decimal(out_tok) * rates["out"]) / Decimal("1000000")
            cached_c = (Decimal(cached_tok) * rates["cached"]) / Decimal("1000000")
            req_cost = (in_c + out_c + cached_c).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            total_cost_decimal += req_cost
            team_costs[norm_team] += req_cost

    # Add 5 Duplicate Request IDs to verify deduplication
    for dup_i in range(1, 6):
        target_dup = requests_rows[dup_i * 10].copy()
        requests_rows.append(target_dup)

    # Add 3 Invalid rows with negative tokens to test reject routing
    requests_rows.append({
        "request_id": "req_invalid_neg_01",
        "timestamp": "2026-10-02T10:00:00Z",
        "team": "engineering",
        "feature": "code-review",
        "user": "user_11",
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": -500,
        "output_tokens": 100,
        "cached_tokens": 0,
        "status": "error",
        "latency_ms": 100,
        "env": "dev"
    })
    requests_rows.append({
        "request_id": "req_invalid_neg_02",
        "timestamp": "2026-10-03T11:00:00Z",
        "team": "support",
        "feature": "agent-chat",
        "user": "user_15",
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
        "input_tokens": 1000,
        "output_tokens": -250,
        "cached_tokens": 0,
        "status": "error",
        "latency_ms": 120,
        "env": "dev"
    })
    requests_rows.append({
        "request_id": "",  # missing id
        "timestamp": "2026-10-04T12:00:00Z",
        "team": "product",
        "feature": "doc-search",
        "user": "user_20",
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 2000,
        "output_tokens": 500,
        "cached_tokens": 0,
        "status": "success",
        "latency_ms": 500,
        "env": "dev"
    })

    req_df = pd.DataFrame(requests_rows)
    req_csv_path = DATA_DIR / "sample_requests.csv"
    req_df.to_csv(req_csv_path, index=False)
    print(f"Generated requests dataset with {len(req_df)} rows: {req_csv_path}")

    # Build Golden Manifest for Independent Reconciliation Testing
    golden_manifest = {
        "dataset_name": "sample_requests.csv",
        "total_source_rows": len(requests_rows),
        "total_requests": total_valid_requests,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cached_tokens": total_cached_tokens,
        "total_cost_usd": float(round(total_cost_decimal, 4)),
        "expected_rejected_rows": 8,  # 5 duplicates + 3 invalid
        "expected_duplicate_ids": 5,
        "expected_missing_price_records": missing_pricing_count,
        "team_costs": {k: float(round(v, 4)) for k, v in team_costs.items()}
    }

    manifest_path = DATA_DIR / "golden_validation.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(golden_manifest, f, indent=2)
    print(f"Generated independent golden validation manifest: {manifest_path}")

if __name__ == "__main__":
    generate()
