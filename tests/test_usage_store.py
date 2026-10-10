from datetime import datetime, timezone
from decimal import Decimal

from core.models import PricedRequest
from core.usage_store import UsageStore


def make_request(request_id: str) -> PricedRequest:
    return PricedRequest(
        request_id=request_id,
        timestamp_utc=datetime.now(timezone.utc),
        team="engineering",
        feature="chat",
        user_id="user-1",
        provider="google",
        model="gemini-1.5-flash",
        input_tokens=12,
        output_tokens=8,
        cached_tokens=2,
        status="success",
        latency_ms=42,
        env="test",
        source="provider_api",
        input_cost_usd=Decimal("0.000001"),
        output_cost_usd=Decimal("0.000002"),
        cached_cost_usd=Decimal("0.000000"),
        total_cost_usd=Decimal("0.000003"),
        missing_price=False,
        is_unattributed=False,
    )


def test_usage_metadata_and_central_sync_state_are_visible(tmp_path):
    store = UsageStore(tmp_path / "usage.duckdb")
    store.append(make_request("request-1"), provider_host="generativelanguage.googleapis.com", provider_request_id="provider-1")

    summary = store.summary()
    row = store.requests(provider="google", limit=10, offset=0)["items"][0]
    assert summary["central_pending_requests"] == 1
    assert row["provider_request_id"] == "provider-1"
    assert row["input_tokens"] == 12
    assert row["cached_tokens"] == 2
    assert row["central_synced"] is False

    store.mark_central_synced(["request-1"])
    assert store.summary()["central_pending_requests"] == 0
    assert store.requests(provider="google", limit=10, offset=0)["items"][0]["central_synced"] is True
    store.connection.close()
