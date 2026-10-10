from datetime import datetime, timedelta, timezone

from core.models import ValidationCheckResult
from core.reconciliation import ReconciliationEngine
from core.validation_report import load_validation_report


def _check(status):
    return ValidationCheckResult(
        metric="sample check",
        category="test",
        expected=1,
        actual=1,
        difference=0,
        tolerance="Exact (0)",
        status=status,
        details="sample",
    )


def _report(path, timestamp, checks=None, spots=None):
    report = ReconciliationEngine().generate_markdown_report(
        checks or [_check("PASS")],
        {
            "dataset_name": "sample_requests.csv",
            "total_source_rows": 2,
            "valid_records": 2,
            "rejected_records": 0,
            "pricing_version": "v-test",
            "audit_time": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        spots if spots is not None else [{"request_id": "r1", "model": "gpt-4o", "expected_cost": "1", "actual_cost": "1"}],
    )
    path.write_text(report, encoding="utf-8")


def test_report_artifact_loads_and_supports_all_statuses(tmp_path):
    dataset = tmp_path / "requests.csv"
    pricing = tmp_path / "pricing.csv"
    report = tmp_path / "validation_report.md"
    dataset.write_text("dataset", encoding="utf-8")
    pricing.write_text("pricing", encoding="utf-8")
    now = datetime.now(timezone.utc)
    for status, expected in (("PASS", "PASS"), ("WARNING", "PASS WITH WARNINGS"), ("FAIL", "FAIL")):
        _report(report, now, checks=[_check(status)])
        artifact = load_validation_report(report, dataset, pricing, now=now)
        assert artifact["state"] == "available"
        assert artifact["status"] == expected
        assert "LLM FinOps Validation Report" in artifact["content"]


def test_missing_report_is_explained_without_fabricated_content(tmp_path):
    artifact = load_validation_report(
        tmp_path / "missing.md", tmp_path / "requests.csv", tmp_path / "pricing.csv"
    )
    assert artifact["state"] == "unavailable"
    assert "unavailable" in artifact["message"].lower()
    assert artifact["content"] is None


def test_stale_report_is_labeled_when_source_changed_after_audit(tmp_path):
    dataset = tmp_path / "requests.csv"
    pricing = tmp_path / "pricing.csv"
    report = tmp_path / "validation_report.md"
    dataset.write_text("dataset", encoding="utf-8")
    pricing.write_text("pricing", encoding="utf-8")
    audit_time = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    _report(report, audit_time)
    source_time = audit_time + timedelta(hours=1)
    import os
    os.utime(dataset, (source_time.timestamp(), source_time.timestamp()))

    artifact = load_validation_report(report, dataset, pricing, now=source_time)
    assert artifact["state"] == "stale"
    assert "dataset changed" in artifact["message"].lower()
    assert "STALE" not in artifact["content"]  # UI adds the same explicit stale banner to display and download.


def test_inconsistent_overall_status_is_hidden(tmp_path):
    dataset = tmp_path / "requests.csv"
    pricing = tmp_path / "pricing.csv"
    report = tmp_path / "validation_report.md"
    dataset.write_text("dataset", encoding="utf-8")
    pricing.write_text("pricing", encoding="utf-8")
    now = datetime.now(timezone.utc)
    _report(report, now, checks=[_check("WARNING")])
    report.write_text(report.read_text(encoding="utf-8").replace("PASS WITH WARNINGS", "PASS", 1), encoding="utf-8")

    artifact = load_validation_report(report, dataset, pricing, now=now)
    assert artifact["state"] == "unavailable"
    assert "does not match" in artifact["message"]
