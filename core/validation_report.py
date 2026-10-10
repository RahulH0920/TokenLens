"""Safe freshness and integrity checks for the generated Markdown validation artifact."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any


_AUDIT_TIME = re.compile(r"(?m)^- \*\*Audit Timestamp \(UTC\):\*\* `([^`]+)`\s*$")
_OVERALL_STATUS = re.compile(r"(?m)^\*\*Overall Status:\*\* `([^`]+)`\s*$")
_STATUS_CELL = re.compile(r"\|\s*(PASS|WARNING|FAIL)\s*\|\s*$")
_ALLOWED_STATUS = {"PASS", "PASS WITH WARNINGS", "FAIL"}
_MAX_REPORT_BYTES = 2 * 1024 * 1024


def _expected_status(body: str) -> str | None:
    try:
        reconciliation = body.split("## 2.", 1)[1].split("## 3.", 1)[0]
        spot_checks = body.split("## 6. Independent Spot Checks", 1)[1].split("## 7.", 1)[0]
    except IndexError:
        return None

    reconciliation_statuses = [match.group(1) for line in reconciliation.splitlines() if (match := _STATUS_CELL.search(line))]
    spot_statuses = [match.group(1) for line in spot_checks.splitlines() if (match := _STATUS_CELL.search(line))]
    if not reconciliation_statuses or not spot_statuses:
        return None
    if "FAIL" in reconciliation_statuses or "FAIL" in spot_statuses:
        return "FAIL"
    if "WARNING" in reconciliation_statuses or "WARNING" in spot_statuses:
        return "PASS WITH WARNINGS"
    return "PASS"


def load_validation_report(
    report_path: Path | str,
    dataset_path: Path | str,
    pricing_path: Path | str,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> dict[str, Any]:
    """Load a generated report and label stale/corrupt artifacts without inventing data."""
    report_file = Path(report_path)
    unavailable = {
        "state": "unavailable",
        "status": None,
        "audit_time": None,
        "filename_date": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
        "content": None,
        "message": "The validation report is unavailable or unreadable. Run the independent validation command to generate it.",
    }
    try:
        stat = report_file.stat()
        if stat.st_size <= 0 or stat.st_size > _MAX_REPORT_BYTES:
            return unavailable
        body = report_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return unavailable

    time_match = _AUDIT_TIME.search(body)
    status_match = _OVERALL_STATUS.search(body)
    if not time_match or not status_match:
        return unavailable
    try:
        audit_time = datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
        if audit_time.tzinfo is None:
            return unavailable
        audit_time = audit_time.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return unavailable

    declared_status = status_match.group(1).strip()
    calculated_status = _expected_status(body)
    if declared_status not in _ALLOWED_STATUS or calculated_status is None or declared_status != calculated_status:
        return {
            **unavailable,
            "message": "The report status does not match its individual checks. It is hidden until validation is rerun.",
        }

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    stale_reasons: list[str] = []
    if audit_time > current_time + timedelta(minutes=5):
        return {
            **unavailable,
            "message": "The report timestamp is in the future. It is hidden until validation is rerun.",
        }
    if current_time - audit_time > max_age:
        stale_reasons.append(f"The audit is more than {int(max_age.total_seconds() // 3600)} hours old.")

    for source_path, label in ((Path(dataset_path), "dataset"), (Path(pricing_path), "pricing table")):
        try:
            source_time = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            stale_reasons.append(f"The current {label} file could not be checked.")
            continue
        if source_time > audit_time + timedelta(seconds=1):
            stale_reasons.append(f"The {label} changed after this audit.")

    message = " ".join(dict.fromkeys(stale_reasons))
    return {
        "state": "stale" if stale_reasons else "available",
        "status": declared_status,
        "audit_time": audit_time,
        "filename_date": audit_time.strftime("%Y-%m-%d"),
        "content": body,
        "message": message,
    }
