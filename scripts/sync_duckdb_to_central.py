"""Ask the local proxy to flush its open DuckDB ledger to central PostgreSQL."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import httpx


def main() -> int:
    proxy_url = os.getenv("FINOPS_PROXY_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        print("Set FINOPS_PROXY_URL to the local TokenLens proxy URL.", file=sys.stderr)
        return 2
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        print("Remote proxy URLs require HTTPS.", file=sys.stderr)
        return 2

    headers = {}
    api_token = os.getenv("FINOPS_API_TOKEN")
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    try:
        response = httpx.post(
            proxy_url + "/internal/central-sync",
            headers=headers,
            timeout=300.0,
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        print(f"Could not reach the local TokenLens proxy ({type(exc).__name__}).", file=sys.stderr)
        return 1
    if not response.is_success:
        print(f"Central sync failed (HTTP {response.status_code}).", file=sys.stderr)
        return 1
    try:
        result = response.json()
    except ValueError:
        print("Central sync returned an invalid response.", file=sys.stderr)
        return 1
    print(
        "Central sync complete: "
        f"{int(result.get('acknowledged', 0))} acknowledged, "
        f"{int(result.get('inserted', 0))} newly inserted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
