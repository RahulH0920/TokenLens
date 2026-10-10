"""Independent validation and reconciliation engine for LLM FinOps."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from core.models import ValidationCheckResult


def _decimal(value: Any) -> Decimal:
    """Convert a numeric value without introducing binary-float rounding."""
    if value is None or pd.isna(value):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Expected a numeric reconciliation value, got {value!r}.") from exc


def _money(value: Decimal, places: int = 6) -> str:
    return f"${value:,.{places}f}"


def _signed_money(value: Decimal, places: int = 8) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):,.{places}f}"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _markdown_code(value: Any) -> str:
    return "`" + _markdown_cell(value).replace("`", "\\`") + "`"


class ReconciliationEngine:
    def __init__(self, tolerance_usd: float | Decimal = 0.01, tolerance_percent: float | Decimal = 0.0001):
        self.tolerance_usd = _decimal(tolerance_usd)
        self.tolerance_percent = _decimal(tolerance_percent)

    @staticmethod
    def overall_status(
        checks: List[ValidationCheckResult],
        spot_checks: List[Dict[str, Any]] | None = None,
    ) -> str:
        """Return FAIL, PASS WITH WARNINGS, or PASS, including independent spot checks."""
        if any(check.status == "FAIL" for check in checks):
            return "FAIL"
        if any(check.status == "WARNING" for check in checks):
            return "PASS WITH WARNINGS"
        for spot in spot_checks or []:
            expected = _decimal(spot.get("expected_cost", 0))
            actual = _decimal(spot.get("actual_cost", 0))
            tolerance = _decimal(spot.get("tolerance_usd", "0.000001"))
            if abs(actual - expected) > tolerance:
                return "FAIL"
        if spot_checks is not None and not spot_checks:
            return "PASS WITH WARNINGS"
        return "PASS"

    @staticmethod
    def _group_costs(actual_df: pd.DataFrame, column: str) -> Dict[str, Decimal]:
        grouped: Dict[str, Decimal] = defaultdict(Decimal)
        if actual_df.empty or column not in actual_df.columns or "total_cost_usd" not in actual_df.columns:
            return dict(grouped)
        for group, amount in zip(actual_df[column], actual_df["total_cost_usd"]):
            key = "unknown" if pd.isna(group) else str(group)
            grouped[key] += _decimal(amount)
        return dict(grouped)

    def _financial_status(self, difference: Decimal, expected: Decimal) -> bool:
        relative = abs(difference) / abs(expected) if expected else (Decimal("0") if not difference else Decimal("Infinity"))
        return abs(difference) <= self.tolerance_usd or relative <= self.tolerance_percent

    def run_reconciliation(
        self,
        actual_df: pd.DataFrame,
        expected_manifest: Dict[str, Any],
        ingestion_stats: Dict[str, int],
        rejected_records: List[Dict[str, Any]],
    ) -> List[ValidationCheckResult]:
        """Reconcile ingestion, financial totals, pricing exceptions, and attribution."""
        del rejected_records  # Rejection rows are already counted once in ingestion_stats.
        results: List[ValidationCheckResult] = []

        src_rows = int(ingestion_stats.get("source_rows", 0))
        loaded_rows = int(ingestion_stats.get("loaded_rows", 0))
        rejected_rows = int(ingestion_stats.get("rejected_rows", 0))
        row_total = loaded_rows + rejected_rows
        row_difference = row_total - src_rows
        results.append(ValidationCheckResult(
            metric="Source Row Reconciliation (Source = Loaded + Rejected)",
            category="Ingestion Integrity",
            expected=src_rows,
            actual=row_total,
            difference=row_difference,
            tolerance="Exact (0)",
            status="PASS" if row_difference == 0 else "FAIL",
            details=(
                f"Loaded {loaded_rows} rows and rejected {rejected_rows} rows. Duplicate rows are included once "
                "in the rejected-row count; duplicate counts are a subset and are not added again."
            ),
        ))

        duplicate_count = int(ingestion_stats.get("duplicate_count", 0))
        results.append(ValidationCheckResult(
            metric="Duplicate Request IDs",
            category="Ingestion Integrity",
            expected=0,
            actual=duplicate_count,
            difference=duplicate_count,
            tolerance="0 duplicate occurrences allowed",
            status="WARNING" if duplicate_count else "PASS",
            details=(
                f"Detected {duplicate_count} duplicate occurrence(s). The first row for each request ID is retained; "
                "subsequent duplicate rows are rejected and included once in the rejected-row count."
            ),
        ))

        request_count = len(actual_df)
        expected_requests = expected_manifest.get("total_requests")
        if expected_requests is not None:
            difference = request_count - int(expected_requests)
            results.append(ValidationCheckResult(
                metric="Total Valid Requests",
                category="Dataset Totals",
                expected=int(expected_requests),
                actual=request_count,
                difference=difference,
                tolerance="Exact (0)",
                status="PASS" if difference == 0 else "FAIL",
                details="Counts only accepted, unique, valid request rows.",
            ))

        for column, manifest_key, label in (
            ("input_tokens", "total_input_tokens", "Total Input Tokens"),
            ("output_tokens", "total_output_tokens", "Total Output Tokens"),
        ):
            expected = expected_manifest.get(manifest_key)
            if expected is None:
                continue
            actual = int(actual_df[column].sum()) if not actual_df.empty and column in actual_df else 0
            difference = actual - int(expected)
            results.append(ValidationCheckResult(
                metric=label,
                category="Token Volume",
                expected=f"{int(expected):,}",
                actual=f"{actual:,}",
                difference=f"{difference:+,}",
                tolerance="Exact (0)",
                status="PASS" if difference == 0 else "FAIL",
                details=f"Aggregated across {request_count:,} accepted request rows.",
            ))

        # Each request cost is quantized by CostEngine. Convert each displayed value back to
        # Decimal before aggregating; never round aggregate totals before comparing them.
        row_costs = [_decimal(value) for value in actual_df["total_cost_usd"]] if not actual_df.empty and "total_cost_usd" in actual_df else []
        grand_total = sum(row_costs, Decimal("0"))
        team_totals = self._group_costs(actual_df, "team")
        model_totals = self._group_costs(actual_df, "model")
        expected_total_value = expected_manifest.get("total_cost_usd")
        if expected_total_value is not None:
            expected_total = _decimal(expected_total_value)
            difference = grand_total - expected_total
            within_tolerance = self._financial_status(difference, expected_total)
            percent_tolerance = self.tolerance_percent * Decimal("100")
            results.append(ValidationCheckResult(
                metric="Grand Total Spend (USD)",
                category="Financial Reconciliation",
                expected=_money(expected_total),
                actual=_money(grand_total),
                difference=_signed_money(difference),
                tolerance=f"±${self.tolerance_usd:,.6f} or ±{percent_tolerance:.4f}%",
                status="PASS" if within_tolerance else "FAIL",
                details=(
                    f"Compared the unrounded Decimal difference of {_signed_money(difference)}. PASS is allowed when "
                    f"the absolute difference is at most ${self.tolerance_usd:,.6f} or the relative difference "
                    f"is at most {percent_tolerance:.4f}%."
                ),
            ))

        expected_team_totals = expected_manifest.get("team_costs")
        if expected_team_totals is not None:
            expected_team_totals = expected_team_totals or {}
        for team in sorted(set(expected_team_totals or {})):
            expected = _decimal(expected_team_totals.get(team, 0))
            actual = team_totals.get(str(team), Decimal("0"))
            difference = actual - expected
            results.append(ValidationCheckResult(
                metric=f"Team Spend — {team}",
                category="Team Financial Reconciliation",
                expected=_money(expected),
                actual=_money(actual),
                difference=_signed_money(difference),
                tolerance=f"±${self.tolerance_usd:,.6f} or ±{self.tolerance_percent * Decimal('100'):.4f}%",
                status="PASS" if self._financial_status(difference, expected) else "FAIL",
                details="Compared this team's unrounded Decimal total with the golden manifest.",
            ))

        team_total = sum(team_totals.values(), Decimal("0"))
        model_total = sum(model_totals.values(), Decimal("0"))
        team_difference = team_total - grand_total
        model_difference = model_total - grand_total
        invariant_difference = max(abs(team_difference), abs(model_difference))
        results.append(ValidationCheckResult(
            metric="Cross-Cut Invariant (Team Sum = Model Sum = Grand Total)",
            category="Financial Integrity",
            expected=_money(grand_total),
            actual=f"Team: {_money(team_total)} | Model: {_money(model_total)}",
            difference=f"${invariant_difference:,.8f}",
            tolerance="Exact Decimal equality (0)",
            status="PASS" if team_difference == 0 and model_difference == 0 else "FAIL",
            details="Team, model, and grand totals were summed from unrounded per-request Decimal costs.",
        ))

        missing_count = int(actual_df["missing_price"].sum()) if not actual_df.empty and "missing_price" in actual_df else 0
        expected_missing = int(expected_manifest.get("expected_missing_price_records", 0))
        missing_status = "FAIL" if missing_count != expected_missing else ("WARNING" if missing_count else "PASS")
        results.append(ValidationCheckResult(
            metric="Missing Pricing Exceptions",
            category="Exception Auditing",
            expected=expected_missing,
            actual=missing_count,
            difference=missing_count - expected_missing,
            tolerance="Exact expected count; any unpriced row remains an exception",
            status=missing_status,
            details=(
                f"Found {missing_count} request(s) without an applicable price; {expected_missing} were expected. "
                "These rows are explicitly flagged and are not valid zero-cost results."
            ),
        ))

        unattributed = actual_df[actual_df["is_unattributed"]] if not actual_df.empty and "is_unattributed" in actual_df else actual_df.iloc[0:0]
        unattributed_count = len(unattributed)
        unattributed_cost = sum((_decimal(value) for value in unattributed["total_cost_usd"]), Decimal("0")) if unattributed_count and "total_cost_usd" in unattributed else Decimal("0")
        unattributed_status = "WARNING" if unattributed_count else "PASS"
        results.append(ValidationCheckResult(
            metric="Unattributed Requests and Spend",
            category="Attribution Integrity",
            expected="0 unattributed requests",
            actual=f"{unattributed_count:,} request(s), {_money(unattributed_cost)}",
            difference=f"{unattributed_count:,} request(s)",
            tolerance="All requests attributed or explicitly reviewed",
            status=unattributed_status,
            details="Unattributed requests remain visible and are included in the team/grand spend totals.",
        ))

        return results

    def generate_markdown_report(
        self,
        checks: List[ValidationCheckResult],
        scope_info: Dict[str, Any],
        spot_checks: List[Dict[str, Any]],
    ) -> str:
        """Generate the validation artifact consumed by the dashboard and report download."""
        status = self.overall_status(checks, spot_checks)
        pass_count = sum(1 for check in checks if check.status == "PASS")
        fail_count = sum(1 for check in checks if check.status == "FAIL")
        warning_count = sum(1 for check in checks if check.status == "WARNING")
        spot_failures = 0

        lines = [
            "# LLM FinOps Validation Report: Comparing Dashboard Totals with Known Expected Values",
            f"**Overall Status:** `{status}`",
            "",
            "## 1. Scope & Audit Metadata",
            f"- **Dataset File:** {_markdown_code(Path(str(scope_info.get('dataset_name', 'unknown dataset'))).name)}",
            f"- **Source Records:** {int(scope_info.get('total_source_rows', 0)):,}",
            f"- **Valid Processed Records:** {int(scope_info.get('valid_records', 0)):,}",
            f"- **Rejected Records (includes duplicate rows):** {int(scope_info.get('rejected_records', 0)):,}",
            f"- **Dataset SHA-256 Prefix:** {_markdown_code(scope_info.get('dataset_sha256', 'unavailable'))}",
            f"- **Pricing Table Version:** {_markdown_code(scope_info.get('pricing_version', 'unversioned'))}",
            f"- **Pricing File SHA-256 Prefix:** {_markdown_code(scope_info.get('pricing_sha256', 'unavailable'))}",
            f"- **Audit Timestamp (UTC):** {_markdown_code(scope_info.get('audit_time', 'unavailable'))}",
            "",
            "## 2. Validation Results Matrix (Expected vs Actual Dashboard Totals)",
            "All financial comparisons use unrounded Decimal amounts. Display values are rounded to six decimal places.",
            "",
            "| Check | Category | Expected | Actual | Difference | Tolerance | Status |",
            "| :--- | :--- | ---: | ---: | ---: | :--- | :---: |",
        ]
        for check in checks:
            lines.append(
                f"| {_markdown_cell(check.metric)} | {_markdown_cell(check.category)} | {_markdown_cell(check.expected)} | "
                f"{_markdown_cell(check.actual)} | {_markdown_cell(check.difference)} | {_markdown_cell(check.tolerance)} | {check.status} |"
            )

        expected_team_totals = scope_info.get("expected_team_totals") or {}
        team_totals = scope_info.get("team_totals") or {}
        lines.extend([
            "",
            "## 3. Team Spend Totals",
            "Expected values come from the golden validation manifest; actual values are unrounded Decimal sums.",
            "",
            "| Team | Expected | Actual | Difference |",
            "| :--- | ---: | ---: | ---: |",
        ])
        for team in sorted(set(expected_team_totals) | set(team_totals)):
            expected = _decimal(expected_team_totals.get(team, 0))
            actual = _decimal(team_totals.get(team, 0))
            lines.append(f"| {_markdown_cell(team)} | {_money(expected)} | {_money(actual)} | {_signed_money(actual - expected)} |")
        if not expected_team_totals and not team_totals:
            lines.append("| No team totals supplied | — | — | — |")

        model_totals = scope_info.get("model_totals") or {}
        lines.extend([
            "",
            "## 4. Model Spend Totals",
            "The manifest does not define independent per-model expected totals; these are actual Decimal aggregates and are reconciled against the grand total above.",
            "",
            "| Model | Actual |",
            "| :--- | ---: |",
        ])
        for model, amount in sorted(model_totals.items()):
            lines.append(f"| {_markdown_cell(model)} | {_money(_decimal(amount))} |")
        if not model_totals:
            lines.append("| No model totals supplied | — |")

        duplicate_ids = sorted({str(value) for value in scope_info.get("duplicate_ids", []) if str(value)})
        missing_exceptions = scope_info.get("missing_pricing_exceptions") or []
        unattributed_requests = scope_info.get("unattributed_requests") or []
        lines.extend([
            "",
            "## 5. Exceptions and Warnings",
            "- **Duplicate handling:** duplicate IDs are detected; the first occurrence is retained and later rows are rejected. Those rejected rows are already included in source-row reconciliation, so duplicates are not double-counted.",
            f"- **Duplicate IDs:** {', '.join(_markdown_code(value) for value in duplicate_ids) if duplicate_ids else 'None'}",
            "- **Missing pricing:** flagged requests are not treated as valid zero-cost results.",
        ])
        if missing_exceptions:
            for item in missing_exceptions:
                lines.append(f"  - {_markdown_code(item.get('model', 'unknown model'))}: {int(item.get('count', 0))} request(s)")
                ids = sorted({str(value) for value in item.get("request_ids", []) if str(value)})
                if ids:
                    lines.append(f"    - Request IDs: {', '.join(_markdown_code(value) for value in ids)}")
        else:
            lines.append("  - None")
        lines.append(f"- **Unattributed requests:** {len(unattributed_requests):,}")
        if unattributed_requests:
            ids = sorted({str(item.get("request_id", "")) for item in unattributed_requests if item.get("request_id")})
            if ids:
                lines.append(f"  - Request IDs: {', '.join(_markdown_code(value) for value in ids)}")

        lines.extend([
            "",
            "## 6. Independent Spot Checks",
            "Expected request costs are independently recalculated from token counts and the applicable pricing record.",
            "",
            "| Request ID | Model | Input Tokens | Output Tokens | Input/Output Rate per 1M | Expected Cost | Actual Cost | Difference | Status |",
            "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
        ])
        for spot in spot_checks:
            expected = _decimal(spot.get("expected_cost", 0))
            actual = _decimal(spot.get("actual_cost", 0))
            difference = actual - expected
            tolerance = _decimal(spot.get("tolerance_usd", "0.000001"))
            spot_status = "PASS" if abs(difference) <= tolerance else "FAIL"
            if spot_status == "FAIL":
                spot_failures += 1
            lines.append(
                f"| {_markdown_code(spot.get('request_id', ''))} | {_markdown_code(spot.get('model', ''))} | "
                f"{int(spot.get('input_tokens', 0)):,} | {int(spot.get('output_tokens', 0)):,} | "
                f"${_decimal(spot.get('in_rate', 0)):,.6f} / ${_decimal(spot.get('out_rate', 0)):,.6f} | "
                f"{_money(expected)} | {_money(actual)} | {_signed_money(difference)} | {spot_status} |"
            )
        if not spot_checks:
            lines.append("| No spot checks available | — | — | — | — | — | — | — | WARNING |")
            warning_count += 1

        lines.extend([
            "",
            "## 7. Audit Conclusion",
            f"- Passed checks: {pass_count}",
            f"- Warnings: {warning_count}",
            f"- Failed checks: {fail_count + spot_failures}",
            "",
            (
                "**FAIL:** one or more reconciliation or spot-check failures require investigation."
                if status == "FAIL"
                else "**PASS WITH WARNINGS:** totals reconcile within configured tolerances, but visible exceptions remain."
                if status == "PASS WITH WARNINGS"
                else "**PASS:** all reconciliation checks and available spot checks passed with no warnings."
            ),
            "",
            "Financial tolerance: pass when the absolute difference is within the configured USD tolerance or the relative difference is within the configured percentage tolerance. Cross-cut team/model/grand totals require exact Decimal equality.",
            "",
            "---",
            "*Generated by TokenLens' existing CostEngine and ReconciliationEngine.*",
        ])
        return "\n".join(lines)
