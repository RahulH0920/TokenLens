"""Statistical Anomaly Detection Engine for LLM Workloads.

Identifies cost spikes, runaway agent loops, and prompt-token bloat using
robust statistical methods (Z-scores, moving baselines, and IQR fences).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Dict, Any
import numpy as np
import pandas as pd

from core.models import AnomalyAlert


class AnomalyDetector:
    """Detects statistical spend, token, and velocity anomalies in LLM logs."""

    def __init__(self, z_cost_threshold: float = 3.0, z_token_threshold: float = 3.0):
        self.z_cost_threshold = z_cost_threshold
        self.z_token_threshold = z_token_threshold

    def detect_cost_spikes(self, df: pd.DataFrame) -> List[AnomalyAlert]:
        """Detect individual requests with statistically extreme spend."""
        alerts: List[AnomalyAlert] = []
        if df.empty or len(df) < 5:
            return alerts

        costs = df["total_cost_usd"].astype(float).values
        mean_cost = float(np.mean(costs))
        std_cost = float(np.std(costs))

        if std_cost <= 1e-9:
            return alerts

        for _, row in df.iterrows():
            cost = float(row["total_cost_usd"])
            z = (cost - mean_cost) / std_cost
            if z >= self.z_cost_threshold:
                severity = "CRITICAL" if z >= 4.5 else "WARNING"
                alerts.append(
                    AnomalyAlert(
                        anomaly_id=f"anomaly_cost_{row['request_id']}",
                        timestamp_utc=row["timestamp_utc"] if isinstance(row["timestamp_utc"], datetime) else pd.to_datetime(row["timestamp_utc"]).to_pydatetime(),
                        anomaly_type="COST_SPIKE",
                        severity=severity,
                        metric="total_cost_usd",
                        actual_value=round(cost, 6),
                        expected_baseline=round(mean_cost, 6),
                        z_score=round(float(z), 2),
                        team=str(row.get("team", "unattributed")),
                        user_id=str(row.get("user_id", "unknown_user")),
                        request_id=str(row["request_id"]),
                        description=(
                            f"Single request spend of ${cost:.4f} is {z:.1f} standard deviations "
                            f"above normal baseline (${mean_cost:.4f}) using model {row.get('model', 'unknown')}."
                        ),
                    )
                )

        return sorted(alerts, key=lambda a: a.actual_value, reverse=True)

    def detect_token_bloat(self, df: pd.DataFrame) -> List[AnomalyAlert]:
        """Detect prompt bloat (context window explosion / oversized inputs)."""
        alerts: List[AnomalyAlert] = []
        if df.empty or len(df) < 5:
            return alerts

        in_tokens = df["input_tokens"].astype(float).values
        median_tokens = float(np.median(in_tokens))
        q75, q25 = np.percentile(in_tokens, [75, 25])
        iqr = q75 - q25

        # Upper IQR fence (median + 3 * IQR or mean + 3 * std)
        fence = q75 + (3.0 * iqr) if iqr > 0 else (np.mean(in_tokens) + 3.0 * np.std(in_tokens))

        for _, row in df.iterrows():
            tok = float(row["input_tokens"])
            if tok > fence and tok > (median_tokens * 3.0) and tok > 1000:
                std_val = float(np.std(in_tokens)) or 1.0
                z = (tok - float(np.mean(in_tokens))) / std_val
                severity = "CRITICAL" if z >= 4.0 else "WARNING"
                alerts.append(
                    AnomalyAlert(
                        anomaly_id=f"anomaly_token_{row['request_id']}",
                        timestamp_utc=row["timestamp_utc"] if isinstance(row["timestamp_utc"], datetime) else pd.to_datetime(row["timestamp_utc"]).to_pydatetime(),
                        anomaly_type="TOKEN_BLOAT",
                        severity=severity,
                        metric="input_tokens",
                        actual_value=float(tok),
                        expected_baseline=round(median_tokens, 1),
                        z_score=round(float(z), 2),
                        team=str(row.get("team", "unattributed")),
                        user_id=str(row.get("user_id", "unknown_user")),
                        request_id=str(row["request_id"]),
                        description=(
                            f"Abnormally large prompt context ({int(tok):,} input tokens) detected for feature "
                            f"'{row.get('feature', 'unassigned')}' (median is {int(median_tokens):,} tokens)."
                        ),
                    )
                )

        return sorted(alerts, key=lambda a: a.actual_value, reverse=True)

    def detect_runaway_users(self, df: pd.DataFrame, top_n: int = 5) -> List[AnomalyAlert]:
        """Detect users with excessive volume and cost concentration."""
        alerts: List[AnomalyAlert] = []
        if df.empty:
            return alerts

        user_agg = df.groupby("user_id").agg(
            total_spend=("total_cost_usd", "sum"),
            req_count=("request_id", "count"),
            team=("team", "first"),
        ).reset_index()

        total_spend = float(df["total_cost_usd"].sum())
        if total_spend <= 0:
            return alerts

        for _, row in user_agg.iterrows():
            user_spend = float(row["total_spend"])
            spend_share = user_spend / total_spend
            # If a single user accounts for > 20% of all company spend
            if spend_share >= 0.20 and user_spend > 1.0:
                alerts.append(
                    AnomalyAlert(
                        anomaly_id=f"anomaly_user_{row['user_id']}",
                        timestamp_utc=datetime.now(timezone.utc),
                        anomaly_type="RUNAWAY_RATE",
                        severity="CRITICAL" if spend_share >= 0.35 else "WARNING",
                        metric="user_spend_share",
                        actual_value=round(user_spend, 4),
                        expected_baseline=round(total_spend / max(1, len(user_agg)), 4),
                        z_score=round(float(spend_share * 10), 2),
                        team=str(row["team"]),
                        user_id=str(row["user_id"]),
                        description=(
                            f"User '{row['user_id']}' drives {spend_share*100:.1f}% (${user_spend:.2f}) "
                            f"of total organization spend across {row['req_count']} calls."
                        ),
                    )
                )

        return alerts

    def get_all_anomalies(self, df: pd.DataFrame) -> List[AnomalyAlert]:
        """Aggregate all anomaly types into a unified sorted list."""
        cost_alerts = self.detect_cost_spikes(df)
        token_alerts = self.detect_token_bloat(df)
        user_alerts = self.detect_runaway_users(df)
        all_alerts = cost_alerts + token_alerts + user_alerts
        return sorted(all_alerts, key=lambda a: (a.severity == "CRITICAL", a.actual_value), reverse=True)
