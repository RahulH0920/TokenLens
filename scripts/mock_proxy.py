"""Local OpenAI and Gemini API proxy with persistent DuckDB usage capture."""

from __future__ import annotations

from datetime import datetime, timezone
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
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
import secrets
import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
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
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
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
ANTHROPIC_HOST = "api.anthropic.com (dummy-simulator)"
OPENAI_CHAT_URL = f"https://{OPENAI_HOST}/v1/chat/completions"
GEMINI_API_ROOT = f"https://{GEMINI_HOST}/v1beta/models"

ACTIVE_MODELS = {
    "gpt-4o": {"provider": "openai", "mode": "real-time"},
    "gemini-1.5-flash": {"provider": "google", "mode": "real-time"},
    "claude-3-5-sonnet": {"provider": "anthropic", "mode": "dummy-data"},
}


def _api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    if provider in {"gemini", "google"}:
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return None


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
        elif provider == "anthropic":
            prompt_total = int(usage.get("prompt_tokens") or usage.get("input_tokens", 0))
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens", 0))
            details = usage.get("prompt_tokens_details") or {}
            cached_tokens = int(details.get("cached_tokens") or usage.get("cache_read_input_tokens", 0) or 0)
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
        "models": {
            "chatgpt": {
                "model": "gpt-4o",
                "provider": "openai",
                "mode": "real-time",
                "configured": bool(_api_key("openai")),
            },
            "gemini": {
                "model": "gemini-1.5-flash",
                "provider": "google",
                "mode": "real-time",
                "configured": bool(_api_key("google")),
            },
            "claude": {
                "model": "claude-3-5-sonnet",
                "provider": "anthropic",
                "mode": "dummy-data",
                "configured": True,
            },
        },
        "providers": {
            "openai": bool(_api_key("openai")),
            "gemini": bool(_api_key("google")),
            "anthropic": True,
        },
        "stored_usage_records": store.count(),
    }


def _generate_dummy_completion(
    model: str,
    body: dict[str, Any],
    response: Response,
    x_team: str | None,
    x_feature: str | None,
    x_user: str | None,
    x_env: str | None,
) -> JSONResponse:
    """Generate synthetic responses & dummy usage for the 3rd model (claude-3-5-sonnet)."""
    import random

    clean_model = "claude-3-5-sonnet"
    messages = body.get("messages") or []
    msg_len = sum(len(str(m.get("content", ""))) for m in messages) if messages else 400
    prompt_tokens = max(150, int(msg_len / 3.8) + random.randint(100, 300))
    completion_tokens = random.randint(180, 520)
    cached_tokens = int(prompt_tokens * 0.25) if random.random() < 0.4 else 0
    elapsed_ms = random.randint(320, 890)

    dummy_content = (
        "TokenLens Dummy AI Model [claude-3-5-sonnet]: This synthetic workload response "
        "is simulated for FinOps telemetry and testing without incurring upstream provider costs."
    )
    result = {
        "id": f"chatcmpl-dummy-{uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": clean_model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": dummy_content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {
                "cached_tokens": cached_tokens,
            },
        },
    }

    response.headers["X-FinOps-Dummy-Mode"] = "true"
    finops = _record_usage(
        provider="anthropic",
        model=clean_model,
        provider_host=ANTHROPIC_HOST,
        provider_request_id=result["id"],
        usage=result["usage"],
        team=x_team,
        feature=x_feature,
        user=x_user,
        env=x_env,
        elapsed_ms=elapsed_ms,
        response=response,
    )
    result["finops_attribution"] = finops
    return JSONResponse(content=result, headers=dict(response.headers))


@app.post("/v1/chat/completions")
def openai_chat_completions(
    body: dict[str, Any],
    response: Response,
    x_team: str | None = Header(default=None),
    x_feature: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
    x_env: str | None = Header(default="production"),
) -> JSONResponse:
    """Forward an OpenAI Chat Completions request or simulate dummy model."""
    model = _validate_body(body, "openai")
    clean_model = model.strip().lower()

    # Route dummy model without requiring an API key
    if clean_model in {"claude-3-5-sonnet", "claude-3.5-sonnet"}:
        return _generate_dummy_completion(
            clean_model, body, response, x_team, x_feature, x_user, x_env
        )

    # ChatGPT (gpt-4o): Real-time API key usage
    if clean_model not in {"gpt-4o", "gpt-4"}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{model}' is not in the active Model Registry. "
                "Allowed models: 'gpt-4o' (ChatGPT, real-time), 'gemini-1.5-flash' (Gemini, real-time), "
                "'claude-3-5-sonnet' (Claude, dummy data)."
            ),
        )

    api_key = _api_key("openai")
    if not api_key:
        raise HTTPException(status_code=503, detail="Set OPENAI_API_KEY to enable real-time OpenAI/ChatGPT requests")
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
    clean_model = model.strip().lower()
    if clean_model != "gemini-1.5-flash":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{model}' is not in the active Model Registry. "
                "Allowed Gemini models: 'gemini-1.5-flash' (Gemini, real-time)."
            ),
        )
    api_key = _api_key("google")
    if not api_key:
        raise HTTPException(status_code=503, detail="Set GEMINI_API_KEY or GOOGLE_API_KEY to enable real-time Gemini requests")
    if "contents" not in body:
        raise HTTPException(status_code=422, detail="Gemini requests must include contents")
    started = perf_counter()
    url = f"{GEMINI_API_ROOT}/{quote(clean_model, safe='-_.')}:generateContent"
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
        model=clean_model,
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


@app.post("/v1/messages")
def anthropic_messages(
    body: dict[str, Any],
    response: Response,
    x_team: str | None = Header(default=None),
    x_feature: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
    x_env: str | None = Header(default="production"),
) -> JSONResponse:
    """Handle Anthropic Messages format for claude-3-5-sonnet with dummy data simulation."""
    model = str(body.get("model", "claude-3-5-sonnet")).strip().lower()
    if model not in {"claude-3-5-sonnet", "claude-3.5-sonnet"}:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not in the active Model Registry. Allowed: 'claude-3-5-sonnet'."
        )
    return _generate_dummy_completion(
        model, body, response, x_team, x_feature, x_user, x_env
    )


@app.post("/v1/simulate/dummy-request")
def simulate_dummy_request(
    team: str = Query(default="engineering"),
    feature: str = Query(default="agent-chat"),
    user_id: str = Query(default="sim_user_01"),
    env: str = Query(default="production"),
) -> dict[str, Any]:
    """Trigger an immediate dummy request generation for claude-3-5-sonnet."""
    dummy_body = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Simulated dummy test request."}],
    }
    dummy_resp = Response()
    res = _generate_dummy_completion(
        "claude-3-5-sonnet", dummy_body, dummy_resp, team, feature, user_id, env
    )
    return json.loads(res.body.decode("utf-8"))


@app.get("/usage/summary")
def usage_summary() -> dict[str, Any]:
    return store.summary()


@app.get("/usage/breakdown")
def usage_breakdown(
    by: Literal["provider", "provider_host", "model", "team", "feature", "user_id"] = Query("provider"),
) -> list[dict[str, Any]]:
    return store.breakdown(by)


@app.get("/usage/requests")
def usage_requests(
    provider: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return store.requests(provider=provider.lower() if provider else None, limit=limit, offset=offset)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
