"""Persistent DuckDB ledger for provider usage and deterministic cost records."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

import duckdb

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
                is_unattributed BOOLEAN NOT NULL,
                central_synced BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        # Add the sync marker to existing local ledgers without replacing their data.
        self.connection.execute(
            "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS central_synced BOOLEAN DEFAULT FALSE"
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
        self._insert_many(
            [
                (
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
                )
            ]
        )

    def _insert_many(self, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.lock:
            self.connection.execute("BEGIN TRANSACTION")
            try:
                self.connection.executemany(
                    """
                    INSERT INTO usage_events (
                        request_id, provider, provider_host, provider_request_id,
                        model, timestamp_utc, team, feature, user_id, env,
                        input_tokens, output_tokens, cached_tokens, latency_ms,
                        status, source, input_cost_usd, output_cost_usd,
                        cached_cost_usd, total_cost_usd, missing_price, is_unattributed
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    rows,
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def seed(self, records: list[PricedRequest]) -> None:
        """Load the supplied sample dataset once when creating a fresh ledger."""
        if self.count() != 0:
            return
        rows = [
            (
                record.request_id,
                record.provider,
                "seed_csv",
                record.request_id,
                record.model,
                record.timestamp_utc,
                record.team,
                record.feature,
                record.user_id,
                record.env,
                record.input_tokens,
                record.output_tokens,
                record.cached_tokens,
                record.latency_ms,
                record.status,
                record.source,
                record.input_cost_usd,
                record.output_cost_usd,
                record.cached_cost_usd,
                record.total_cost_usd,
                record.missing_price,
                record.is_unattributed,
            )
            for record in records
        ]
        self._insert_many(rows)

    def summary(self) -> dict:
        with self.lock:
            total = self.connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(cached_tokens), 0), COALESCE(SUM(output_tokens), 0),
                       COALESCE(SUM(total_cost_usd), 0),
                       COALESCE(SUM(CASE WHEN missing_price THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN central_synced = FALSE THEN 1 ELSE 0 END), 0)
                FROM usage_events
                """
            ).fetchone()
            providers = self.connection.execute(
                "SELECT provider, COUNT(*) FROM usage_events GROUP BY provider"
            ).fetchall()
        return {
            "total_requests": total[0],
            "total_input_tokens": total[1],
            "total_cached_tokens": total[2],
            "total_output_tokens": total[3],
            "total_cost_usd": float(total[4]),
            "providers": dict(providers),
            "missing_price_requests": total[5],
            "central_pending_requests": total[6],
        }

    def breakdown(self, column: str) -> list[dict]:
        allowed = {"provider", "provider_host", "model", "team", "feature", "user_id"}
        if column not in allowed:
            raise ValueError("Unsupported breakdown column")
        with self.lock:
            rows = self.connection.execute(
                f"""
                SELECT {column}, SUM(total_cost_usd), COUNT(*), SUM(input_tokens),
                       SUM(cached_tokens), SUM(output_tokens)
                FROM usage_events GROUP BY {column} ORDER BY SUM(total_cost_usd) DESC
                """
            ).fetchall()
        return [
            {
                column: row[0],
                "total_cost_usd": float(row[1]),
                "request_count": row[2],
                "input_tokens": row[3],
                "cached_tokens": row[4],
                "output_tokens": row[5],
            }
            for row in rows
        ]

    def requests(self, *, provider: str | None, limit: int, offset: int) -> dict:
        with self.lock:
            total = self.connection.execute(
                "SELECT COUNT(*) FROM usage_events WHERE (? IS NULL OR lower(provider) = ?)",
                [provider, provider],
            ).fetchone()[0]
            cursor = self.connection.execute(
                """
                SELECT request_id, provider, provider_host, provider_request_id, model,
                       CAST(timestamp_utc AS VARCHAR) AS timestamp_utc,
                       team, feature, user_id, env, input_tokens, output_tokens,
                       cached_tokens, latency_ms, status, source, input_cost_usd,
                       output_cost_usd, cached_cost_usd, total_cost_usd,
                       missing_price, is_unattributed, central_synced
                FROM usage_events
                WHERE (? IS NULL OR lower(provider) = ?)
                ORDER BY timestamp_utc DESC, request_id
                LIMIT ? OFFSET ?
                """,
                [provider, provider, limit, offset],
            )
            names = [item[0] for item in cursor.description]
            rows = cursor.fetchall()
        items = []
        for row in rows:
            item = dict(zip(names, row))
            for key, value in item.items():
                if key.endswith("_usd"):
                    item[key] = float(value)
            items.append(item)
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    def pending_for_central(self, limit: int = 500) -> list[dict]:
        """Read an ordered page of ledger rows not yet acknowledged by central."""
        with self.lock:
            cursor = self.connection.execute(
                """
                SELECT request_id, provider, provider_host, provider_request_id, model,
                       CAST(timestamp_utc AS VARCHAR) AS timestamp_utc,
                       team, feature, user_id, env, input_tokens, output_tokens,
                       cached_tokens, latency_ms, status, source, input_cost_usd,
                       output_cost_usd, cached_cost_usd, total_cost_usd,
                       missing_price, is_unattributed
                FROM usage_events
                WHERE central_synced = FALSE
                ORDER BY timestamp_utc, request_id
                LIMIT ?
                """,
                [limit],
            )
            names = [item[0] for item in cursor.description]
            rows = cursor.fetchall()
        events = []
        for row in rows:
            event = dict(zip(names, row))
            for key, value in event.items():
                if key.endswith("_usd"):
                    event[key] = str(value)
            events.append(event)
        return events

    def mark_central_synced(self, request_ids: list[str]) -> None:
        """Checkpoint only rows that the receiver has acknowledged."""
        if not request_ids:
            return
        with self.lock:
            self.connection.execute(
                "UPDATE usage_events SET central_synced = TRUE WHERE request_id IN (SELECT UNNEST(?))",
                [request_ids],
            )
