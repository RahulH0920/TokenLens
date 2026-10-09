"""Dataset ingestion, sanitization, reject routing, and pricing loader."""

from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd

from core.models import RequestRecord, PricingRecord
from core.attribution import AttributionParser
from core.provider_adapters import ProviderAdapterRegistry


class DataImporter:
    def __init__(self, attribution_parser: Optional[AttributionParser] = None):
        self.attribution = attribution_parser or AttributionParser()
        self.provider_adapters = ProviderAdapterRegistry(self.attribution)
        self.rejected_records: List[Dict[str, Any]] = []
        self.duplicate_records: List[Dict[str, Any]] = []

    def parse_provider_payload(
        self,
        payload: Dict[str, Any],
        format_hint: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> Tuple[RequestRecord, str]:
        """Normalize raw provider payload (OpenAI, Anthropic, Google, Simulated) into RequestRecord."""
        return self.provider_adapters.parse_payload(payload, format_hint=format_hint, headers=headers)

    def load_pricing(self, csv_path: Path) -> List[PricingRecord]:
        """Load model pricing CSV file into validated PricingRecords."""
        if not csv_path.exists():
            raise FileNotFoundError(f"Pricing file not found at: {csv_path}")

        df = pd.read_csv(csv_path)
        records: List[PricingRecord] = []

        for idx, row in df.iterrows():
            model = str(row["model"]).strip().lower()
            provider = str(row.get("provider", "openai")).strip().lower()
            input_rate = Decimal(str(row["input_usd_per_1m"]))
            output_rate = Decimal(str(row["output_usd_per_1m"]))
            cached_rate = Decimal(str(row.get("cached_usd_per_1m", "0.0")))

            raw_from = str(row.get("effective_from", "2024-01-01")).strip()
            eff_from = datetime.strptime(raw_from, "%Y-%m-%d").date()

            eff_to = None
            if "effective_to" in row and pd.notna(row["effective_to"]) and str(row["effective_to"]).strip():
                eff_to = datetime.strptime(str(row["effective_to"]).strip(), "%Y-%m-%d").date()

            records.append(PricingRecord(
                model=model,
                provider=provider,
                input_usd_per_1m=input_rate,
                output_usd_per_1m=output_rate,
                cached_usd_per_1m=cached_rate,
                effective_from=eff_from,
                effective_to=eff_to
            ))

        return records

    def load_requests(self, csv_path: Path) -> Tuple[List[RequestRecord], List[Dict[str, Any]], Dict[str, int]]:
        """Load and sanitize requests CSV, rejecting malformed rows and deduplicating IDs."""
        self.rejected_records.clear()
        self.duplicate_records.clear()

        if not csv_path.exists():
            raise FileNotFoundError(f"Requests file not found at: {csv_path}")

        df = pd.read_csv(csv_path)
        source_row_count = len(df)

        valid_records: List[RequestRecord] = []
        seen_request_ids = set()

        for idx, row in df.iterrows():
            raw_id = row.get("request_id")
            if pd.isna(raw_id) or not str(raw_id).strip():
                self.rejected_records.append({
                    "row_index": idx,
                    "raw_data": row.to_dict(),
                    "reason": "Missing or blank request_id"
                })
                continue

            req_id = str(raw_id).strip()

            # Duplicate request detection
            if req_id in seen_request_ids:
                self.duplicate_records.append({
                    "row_index": idx,
                    "request_id": req_id,
                    "reason": "Duplicate request_id detected"
                })
                self.rejected_records.append({
                    "row_index": idx,
                    "raw_data": row.to_dict(),
                    "reason": f"Duplicate request_id: {req_id}"
                })
                continue

            # Validate timestamp
            raw_ts = row.get("timestamp") or row.get("timestamp_utc")
            try:
                if pd.isna(raw_ts):
                    raise ValueError("Null timestamp")
                # Parse ISO or standard string
                ts = pd.to_datetime(raw_ts).to_pydatetime()
            except Exception as e:
                self.rejected_records.append({
                    "row_index": idx,
                    "raw_data": row.to_dict(),
                    "reason": f"Invalid timestamp format: {raw_ts} ({e})"
                })
                continue

            # Validate token counts (reject negative tokens)
            try:
                in_tok = int(row.get("input_tokens", 0))
                out_tok = int(row.get("output_tokens", 0))
                cached_tok = int(row.get("cached_tokens", 0) if pd.notna(row.get("cached_tokens")) else 0)

                if in_tok < 0 or out_tok < 0 or cached_tok < 0:
                    raise ValueError("Negative token count")
            except Exception as e:
                self.rejected_records.append({
                    "row_index": idx,
                    "raw_data": row.to_dict(),
                    "reason": f"Invalid token count: {e}"
                })
                continue

            # Model validation
            raw_model = row.get("model")
            if pd.isna(raw_model) or not str(raw_model).strip():
                self.rejected_records.append({
                    "row_index": idx,
                    "raw_data": row.to_dict(),
                    "reason": "Missing model identifier"
                })
                continue
            model = str(raw_model).strip().lower()

            # Attribution normalization
            attr = self.attribution.parse_attribution(row.to_dict())

            provider = str(row.get("provider", "openai")).strip().lower()
            status = str(row.get("status", "success")).strip().lower()
            latency = int(row.get("latency_ms", 0)) if pd.notna(row.get("latency_ms")) else 0
            env = attr.get("env", "production")
            source = str(row.get("source", "import")).strip()

            valid_record = RequestRecord(
                request_id=req_id,
                timestamp_utc=ts,
                team=attr["team"],
                feature=attr["feature"],
                user_id=attr["user_id"],
                provider=provider,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cached_tokens=cached_tok,
                status=status,
                latency_ms=latency,
                env=env,
                source=source
            )

            seen_request_ids.add(req_id)
            valid_records.append(valid_record)

        stats = {
            "source_rows": source_row_count,
            "loaded_rows": len(valid_records),
            "rejected_rows": len(self.rejected_records),
            "duplicate_count": len(self.duplicate_records)
        }

        return valid_records, self.rejected_records, stats
