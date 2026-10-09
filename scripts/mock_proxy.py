"""Local OpenAI and Gemini API proxy with persistent DuckDB usage capture."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import ipaddress
import os
from pathlib import Path
import sys
from threading import RLock
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
import secrets
import pandas as pd
import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.attribution import AttributionParser
from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.models import RequestRecord
from core.usage_store import UsageStore


app = FastAPI(
    title="TokenLens Provider Usage Proxy",
    version="2.0",
    description=(
        "Forwards requests to OpenAI and Gemini, records returned token usage, "
        "calculates cost, and stores an auditable local DuckDB ledger."
    ),
)

DATA_DIR = BASE_DIR / "data"
MAX_BODY_BYTES = int(os.getenv("FINOPS_MAX_BODY_BYTES", "65536"))
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("FINOPS_PROVIDER_TIMEOUT_SECONDS", "90"))
DUCKDB_PATH = Path(
    os.getenv("FINOPS_DUCKDB_PATH", str(DATA_DIR / "tokenlens_usage.duckdb"))
).expanduser()

importer = DataImporter()
pricing_records = importer.load_pricing(DATA_DIR / "model_pricing.csv")
seed_requests, seed_rejects, seed_stats = importer.load_requests(DATA_DIR / "sample_requests.csv")
cost_engine = CostEngine()
cost_engine.load_pricing_records(pricing_records)
seed_priced = cost_engine.process_requests(seed_requests)
store = UsageStore(DUCKDB_PATH)
store.seed(seed_priced)
attribution_parser = AttributionParser()
write_lock = RLock()

OPENAI_HOST = "api.openai.com"
GEMINI_HOST = "generativelanguage.googleapis.com"
OPENAI_CHAT_URL = f"https://{OPENAI_HOST}/v1/chat/completions"
GEMINI_API_ROOT = f"https://{GEMINI_HOST}/v1beta/models"


def _api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _attribution(
    x_team: str | None,
    x_feature: str | None,
    x_user: str | None,
    x_env: str | None,
) -> dict[str, str]:
    return attribution_parser.parse_attribution(
        {"x-team": x_team, "x-feature": x_feature, "x-user": x_user, "x-env": x_env}
    )


def _validate_body(body: dict[str, Any], provider: str) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise HTTPException(status_code=422, detail="A model identifier of 1 to 200 characters is required")
    if body.get("stream") is True:
        raise HTTPException(status_code=422, detail="Streaming is not supported by this usage-capture proxy")
    if provider == "openai":
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=422, detail="messages must be a non-empty array")
    return model.strip()


def _upstream_json(url: str, *, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    try:
        upstream = httpx.post(
            url,
            headers=headers,
            json=body,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Provider request timed out") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Could not connect to provider") from exc

    if not upstream.is_success:
        # Never return upstream headers or request details that could expose a key.
        raise HTTPException(
            status_code=502,
            detail=f"Provider returned HTTP {upstream.status_code}; no usage was recorded",
        )
    try:
        result = upstream.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Provider returned invalid JSON; no usage was recorded") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Provider returned an unexpected response; no usage was recorded")
    return result


def _record_usage(
    *,
    provider: str,
    model: str,
    provider_host: str,
    provider_request_id: str | None,
    usage: dict[str, Any],
    team: str | None,
    feature: str | None,
    user: str | None,
    env: str | None,
    elapsed_ms: int,
    response: Response,
) -> dict[str, Any]:
    try:
        if provider == "openai":
            prompt_total = int(usage["prompt_tokens"])
            output_tokens = int(usage["completion_tokens"])
            details = usage.get("prompt_tokens_details") or {}
            cached_tokens = int(details.get("cached_tokens", 0) or 0)
        else:
            prompt_total = int(usage["promptTokenCount"])
            output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
            output_tokens += int(usage.get("thoughtsTokenCount", 0) or 0)
            cached_tokens = int(usage.get("cachedContentTokenCount", 0) or 0)
        if min(prompt_total, output_tokens, cached_tokens) < 0 or cached_tokens > prompt_total:
            raise ValueError("invalid token counts")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Provider response did not include valid token usage; no usage was recorded",
        ) from exc

    # Provider usage reports prompt tokens including cached tokens. TokenLens
    # stores cached tokens separately so the cache rate is not charged twice.
    input_tokens = prompt_total - cached_tokens
    tags = _attribution(team, feature, user, env)
    now = datetime.now(timezone.utc)
    request_record = RequestRecord(
        request_id=str(uuid4()),
        timestamp_utc=now,
        team=tags["team"],
        feature=tags["feature"],
        user_id=tags["user_id"],
        provider=provider,
        model=model.lower(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        status="success",
        latency_ms=max(0, elapsed_ms),
        env=tags["env"],
        source="provider_api",
    )
    priced = cost_engine.calculate_request_cost(request_record)
    with write_lock:
        store.append(
            priced,
            provider_host=provider_host,
            provider_request_id=provider_request_id,
        )

    response.headers["X-FinOps-Request-ID"] = priced.request_id
    response.headers["X-FinOps-Cost-USD"] = str(priced.total_cost_usd)
    response.headers["X-FinOps-Team"] = priced.team
    response.headers["X-FinOps-Missing-Price"] = str(priced.missing_price).lower()
    return {
        "request_id": priced.request_id,
        "provider": provider,
        "provider_host": provider_host,
        "provider_request_id": provider_request_id,
        "model": priced.model,
        "team": priced.team,
        "feature": priced.feature,
        "user_id": priced.user_id,
        "input_tokens": priced.input_tokens,
        "cached_tokens": priced.cached_tokens,
        "output_tokens": priced.output_tokens,
        "total_cost_usd": str(priced.total_cost_usd),
        "missing_price": priced.missing_price,
    }


def _usage_frame() -> pd.DataFrame:
    return store.dataframe()


@app.middleware("http")
async def cap_request_body(request: Request, call_next):
    """Bound both declared and chunked request bodies before JSON parsing."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        if declared_length < 0 or declared_length > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    if request.method in {"POST", "PUT", "PATCH"}:
        received = bytearray()
        async for chunk in request.stream():
            received.extend(chunk)
            if len(received) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        request._body = bytes(received)
    return await call_next(request)


@app.middleware("http")
async def protect_proxy(request: Request, call_next):
    """Keep unauthenticated use loopback-only; require bearer auth remotely."""
    configured_token = os.getenv("FINOPS_API_TOKEN")
    if configured_token and len(configured_token) < 32:
        return JSONResponse(status_code=500, content={"detail": "FINOPS_API_TOKEN must contain at least 32 characters"})

    if request.url.path != "/health":
        if configured_token:
            scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, configured_token):
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        else:
            host = request.client.host if request.client else ""
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = host.lower() in {"localhost", "testclient"}
            if not is_loopback:
                return JSONResponse(status_code=403, content={"detail": "Remote access is disabled without FINOPS_API_TOKEN"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "tokenlens-provider-usage-proxy",
        "providers": {
            "openai": bool(_api_key("openai")),
            "gemini": bool(_api_key("gemini")),
        },
        "stored_usage_records": store.count(),
    }


@app.post("/v1/chat/completions")
def openai_chat_completions(
    body: dict[str, Any],
    response: Response,
    x_team: str | None = Header(default=None),
    x_feature: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
    x_env: str | None = Header(default="production"),
) -> JSONResponse:
    """Forward an OpenAI Chat Completions request and capture returned usage."""
    model = _validate_body(body, "openai")
    api_key = _api_key("openai")
    if not api_key:
        raise HTTPException(status_code=503, detail="Set OPENAI_API_KEY to enable OpenAI requests")
    started = perf_counter()
    result = _upstream_json(
        OPENAI_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body=body,
    )
    elapsed_ms = int((perf_counter() - started) * 1000)
    usage = result.get("usage")
    if not isinstance(usage, dict):
        raise HTTPException(status_code=502, detail="OpenAI response has no usage data; no usage was recorded")
    finops = _record_usage(
        provider="openai",
        model=model,
        provider_host=OPENAI_HOST,
        provider_request_id=result.get("id"),
        usage=usage,
        team=x_team,
        feature=x_feature,
        user=x_user,
        env=x_env,
        elapsed_ms=elapsed_ms,
        response=response,
    )
    result["finops_attribution"] = finops
    return JSONResponse(content=result, headers=dict(response.headers))


@app.post("/v1beta/models/{model}:generateContent")
def gemini_generate_content(
    model: str,
    body: dict[str, Any],
    response: Response,
    x_team: str | None = Header(default=None),
    x_feature: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
    x_env: str | None = Header(default="production"),
) -> JSONResponse:
    """Forward Gemini's native generateContent format and capture usageMetadata."""
    model = model.strip()
    if not model or len(model) > 200:
        raise HTTPException(status_code=422, detail="A valid Gemini model identifier is required")
    api_key = _api_key("gemini")
    if not api_key:
        raise HTTPException(status_code=503, detail="Set GEMINI_API_KEY or GOOGLE_API_KEY to enable Gemini requests")
    if "contents" not in body:
        raise HTTPException(status_code=422, detail="Gemini requests must include contents")
    started = perf_counter()
    url = f"{GEMINI_API_ROOT}/{quote(model, safe='-_.')}:generateContent"
    result = _upstream_json(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        body=body,
    )
    elapsed_ms = int((perf_counter() - started) * 1000)
    usage = result.get("usageMetadata")
    if not isinstance(usage, dict):
        raise HTTPException(status_code=502, detail="Gemini response has no usageMetadata; no usage was recorded")
    finops = _record_usage(
        provider="google",
        model=model,
        provider_host=GEMINI_HOST,
        provider_request_id=result.get("responseId"),
        usage=usage,
        team=x_team,
        feature=x_feature,
        user=x_user,
        env=x_env,
        elapsed_ms=elapsed_ms,
        response=response,
    )
    result["finops_attribution"] = finops
    return JSONResponse(content=result, headers=dict(response.headers))


@app.get("/usage/summary")
def usage_summary() -> dict[str, Any]:
    frame = _usage_frame()
    return {
        "total_requests": int(len(frame)),
        "total_input_tokens": int(frame["input_tokens"].sum()) if not frame.empty else 0,
        "total_cached_tokens": int(frame["cached_tokens"].sum()) if not frame.empty else 0,
        "total_output_tokens": int(frame["output_tokens"].sum()) if not frame.empty else 0,
        "total_cost_usd": round(float(frame["total_cost_usd"].sum()), 6) if not frame.empty else 0.0,
        "providers": frame.groupby("provider").size().to_dict() if not frame.empty else {},
        "missing_price_requests": int(frame["missing_price"].sum()) if not frame.empty else 0,
    }


@app.get("/usage/breakdown")
def usage_breakdown(
    by: Literal["provider", "provider_host", "model", "team", "feature", "user_id"] = Query("provider"),
) -> list[dict[str, Any]]:
    frame = _usage_frame()
    if frame.empty:
        return []
    grouped = frame.groupby(by, dropna=False).agg(
        total_cost_usd=("total_cost_usd", "sum"),
        request_count=("request_id", "count"),
        input_tokens=("input_tokens", "sum"),
        cached_tokens=("cached_tokens", "sum"),
        output_tokens=("output_tokens", "sum"),
    ).reset_index()
    return json.loads(grouped.to_json(orient="records"))


@app.get("/usage/requests")
def usage_requests(
    provider: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    frame = _usage_frame().sort_values("timestamp_utc", ascending=False)
    if provider:
        frame = frame[frame["provider"].astype(str) == provider.lower()]
    return {
        "total": int(len(frame)),
        "limit": limit,
        "offset": offset,
        "items": json.loads(frame.iloc[offset:offset + limit].to_json(orient="records", date_format="iso")),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
