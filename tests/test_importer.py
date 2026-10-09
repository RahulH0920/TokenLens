"""Unit tests for dataset ingestion, duplicate detection, and rejection accounting."""

import tempfile
from pathlib import Path
import pandas as pd
import pytest

from core.importer import DataImporter


def test_duplicate_requests_quarantined():
    """Verify that duplicate request IDs are flagged and quarantined from valid records."""
    importer = DataImporter()

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        csv_path = Path(f.name)
        f.write("request_id,timestamp,team,feature,user,model,provider,input_tokens,output_tokens,cached_tokens\n")
        f.write("req_001,2026-10-01T10:00:00Z,engineering,code-review,user_1,gpt-4o,openai,1000,500,0\n")
        f.write("req_002,2026-10-01T11:00:00Z,support,agent-chat,user_2,gpt-4o,openai,2000,1000,0\n")
        f.write("req_001,2026-10-01T12:00:00Z,engineering,code-review,user_1,gpt-4o,openai,1000,500,0\n")  # DUPLICATE
        f.write("req_003,2026-10-01T13:00:00Z,product,doc-search,user_3,gpt-4o,openai,1500,750,0\n")
        f.write("req_002,2026-10-01T14:00:00Z,support,agent-chat,user_2,gpt-4o,openai,2000,1000,0\n")  # DUPLICATE

    try:
        valid_records, rejects, stats = importer.load_requests(csv_path)

        assert stats["source_rows"] == 5
        assert stats["loaded_rows"] == 3
        assert stats["rejected_rows"] == 2
        assert stats["duplicate_count"] == 2
        assert stats["source_rows"] == stats["loaded_rows"] + stats["rejected_rows"]

        valid_ids = [r.request_id for r in valid_records]
        assert valid_ids == ["req_001", "req_002", "req_003"]
        assert len(valid_ids) == len(set(valid_ids))
    finally:
        csv_path.unlink(missing_ok=True)


def test_malformed_and_negative_tokens_rejected():
    """Verify that negative token counts and blank IDs are rejected."""
    importer = DataImporter()

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        csv_path = Path(f.name)
        f.write("request_id,timestamp,team,feature,user,model,provider,input_tokens,output_tokens,cached_tokens\n")
        f.write("req_ok,2026-10-01T10:00:00Z,engineering,code-review,user_1,gpt-4o,openai,1000,500,0\n")
        f.write("req_neg,2026-10-01T11:00:00Z,engineering,code-review,user_1,gpt-4o,openai,-500,200,0\n")
        f.write(",2026-10-01T12:00:00Z,engineering,code-review,user_1,gpt-4o,openai,1000,500,0\n")

    try:
        valid_records, rejects, stats = importer.load_requests(csv_path)
        assert stats["source_rows"] == 3
        assert stats["loaded_rows"] == 1
        assert stats["rejected_rows"] == 2
        assert valid_records[0].request_id == "req_ok"
    finally:
        csv_path.unlink(missing_ok=True)
