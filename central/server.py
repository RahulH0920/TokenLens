"""Authenticated PostgreSQL receiver for DuckDB usage ledgers from host agents."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

LOGGER = logging.getLogger("tokenlens.central")
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_BATCH_SIZE = 500
HOST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def _read_host_tokens() -> dict[str, str]:
    raw = os.getenv("CENTRAL_INGEST_TOKENS_JSON", "")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CENTRAL_INGEST_TOKENS_JSON must contain a JSON object") from exc
    if not isinstance(tokens, dict) or not tokens:
        raise RuntimeError("Configure at least one host token in CENTRAL_INGEST_TOKENS_JSON")
    for host_id, token in tokens.items():
        if not isinstance(host_id, str) or not HOST_ID_PATTERN.fullmatch(host_id):
            raise RuntimeError("Central host IDs must use letters, numbers, dot, underscore, or dash")
        if not isinstance(token, str) or len(token) < 32 or not token.isascii():
            raise RuntimeError("Each central host token must contain at least 32 ASCII characters")
    if len(set(tokens.values())) != len(tokens):
        raise RuntimeError("Each host must have a unique central token")
    return tokens


def _connect() -> psycopg.Connection:
    """Connect using libpq environment settings without logging credentials."""
    return psycopg.connect(
        host=os.getenv("PGHOST", "postgres"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "tokenlens"),
        user=os.getenv("PGUSER", "tokenlens_ingest"),
        password=os.getenv("PGPASSWORD", ""),
        sslmode=os.getenv("PGSSLMODE", "prefer"),
        connect_timeout=5,
    )


class UsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=32)
    provider_host: str = Field(min_length=1, max_length=255)
    provider_request_id: str | None = Field(default=None, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    timestamp_utc: datetime
    team: str = Field(min_length=1, max_length=200)
    feature: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    env: str = Field(min_length=1, max_length=100)
    input_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)
    output_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)
    cached_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)
    latency_ms: int = Field(ge=0, le=2_147_483_647)
    status: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=50)
    input_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    output_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    cached_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    total_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    missing_price: bool
    is_unattributed: bool

    @model_validator(mode="after")
    def validate_usage(self) -> "UsageEvent":
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        value = self.timestamp_utc
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must include a timezone")
        if self.total_cost_usd != self.input_cost_usd + self.output_cost_usd + self.cached_cost_usd:
            raise ValueError("total_cost_usd must equal its component costs")
        return self


class UsageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[UsageEvent] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.host_tokens = _read_host_tokens()
    try:
        with _connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        LOGGER.error("Could not initialize central PostgreSQL schema (%s)", type(exc).__name__)
        raise RuntimeError("Central PostgreSQL is unavailable") from exc
    yield


app = FastAPI(
    title="TokenLens Central Usage Receiver",
    version="1.0",
    description="Receives authenticated, idempotent usage batches from host-local DuckDB ledgers.",
    lifespan=lifespan,
)


@app.middleware("http")
async def protect_and_bound_requests(request: Request, call_next):
    if request.url.path != "/health":
        host_id = request.headers.get("x-host-id", "")
        expected = request.app.state.host_tokens.get(host_id)
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if (
            expected is None
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied, expected)
        ):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        request.state.host_id = host_id

    if request.method in {"POST", "PUT", "PATCH"}:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) < 0 or int(declared) > MAX_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        received = bytearray()
        async for chunk in request.stream():
            received.extend(chunk)
            if len(received) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        request._body = bytes(received)

    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with _connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        LOGGER.warning("Central PostgreSQL health check failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Central database unavailable") from exc
    return {"status": "ok", "service": "tokenlens-central-receiver"}


@app.post("/v1/usage/batch")
def receive_usage_batch(batch: UsageBatch, request: Request) -> dict[str, int]:
    host_id = request.state.host_id
    columns = (
        "host_id, request_id, provider, provider_host, provider_request_id, model, timestamp_utc, "
        "team, feature, user_id, env, input_tokens, output_tokens, cached_tokens, latency_ms, "
        "status, source, input_cost_usd, output_cost_usd, cached_cost_usd, total_cost_usd, "
        "missing_price, is_unattributed"
    )
    placeholders = "(" + ", ".join(["%s"] * 23) + ")"
    rows = [
        (
            host_id,
            event.request_id,
            event.provider,
            event.provider_host,
            event.provider_request_id,
            event.model,
            event.timestamp_utc,
            event.team,
            event.feature,
            event.user_id,
            event.env,
            event.input_tokens,
            event.output_tokens,
            event.cached_tokens,
            event.latency_ms,
            event.status,
            event.source,
            event.input_cost_usd,
            event.output_cost_usd,
            event.cached_cost_usd,
            event.total_cost_usd,
            event.missing_price,
            event.is_unattributed,
        )
        for event in batch.events
    ]
    values_sql = ", ".join([placeholders] * len(rows))
    query = (
        f"INSERT INTO usage_events ({columns}) VALUES {values_sql} "
        "ON CONFLICT (host_id, request_id) DO NOTHING RETURNING request_id"
    )
    parameters = [value for row in rows for value in row]
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                inserted = len(cursor.fetchall())
    except psycopg.Error as exc:
        LOGGER.error("Central PostgreSQL batch write failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Central database write failed") from exc
    return {"accepted": len(rows), "inserted": inserted, "duplicates": len(rows) - inserted}
