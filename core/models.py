"""Data models for LLM FinOps request records, pricing entries, and validation reports."""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator


class RequestRecord(BaseModel):
    request_id: str
    timestamp_utc: datetime
    team: str = "unattributed"
    feature: str = "unassigned"
    user_id: str = "unknown_user"
    provider: str = "openai"
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    status: str = "success"
    latency_ms: int = Field(default=0, ge=0)
    env: str = "production"
    source: str = "import"

    @field_validator("input_tokens", "output_tokens", "cached_tokens")
    @classmethod
    def validate_non_negative_tokens(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Token counts cannot be negative")
        return v


class PricingRecord(BaseModel):
    model: str
    provider: str = "unknown"
    input_usd_per_1m: Decimal
    output_usd_per_1m: Decimal
    cached_usd_per_1m: Decimal = Decimal("0.0")
    effective_from: date
    effective_to: Optional[date] = None


class PricedRequest(BaseModel):
    request_id: str
    timestamp_utc: datetime
    team: str
    feature: str
    user_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    status: str
    latency_ms: int
    env: str
    source: str
    input_cost_usd: Decimal
    output_cost_usd: Decimal
    cached_cost_usd: Decimal
    total_cost_usd: Decimal
    missing_price: bool
    is_unattributed: bool


class ValidationCheckResult(BaseModel):
    metric: str
    category: str
    expected: Any
    actual: Any
    difference: Any
    tolerance: str
    status: str  # 'PASS' | 'FAIL' | 'WARNING'
    details: str
