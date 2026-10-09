"""Unit and integration tests for Anomaly Detection and Budget Guardrails (FR-09, FR-10)."""

from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd
import pytest
from starlette.testclient import TestClient

from core.anomaly_engine import AnomalyDetector
from core.guardrails import GuardrailEngine
from core.models import BudgetPolicy, AnomalyAlert
from api.main import app


def test_anomaly_detector_cost_spike():
    """Verify statistical cost spike detection identifies extreme spend outliers."""
    detector = AnomalyDetector(z_cost_threshold=2.5)

    # 20 baseline requests at ~$0.01 and 1 extreme anomaly at $1.50
    rows = []
    for i in range(20):
        rows.append({
            "request_id": f"req_norm_{i}",
            "timestamp_utc": datetime(2026, 3, 1, 10, i, 0, tzinfo=timezone.utc),
            "team": "engineering",
            "feature": "code-review",
            "user_id": f"user_{i%3}",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_cost_usd": 0.01,
        })
    # Add outlier
    rows.append({
        "request_id": "req_anomaly_spike_99",
        "timestamp_utc": datetime(2026, 3, 1, 10, 30, 0, tzinfo=timezone.utc),
        "team": "engineering",
        "feature": "code-review",
        "user_id": "user_rogue",
        "model": "gpt-4o",
        "input_tokens": 120000,
        "output_tokens": 15000,
        "total_cost_usd": 1.50,
    })

    df = pd.DataFrame(rows)
    alerts = detector.detect_cost_spikes(df)

    assert len(alerts) >= 1
    outlier_alert = next(a for a in alerts if a.request_id == "req_anomaly_spike_99")
    assert outlier_alert.anomaly_type == "COST_SPIKE"
    assert outlier_alert.severity in {"CRITICAL", "WARNING"}
    assert outlier_alert.z_score > 2.5
    assert outlier_alert.actual_value == 1.50


def test_anomaly_detector_token_bloat():
    """Verify detection of context window bloat and oversized prompt inputs."""
    detector = AnomalyDetector()

    rows = []
    # 20 standard queries with 400-600 tokens
    for i in range(20):
        rows.append({
            "request_id": f"req_tok_{i}",
            "timestamp_utc": datetime(2026, 3, 1, 10, i, 0, tzinfo=timezone.utc),
            "team": "support",
            "feature": "agent-chat",
            "user_id": "user_1",
            "model": "claude-3-5-sonnet",
            "input_tokens": 500,
            "output_tokens": 100,
            "total_cost_usd": 0.003,
        })
    # Add massive prompt bloat
    rows.append({
        "request_id": "req_bloated_input",
        "timestamp_utc": datetime(2026, 3, 1, 10, 25, 0, tzinfo=timezone.utc),
        "team": "support",
        "feature": "agent-chat",
        "user_id": "user_2",
        "model": "claude-3-5-sonnet",
        "input_tokens": 85000,
        "output_tokens": 100,
        "total_cost_usd": 0.25,
    })

    df = pd.DataFrame(rows)
    bloat_alerts = detector.detect_token_bloat(df)

    assert len(bloat_alerts) >= 1
    alert = bloat_alerts[0]
    assert alert.anomaly_type == "TOKEN_BLOAT"
    assert alert.actual_value == 85000.0
    assert alert.request_id == "req_bloated_input"


def test_guardrails_evaluation_allow_warn_block():
    """Verify guardrails transition properly through ALLOW, WARN, and BLOCK."""
    engine = GuardrailEngine([
        BudgetPolicy(
            team="data-science",
            monthly_budget_usd=Decimal("100.00"),
            warning_threshold_pct=80.0,
            critical_threshold_pct=100.0,
            enforcement_action="BLOCK",
        )
    ])

    # 1. Normal spend: $50.00 current + $5.00 estimate = $55.00 (55% -> ALLOW)
    res_allow = engine.evaluate_request(
        team="data-science",
        estimated_cost=Decimal("5.00"),
        current_team_spend=Decimal("50.00"),
    )
    assert res_allow.allowed is True
    assert res_allow.action == "ALLOW"
    assert res_allow.utilization_pct == 55.0

    # 2. Warning spend: $82.00 current + $3.00 estimate = $85.00 (85% -> WARN)
    res_warn = engine.evaluate_request(
        team="data-science",
        estimated_cost=Decimal("3.00"),
        current_team_spend=Decimal("82.00"),
    )
    assert res_warn.allowed is True
    assert res_warn.action == "WARN"
    assert "BUDGET WARNING" in res_warn.message

    # 3. Breach spend: $98.00 current + $5.00 estimate = $103.00 (103% -> BLOCK)
    res_block = engine.evaluate_request(
        team="data-science",
        estimated_cost=Decimal("5.00"),
        current_team_spend=Decimal("98.00"),
    )
    assert res_block.allowed is False
    assert res_block.action == "BLOCK"
    assert "BUDGET BREACH" in res_block.message


def test_guardrails_api_endpoints():
    """Verify /api/v1/anomalies and /api/v1/guardrails endpoints."""
    client = TestClient(app, client=("127.0.0.1", 50000))

    # 1. Anomalies endpoint
    res_anomalies = client.get("/api/v1/anomalies")
    assert res_anomalies.status_code == 200
    alerts = res_anomalies.json()
    assert isinstance(alerts, list)

    # 2. Guardrails summary endpoint
    res_guardrails = client.get("/api/v1/guardrails")
    assert res_guardrails.status_code == 200
    statuses = res_guardrails.json()
    assert isinstance(statuses, list)
    assert any("team" in item and "utilization_pct" in item for item in statuses)

    # 3. Guardrail check evaluation endpoint
    payload = {"team": "engineering", "estimated_cost_usd": 0.05}
    res_check = client.post("/api/v1/guardrails/check", json=payload)
    assert res_check.status_code == 200
    check_result = res_check.json()
    assert "allowed" in check_result
    assert check_result["action"] in {"ALLOW", "WARN", "BLOCK"}
