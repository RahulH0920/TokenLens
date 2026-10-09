"""Persistent DuckDB ledger for provider usage and deterministic cost records."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

import duckdb
import pandas as pd

from core.models import PricedRequest


class UsageStore:
    """Store token usage and costs without retaining prompts or API credentials."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(self.path))
        self.lock = RLock()
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                request_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                provider_host VARCHAR NOT NULL,
                provider_request_id VARCHAR,
                model VARCHAR NOT NULL,
                timestamp_utc TIMESTAMPTZ NOT NULL,
                team VARCHAR NOT NULL,
                feature VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                env VARCHAR NOT NULL,
                input_tokens BIGINT NOT NULL,
                output_tokens BIGINT NOT NULL,
                cached_tokens BIGINT NOT NULL,
                latency_ms INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                input_cost_usd DECIMAL(18, 6) NOT NULL,
                output_cost_usd DECIMAL(18, 6) NOT NULL,
                cached_cost_usd DECIMAL(18, 6) NOT NULL,
                total_cost_usd DECIMAL(18, 6) NOT NULL,
                missing_price BOOLEAN NOT NULL,
                is_unattributed BOOLEAN NOT NULL
            )
            """
        )

    def count(self) -> int:
        with self.lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])

    def append(
        self,
        request: PricedRequest,
        *,
        provider_host: str,
        provider_request_id: str | None = None,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO usage_events VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    request.request_id,
                    request.provider,
                    provider_host,
                    provider_request_id,
                    request.model,
                    request.timestamp_utc,
                    request.team,
                    request.feature,
                    request.user_id,
                    request.env,
                    request.input_tokens,
                    request.output_tokens,
                    request.cached_tokens,
                    request.latency_ms,
                    request.status,
                    request.source,
                    request.input_cost_usd,
                    request.output_cost_usd,
                    request.cached_cost_usd,
                    request.total_cost_usd,
                    request.missing_price,
                    request.is_unattributed,
                ],
            )

    def seed(self, records: list[PricedRequest]) -> None:
        """Load the supplied sample dataset once when creating a fresh ledger."""
        if self.count() != 0:
            return
        for record in records:
            self.append(
                record,
                provider_host="seed_csv",
                provider_request_id=record.request_id,
            )

    def dataframe(self) -> pd.DataFrame:
        with self.lock:
            return self.connection.execute(
                "SELECT * FROM usage_events ORDER BY timestamp_utc, request_id"
            ).df()
