"""FinOps Budget Guardrails and Policy Enforcement Engine.

Provides proactive quota management, threshold alerts, and request-level
enforcement to protect engineering organizations from runaway LLM bills.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Any
import pandas as pd

from core.models import BudgetPolicy, GuardrailEvaluationResult


class GuardrailEngine:
    """Evaluates spend against per-team financial budgets and enforces policies."""

    def __init__(self, policies: List[BudgetPolicy] | None = None):
        if policies:
            self.policies = {p.team.lower(): p for p in policies}
        else:
            self.policies = {
                "engineering": BudgetPolicy(
                    team="engineering",
                    monthly_budget_usd=Decimal("15.00"),
                    warning_threshold_pct=80.0,
                    critical_threshold_pct=100.0,
                    enforcement_action="WARN",
                ),
                "product": BudgetPolicy(
                    team="product",
                    monthly_budget_usd=Decimal("10.00"),
                    warning_threshold_pct=80.0,
                    critical_threshold_pct=100.0,
                    enforcement_action="WARN",
                ),
                "support": BudgetPolicy(
                    team="support",
                    monthly_budget_usd=Decimal("5.00"),
                    warning_threshold_pct=80.0,
                    critical_threshold_pct=100.0,
                    enforcement_action="WARN",
                ),
                "unattributed": BudgetPolicy(
                    team="unattributed",
                    monthly_budget_usd=Decimal("2.00"),
                    warning_threshold_pct=50.0,
                    critical_threshold_pct=100.0,
                    enforcement_action="BLOCK",
                ),
            }

    def set_team_policy(self, policy: BudgetPolicy) -> None:
        """Register or update a team's financial budget policy."""
        self.policies[policy.team.lower()] = policy

    def evaluate_request(
        self, team: str, estimated_cost: Decimal, current_team_spend: Decimal
    ) -> GuardrailEvaluationResult:
        """Evaluate if an incoming LLM request is permitted under team guardrails."""
        clean_team = team.strip().lower()
        policy = self.policies.get(
            clean_team,
            BudgetPolicy(
                team=clean_team,
                monthly_budget_usd=Decimal("10.00"),
                warning_threshold_pct=80.0,
                critical_threshold_pct=100.0,
                enforcement_action="WARN",
            ),
        )

        projected = current_team_spend + estimated_cost
        limit = policy.monthly_budget_usd
        utilization_pct = float((projected / limit) * 100) if limit > 0 else 100.0

        if utilization_pct >= policy.critical_threshold_pct:
            if policy.enforcement_action == "BLOCK":
                return GuardrailEvaluationResult(
                    allowed=False,
                    action="BLOCK",
                    team=clean_team,
                    current_spend_usd=current_team_spend,
                    budget_limit_usd=limit,
                    utilization_pct=round(utilization_pct, 2),
                    message=(
                        f"BUDGET BREACH: Team '{clean_team}' exceeded monthly budget of "
                        f"${limit:.2f} (current + estimated = ${projected:.4f}). Request blocked."
                    ),
                )
            else:
                return GuardrailEvaluationResult(
                    allowed=True,
                    action="WARN",
                    team=clean_team,
                    current_spend_usd=current_team_spend,
                    budget_limit_usd=limit,
                    utilization_pct=round(utilization_pct, 2),
                    message=(
                        f"BUDGET CRITICAL: Team '{clean_team}' at {utilization_pct:.1f}% "
                        f"of monthly budget (${projected:.4f} / ${limit:.2f})."
                    ),
                )

        if utilization_pct >= policy.warning_threshold_pct:
            return GuardrailEvaluationResult(
                allowed=True,
                action="WARN",
                team=clean_team,
                current_spend_usd=current_team_spend,
                budget_limit_usd=limit,
                utilization_pct=round(utilization_pct, 2),
                message=(
                    f"BUDGET WARNING: Team '{clean_team}' approaching limit "
                    f"({utilization_pct:.1f}% utilized)."
                ),
            )

        return GuardrailEvaluationResult(
            allowed=True,
            action="ALLOW",
            team=clean_team,
            current_spend_usd=current_team_spend,
            budget_limit_usd=limit,
            utilization_pct=round(utilization_pct, 2),
            message=f"Within budget limits ({utilization_pct:.1f}% utilized).",
        )

    def get_summary_status(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Compute live budget status, utilization percentages, and health state."""
        team_spend = (
            df.groupby("team")["total_cost_usd"]
            .sum()
            .to_dict()
            if not df.empty and "team" in df.columns
            else {}
        )

        results = []
        all_teams = set(list(self.policies.keys()) + [str(t).lower() for t in team_spend.keys()])
        for team in sorted(all_teams):
            policy = self.policies.get(
                team,
                BudgetPolicy(
                    team=team,
                    monthly_budget_usd=Decimal("10.00"),
                    warning_threshold_pct=80.0,
                    critical_threshold_pct=100.0,
                    enforcement_action="WARN",
                ),
            )
            spend = Decimal(str(team_spend.get(team, 0.0)))
            limit = policy.monthly_budget_usd
            pct = float((spend / limit) * 100) if limit > Decimal("0") else 0.0

            if pct >= policy.critical_threshold_pct:
                state = "CRITICAL"
            elif pct >= policy.warning_threshold_pct:
                state = "WARNING"
            else:
                state = "HEALTHY"

            results.append({
                "team": team,
                "current_spend_usd": float(spend),
                "monthly_budget_usd": float(limit),
                "utilization_pct": round(pct, 1),
                "state": state,
                "enforcement_action": policy.enforcement_action,
            })

        return results
