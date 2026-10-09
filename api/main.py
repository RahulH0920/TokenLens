"""Small local API that exposes the existing FinOps engine to a frontend.

The demo loads its seed CSVs once at startup. Requests posted to /api/v1/requests
are kept in process memory and are lost when the API restarts.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.cost_engine import CostEngine
from core.importer import DataImporter
from core.models import RequestRecord
from core.reconciliation import ReconciliationEngine
from core.anomaly_engine import AnomalyDetector
from core.guardrails import GuardrailEngine

DATA_DIR = BASE_DIR / "data"
DEPLOYMENT_ENV = os.getenv("FINOPS_ENV", "development").strip().lower()
API_TOKEN = os.getenv("FINOPS_API_TOKEN")
MAX_BODY_BYTES = int(os.getenv("FINOPS_MAX_BODY_BYTES", "65536"))
MAX_MEMORY_REQUESTS = int(os.getenv("FINOPS_MAX_MEMORY_REQUESTS", "10000"))
if DEPLOYMENT_ENV == "production":
    required_deployment_settings = (
        "FINOPS_API_TOKEN", "FINOPS_ALLOWED_HOSTS", "FINOPS_CORS_ORIGINS"
    )
    missing_settings = [key for key in required_deployment_settings if not os.getenv(key)]
    if missing_settings:
        raise RuntimeError(
            "Production requires explicit settings: " + ", ".join(missing_settings)
        )
    if "*" in os.getenv("FINOPS_CORS_ORIGINS", "").split(","):
        raise RuntimeError("Wildcard CORS origins are not allowed in production")
if API_TOKEN and len(API_TOKEN) < 32:
    raise RuntimeError("FINOPS_API_TOKEN must contain at least 32 characters")
if MAX_BODY_BYTES < 1 or MAX_MEMORY_REQUESTS < 1:
    raise RuntimeError("Request body and in-memory request limits must be positive")

app = FastAPI(
    title="LLM FinOps Backend API",
    version="1.0.0",
    description="Dashboard analytics and request logging for the LLM FinOps prototype.",
)
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "FINOPS_CORS_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501,http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
allowed_hosts = [
    host.strip()
    for host in os.getenv(
        "FINOPS_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
    ).split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

importer = DataImporter()
pricing_records = importer.load_pricing(DATA_DIR / "model_pricing.csv")
request_records, rejected_records, ingestion_stats = importer.load_requests(
    DATA_DIR / "sample_requests.csv"
)
engine = CostEngine()
engine.load_pricing_records(pricing_records)
priced_records = engine.process_requests(request_records)
seed_priced_records = tuple(priced_records)
data_lock = RLock()
anomaly_detector = AnomalyDetector()
guardrail_engine = GuardrailEngine()


class NewRequest(BaseModel):
    """Request metadata accepted from the prototype logger."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=200)
    timestamp_utc: datetime | None = None
    timestamp: str | None = None
    team: str = Field(default="unattributed", min_length=1, max_length=200)
    feature: str = Field(default="unassigned", min_length=1, max_length=200)
    user_id: str = Field(default="unknown_user", min_length=1, max_length=200)
    provider: str = Field(default="openai", min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    status: str = Field(default="success", min_length=1, max_length=50)
    latency_ms: int = Field(default=0, ge=0)
    env: str = Field(default="production", min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_token_counts(self):
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        if self.timestamp and self.timestamp_utc:
            raise ValueError("provide only one of timestamp or timestamp_utc")
        return self


def _frame() -> pd.DataFrame:
    return engine.get_priced_dataframe(priced_records)


def _filtered_frame(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    team: str | None = None,
    feature: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    env: str | None = None,
) -> pd.DataFrame:
    frame = _frame()
    if start_date:
        frame = frame[frame["timestamp_utc"].dt.date >= start_date]
    if end_date:
        frame = frame[frame["timestamp_utc"].dt.date <= end_date]
    for column, value in (
        ("team", team), ("feature", feature), ("model", model),
        ("user_id", user_id), ("provider", provider), ("status", status), ("env", env),
    ):
        if value:
            frame = frame[frame[column].astype(str) == value]
    return frame


def _request_items(frame: pd.DataFrame) -> list[dict]:
    # Convert timestamps and numpy scalar types into JSON-safe values.
    records = frame.where(pd.notna(frame), None).to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if hasattr(value, "isoformat"):
                record[key] = value.isoformat()
            elif hasattr(value, "item"):
                record[key] = value.item()
    return records


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "llm-finops-backend"}


@app.middleware("http")
async def secure_api_requests(request, call_next):
    """Apply baseline transport headers, payload limits, and optional API auth."""
    client_host = request.client.host if request.client else None
    is_loopback = False
    if client_host:
        try:
            import ipaddress

            is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = client_host.lower() in {"localhost", "testclient"}
    current_token = os.getenv("FINOPS_API_TOKEN", API_TOKEN)
    if current_token and len(current_token) < 32:
        from starlette.responses import JSONResponse

        return JSONResponse(status_code=500, content={"detail": "FINOPS_API_TOKEN misconfigured: must contain at least 32 characters"})
    if request.url.path != "/health" and request.method != "OPTIONS":
        if not current_token and not is_loopback:
            from starlette.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": "Remote access is disabled without FINOPS_API_TOKEN"})
    if request.url.path != "/health" and request.method != "OPTIONS" and current_token:
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied_token, current_token):
            from starlette.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length_val = int(content_length)
            invalid_length = length_val < 0 or length_val > MAX_BODY_BYTES
        except ValueError:
            invalid_length = True
        if invalid_length:
            from starlette.responses import JSONResponse

            return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    if request.method in {"POST", "PUT", "PATCH"} and not request.headers.get("content-type"):
        headers = list(request.scope["headers"])
        headers.append((b"content-type", b"application/json"))
        request.scope["headers"] = headers

    if request.method in {"POST", "PUT", "PATCH"}:
        # Enforce the limit against received bytes too: Content-Length can be
        # absent for chunked bodies, and must never be the only size check.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                from starlette.responses import JSONResponse

                return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        # BaseHTTPMiddleware's cached request replays `_body` to downstream.
        request._body = bytes(body)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.getenv("FINOPS_BEHIND_HTTPS_PROXY", "false").lower() == "true":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/v1/options")
def get_filter_options() -> dict:
    frame = _frame()
    return {
        key: sorted(frame[key].dropna().astype(str).unique().tolist())
        for key in ("team", "feature", "model", "user_id", "provider", "status", "env")
    }


@app.get("/api/v1/summary")
def get_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    team: str | None = None,
    feature: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    env: str | None = None,
) -> dict:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    frame = _filtered_frame(
        start_date=start_date, end_date=end_date, team=team,
        feature=feature, model=model, user_id=user_id,
        provider=provider, status=status, env=env,
    )
    spend = frame.loc[~frame["missing_price"], "total_cost_usd"].sum()
    priced_count = int((~frame["missing_price"]).sum())
    unattributed = frame[frame["is_unattributed"]]
    return {
        "total_spend_usd": round(float(spend), 6),
        "total_requests": int(len(frame)),
        "total_input_tokens": int(frame["input_tokens"].sum()),
        "total_output_tokens": int(frame["output_tokens"].sum()),
        "average_cost_per_request_usd": round(float(spend / priced_count), 6) if priced_count else 0,
        "priced_requests": priced_count,
        "unattributed_requests": int(len(unattributed)),
        "unattributed_spend_usd": round(float(unattributed.loc[~unattributed["missing_price"], "total_cost_usd"].sum()), 6),
        "missing_pricing_requests": int(frame["missing_price"].sum()),
    }


@app.get("/api/v1/breakdown")
def get_breakdown(
    by: Literal["team", "feature", "model", "user_id", "date"] = "team",
    start_date: date | None = None,
    end_date: date | None = None,
    team: str | None = None,
    feature: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    env: str | None = None,
) -> list[dict]:
    frame = _filtered_frame(
        start_date=start_date, end_date=end_date, team=team,
        feature=feature, model=model, user_id=user_id,
        provider=provider, status=status, env=env,
    )
    if by == "date":
        frame = frame.assign(date=frame["timestamp_utc"].dt.date.astype(str))
        group_column = "date"
    else:
        group_column = by
    grouped = frame.groupby(group_column, dropna=False).agg(
        cost_usd=("total_cost_usd", "sum"),
        request_count=("request_id", "count"),
        input_tokens=("input_tokens", "sum"),
        output_tokens=("output_tokens", "sum"),
        missing_pricing_requests=("missing_price", "sum"),
    ).reset_index().sort_values("cost_usd", ascending=False)
    return _request_items(grouped)


@app.get("/api/v1/requests")
def get_requests(
    start_date: date | None = None,
    end_date: date | None = None,
    team: str | None = None,
    feature: str | None = None,
    model: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    env: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    frame = _filtered_frame(
        start_date=start_date, end_date=end_date, team=team,
        feature=feature, model=model, user_id=user_id,
        provider=provider, status=status, env=env,
    ).sort_values("timestamp_utc", ascending=False)
    return {
        "total": int(len(frame)),
        "limit": limit,
        "offset": offset,
        "items": _request_items(frame.iloc[offset:offset + limit]),
    }


@app.post("/api/v1/requests", status_code=201)
def add_request(body: NewRequest) -> dict:
    from datetime import datetime, timezone

    if any(record.request_id == body.request_id for record in priced_records):
        raise HTTPException(status_code=409, detail="request_id already exists")
    try:
        timestamp = (
            datetime.fromisoformat(body.timestamp.replace("Z", "+00:00"))
            if body.timestamp
            else body.timestamp_utc
            if body.timestamp_utc
            else datetime.now(timezone.utc)
        )
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        request = RequestRecord(
            request_id=body.request_id,
            timestamp_utc=timestamp,
            team=body.team,
            feature=body.feature,
            user_id=body.user_id,
            provider=body.provider,
            model=body.model.strip().lower(),
            input_tokens=body.input_tokens,
            output_tokens=body.output_tokens,
            cached_tokens=body.cached_tokens,
            status=body.status,
            latency_ms=body.latency_ms,
            env=body.env,
            source="api",
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request timestamp or fields: {exc}") from exc

    with data_lock:
        if any(record.request_id == body.request_id for record in priced_records):
            raise HTTPException(status_code=409, detail="request_id already exists")
        if len(priced_records) - len(seed_priced_records) >= MAX_MEMORY_REQUESTS:
            raise HTTPException(status_code=429, detail="Demo request capacity reached; restart the service to reset it")
        priced = engine.calculate_request_cost(request)
        priced_records.append(priced)
    return {
        "request_id": priced.request_id,
        "team": priced.team,
        "feature": priced.feature,
        "model": priced.model,
        "input_cost_usd": str(priced.input_cost_usd),
        "output_cost_usd": str(priced.output_cost_usd),
        "total_cost_usd": str(priced.total_cost_usd),
        "missing_price": priced.missing_price,
        "persistence": "process_memory_only",
    }


@app.get("/api/v1/reconciliation")
def get_reconciliation() -> dict:
    with open(DATA_DIR / "golden_validation.json", encoding="utf-8") as file:
        expected = json.load(file)
    # The golden manifest describes the seeded CSV. API-logged records are
    # outside that manifest and must not make its reconciliation drift.
    frame = engine.get_priced_dataframe(list(seed_priced_records))
    checks = ReconciliationEngine().run_reconciliation(
        actual_df=frame,
        expected_manifest=expected,
        ingestion_stats=ingestion_stats,
        rejected_records=rejected_records,
    )
    return {
        "overall_status": "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS",
        "checks": [check.model_dump(mode="json") for check in checks],
        "ingestion": ingestion_stats,
    }


class GuardrailCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str = Field(min_length=1, max_length=100)
    estimated_cost_usd: float = Field(ge=0.0, le=100000.0)


@app.get("/api/v1/anomalies")
def get_anomalies(
    severity: Literal["ALL", "CRITICAL", "WARNING", "INFO"] = "ALL",
    anomaly_type: Literal["ALL", "COST_SPIKE", "TOKEN_BLOAT", "RUNAWAY_RATE"] = "ALL",
) -> list[dict]:
    frame = _frame()
    alerts = anomaly_detector.get_all_anomalies(frame)
    if severity != "ALL":
        alerts = [a for a in alerts if a.severity == severity]
    if anomaly_type != "ALL":
        alerts = [a for a in alerts if a.anomaly_type == anomaly_type]
    return [a.model_dump(mode="json") for a in alerts]


@app.get("/api/v1/guardrails")
def get_guardrails() -> list[dict]:
    frame = _frame()
    return guardrail_engine.get_summary_status(frame)


@app.post("/api/v1/guardrails/check")
def check_guardrails(body: GuardrailCheckRequest) -> dict:
    from decimal import Decimal

    frame = _frame()
    team_spend = (
        frame[frame["team"].astype(str) == body.team]["total_cost_usd"].sum()
        if not frame.empty
        else 0.0
    )
    res = guardrail_engine.evaluate_request(
        team=body.team,
        estimated_cost=Decimal(str(body.estimated_cost_usd)),
        current_team_spend=Decimal(str(team_spend)),
    )
    return res.model_dump(mode="json")
