"""Send pending DuckDB usage rows to a centralized TokenLens receiver."""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

import httpx

from core.usage_store import UsageStore

HOST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
MAX_BATCH_SIZE = 500


def sync_usage_store(store: UsageStore) -> dict[str, int]:
    """Upload pending pages and mark them locally only after central acknowledgement."""
    base_url = os.getenv("FINOPS_CENTRAL_URL", "").strip().rstrip("/")
    host_id = os.getenv("FINOPS_HOST_ID", "").strip()
    token = os.getenv("FINOPS_CENTRAL_TOKEN", "")
    if not base_url or not host_id or not token:
        raise ValueError("Central sync requires FINOPS_CENTRAL_URL, FINOPS_HOST_ID, and FINOPS_CENTRAL_TOKEN")
    if not HOST_ID_PATTERN.fullmatch(host_id):
        raise ValueError("FINOPS_HOST_ID has an invalid format")
    if len(token) < 32:
        raise ValueError("FINOPS_CENTRAL_TOKEN must contain at least 32 characters")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("FINOPS_CENTRAL_URL must be an HTTP or HTTPS base URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote central receivers require HTTPS")

    endpoint = base_url + "/v1/usage/batch"
    ca_bundle = os.getenv("FINOPS_CENTRAL_CA_BUNDLE")
    sent = 0
    inserted = 0
    with httpx.Client(timeout=30.0, verify=ca_bundle or True, follow_redirects=False) as client:
        while True:
            events = store.pending_for_central(limit=MAX_BATCH_SIZE)
            if not events:
                return {"acknowledged": sent, "inserted": inserted}
            try:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Host-ID": host_id,
                        "Content-Type": "application/json",
                    },
                    json={"events": events},
                )
            except httpx.RequestError as exc:
                raise RuntimeError(f"Could not reach central receiver ({type(exc).__name__})") from exc
            if not response.is_success:
                raise RuntimeError(f"Central receiver returned HTTP {response.status_code}")
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError("Central receiver returned an invalid acknowledgement") from exc
            if not isinstance(result, dict) or result.get("accepted") != len(events):
                raise RuntimeError("Central receiver did not acknowledge the whole batch")
            store.mark_central_synced([event["request_id"] for event in events])
            sent += len(events)
            inserted += int(result.get("inserted", 0))
